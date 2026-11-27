"""The MCP server: the authoring tools over the wire, as an agent's client sees them."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any

from mcp import Client
from mcp_types import TextContent

from bond import Bond, CitationKind, Ref, VersionLocator, details
from bond.authoring import Tools
from bond.mcp.server import create_server
from bond.storage.local import LocalBlobs
from bond.workspaces import Fixed, Workspaces
from conftest import written

FINDING = "No significant damage to other rooms."
CASE = "case-001"  # the name the server serves the bond fixture under


def serve(workspaces: Workspaces, steps: Callable[[Client], Awaitable[Any]]) -> Any:
    """Run steps against a server over these workspaces through a client connected in-process."""

    async def run() -> Any:
        async with Client(create_server(workspaces)) as client:
            return await steps(client)

    return asyncio.run(run())


def session(
    bond: Bond, steps: Callable[[Client], Awaitable[Any]], sources: Mapping[str, Bond] = MappingProxyType({}),
) -> Any:
    """Run steps against a server over one workspace, served as CASE, linked to sources."""
    bond.set_links(list(sources.values()))
    return serve(Fixed({CASE: bond}), steps)


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    """A tool's structured result; fails if the call was an error."""
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    content = result.structured_content
    assert content is not None
    return content.get("result", content)  # a list comes back wrapped


async def error(client: Client, tool: str, **arguments: Any) -> str:
    """The message of a call that fails."""
    result = await client.call_tool(tool, arguments)
    assert result.is_error
    [message] = result.content
    assert isinstance(message, TextContent)
    return message.text


async def offered(client: Client) -> tuple[str | None, list[Any]]:
    """The server's instructions and the tools it offers."""
    return client.instructions, (await client.list_tools()).tools


def test_the_server_offers_the_authoring_tools_and_says_how_to_cite(bond):
    instructions, tools = session(bond, offered)
    assert instructions is not None
    assert sorted(t.name for t in tools) == ["documents", "draft", "edit", "read", "search", "trace", "write"]
    assert "<bond:cite" in instructions and "pass it on every call" in instructions
    assert "PDF" in instructions and "transcription" in instructions
    assert instructions.endswith(f"Workspaces you can work in:\n- {CASE!r}\n")
    by_name = {t.name: t for t in tools}
    # Every tool names the workspace it works in, first and required: the server remembers none.
    assert all(next(iter(t.input_schema["properties"])) == "workspace" for t in tools)
    assert all("workspace" in t.input_schema["required"] for t in tools)
    assert set(by_name["search"].input_schema["properties"]) == {"workspace", "phrases", "document", "within", "limit",
                                                                 "from_workspace"}
    assert list(by_name["read"].input_schema["properties"]) == ["workspace", "document", "from_workspace"]
    assert all(by_name[n].annotations.read_only_hint for n in ("documents", "read", "search", "trace", "draft"))
    assert not by_name["write"].annotations.read_only_hint and not by_name["write"].annotations.destructive_hint


def test_the_instructions_list_each_workspace_and_its_links_or_that_there_are_none_yet(bond, tmp_path):
    with Bond.local(tmp_path / "policies") as policies:
        instructions, _ = session(bond, offered, {"policies": policies})
    assert instructions is not None and instructions.endswith(
        f"Workspaces you can work in:\n- {CASE!r}, links to: 'policies'\n")

    async def steps(client: Client):
        return client.instructions, await error(client, "documents", workspace=CASE)

    instructions, refused = serve(Fixed({}), steps)
    assert instructions is not None and "No workspaces are available to you yet." in instructions
    assert f"{CASE!r} isn't a workspace you can work in (you can in: none yet)" in refused


def test_an_agent_finds_evidence_writes_reads_and_edits(bond, case):
    async def steps(client: Client):
        listed = await call(client, "documents", workspace=CASE)
        hits = await call(client, "search", workspace=CASE, phrases=[FINDING], document="fax_case_001.reducto")
        [hit] = [h for h in hits if h["support"] == "supported"]  # not the parse's unsourced copies of its text
        quote = f'<bond:cite kind="quote" from="{hit["handle"]}">{FINDING}</bond:cite>'
        refused = await call(client, "write", workspace=CASE, document="note", draft=f"{quote} Made up.")
        written = await call(client, "write", workspace=CASE, document="note", draft=quote)
        why = await call(client, "trace", workspace=CASE, handle=hit["handle"])
        reading = await call(client, "draft", workspace=CASE, document="note")
        edited = await call(client, "edit", workspace=CASE, document="note", base=reading["base"], edits=[
            {"old": "</bond:cite>", "new": '</bond:cite> <bond:cite kind="unsourced">Proceed.</bond:cite>'},
        ])
        return listed, hits, hit, refused, written, why, reading, edited

    listed, hits, hit, refused, written, why, reading, edited = session(bond, steps)
    assert {"document": "fax_case_001.reducto", "format": "json"} in listed
    assert {"document": "fax_case_001", "format": "pdf"} in listed  # a PDF says so, so it's read through its transcription
    assert {h["support"] for h in hits} == {"supported", "unsupported"}  # every match comes back, annotated
    assert hit["kinds"] == ["transcription"] and "provenance" not in hit
    assert hit["where"].startswith("page 2") and isinstance(hit["passage"], str)
    assert refused["status"] == "refused" and "'Made up.'" in refused["issues"][0]  # nothing written, said in its terms
    assert written["status"] == "written" and written["advanced"] and written["support"] == "supported"
    assert why["status"] == "supported" and why["issues"] == [] and not why["truncated"]
    assert [link["kind"] for s in why["spans"] for link in s["rests_on"]] == [
        "transcription", "source"]  # the OCR's text, read from a region of a page
    assert reading["base"] == written["base"] and hit["handle"] in reading["text"]
    assert edited["status"] == "written" and edited["advanced"]
    assert edited["support"] == "partially_supported"  # "Proceed." is unsourced
    version = bond.document("note").head
    assert bond.text(Ref(version, VersionLocator())) == f"{FINDING} Proceed."
    assert [c.kind for c in bond.citations(version)] == [CitationKind.QUOTE, CitationKind.UNSOURCED]
    assert bond.verify(version) == []


def test_one_server_serves_several_workspaces_by_name_and_citing_stays_within_one(tmp_path):
    with Bond.local(tmp_path / "case-001") as one, Bond.local(tmp_path / "case-002") as two:
        one.ingest("exam", b"Active drip.\n")
        two.ingest("exam", b"Floor dry.\n")

        async def steps(client: Client):
            [first] = await call(client, "search", workspace="case-001", phrases=["Active drip"])
            [second] = await call(client, "search", workspace="case-002", phrases=["Floor dry"])
            quote = f'<bond:cite kind="quote" from="{first["handle"]}">Active drip</bond:cite>'
            here = await call(client, "write", workspace="case-001", document="note", draft=quote)
            there = await call(client, "write", workspace="case-002", document="note", draft=quote)
            gait = f'<bond:cite kind="quote" from="{second["handle"]}">Floor dry</bond:cite>'
            other = await call(client, "write", workspace="case-002", document="note", draft=gait)
            elsewhere = await error(client, "trace", workspace="case-002", handle=first["handle"])
            unknown = await error(client, "read", workspace="case-003", document="exam")
            listed = {w: await call(client, "documents", workspace=w) for w in ("case-001", "case-002")}
            return client.instructions, here, there, other, elsewhere, unknown, listed

        instructions, here, there, other, elsewhere, unknown, listed = serve(
            Fixed({"case-001": one, "case-002": two}), steps)
        assert instructions is not None and "- 'case-001'\n- 'case-002'\n" in instructions
        assert here["status"] == "written" and here["support"] == "supported"
        # A handle from one workspace names nothing in the other, so a write there can't cite it.
        assert there["status"] == "refused" and "names nothing" in there["issues"][0]
        assert other["status"] == "written" and other["support"] == "supported"
        assert "names nothing" in elsewhere
        assert "'case-003' isn't a workspace you can work in (you can in: 'case-001', 'case-002')" in unknown
        assert all([d["document"] for d in listing] == ["exam", "note"] for listing in listed.values())
        assert one.text(Ref(one.document("note").head, VersionLocator())) == "Active drip"
        assert two.text(Ref(two.document("note").head, VersionLocator())) == "Floor dry"


def test_workspaces_are_looked_up_on_each_call_so_one_granted_later_is_served(bond, case):
    workspaces: dict[str, Bond] = {}

    async def steps(client: Client):
        before = await error(client, "documents", workspace=CASE)
        workspaces[CASE] = bond  # granted while the server runs
        after = await call(client, "documents", workspace=CASE)
        return before, after

    before, after = serve(Fixed(workspaces), steps)
    assert "isn't a workspace you can work in" in before
    assert {"document": "fax_case_001.reducto", "format": "json"} in after


def test_a_call_that_cant_be_carried_out_is_an_error_the_agent_can_read(bond, case):
    async def steps(client: Client):
        return (
            await error(client, "read", workspace=CASE, document="nope"),
            await error(client, "read", workspace=CASE, document="fax_case_001"),
            await error(client, "search", workspace=CASE, phrases=[FINDING], within="nothing@1-2"),
        )

    missing, pdf, bad_handle = session(bond, steps)
    assert "no document is named 'nope'" in missing
    assert "'fax_case_001.reducto'" in pdf  # a PDF points to the documents that transcribe it
    assert "search()" in bad_handle


def test_a_failure_the_agent_cant_fix_is_a_crash_not_an_explanation(bond, case):
    # The store loses a blob: nothing the agent asked for was wrong, so nothing about the store is explained to it.
    content_hash = details.version(bond, case["fax"]).content_hash
    assert isinstance(bond.blobs, LocalBlobs)
    bond.blobs.path(content_hash).unlink()

    async def steps(client: Client):
        return await error(client, "read", workspace=CASE, document="fax_case_001.reducto")

    message = session(bond, steps)
    assert content_hash not in message and case["fax"] not in message and "blob" not in message


def test_an_agent_cites_a_policy_in_a_linked_workspace_where_it_is(bond, tmp_path):
    with Bond.local(tmp_path / "policies") as policies:
        policies.ingest("policy/IP-112", b"# IP-112\n\nSix weeks of written notice.\n")

        async def steps(client: Client):
            listed = await call(client, "documents", workspace=CASE, from_workspace="policies")
            read = await call(client, "read", workspace=CASE, document="policy/IP-112", from_workspace="policies")
            [hit] = await call(client, "search", workspace=CASE, phrases=["Six weeks"])
            draft = f'<bond:cite kind="quote" from="{hit["handle"]}">Six weeks</bond:cite>'
            written = await call(client, "write", workspace=CASE, document="note", draft=draft)
            return listed, read, hit, written

        listed, read, hit, written = session(bond, steps, {"policies": policies})
        assert bond.verify(bond.document("note").head) == []
    assert listed == [{"document": "policy/IP-112", "format": "markdown"}]
    assert read["workspace"] == "policies" and "Six weeks" in read["text"]
    assert hit["workspace"] == "policies" and hit["documents"] == ["policy/IP-112"]
    assert hit["support"] == "supported" and hit["kinds"] == ["source"]
    assert written["status"] == "written" and written["support"] == "supported"
    assert [d.name for d in bond.documents()] == ["note"]  # the policy stays where it is


def test_a_workspace_that_isnt_linked_cant_be_read_naming_the_linked_ones(bond, tmp_path):
    with Bond.local(tmp_path / "policies") as policies, Bond.local(tmp_path / "case-002") as other:
        other.ingest("exam", b"Normal gait.\n")
        bond.set_links([policies])

        async def steps(client: Client):
            return (
                # Served, but not linked to CASE: serving a workspace doesn't let others read it.
                await error(client, "read", workspace=CASE, document="exam", from_workspace="case-002"),
                await error(client, "documents", workspace=CASE, from_workspace="case-002"),
                await error(client, "search", workspace="case-002", phrases=["gait"], from_workspace="policies"),
            )

        unlinked, unlisted, no_links = serve(Fixed({CASE: bond, "case-002": other}), steps)
    assert "this workspace doesn't link to 'case-002' (it links to: 'policies')" in unlinked
    assert "(it links to: 'policies')" in unlisted
    assert "doesn't link to 'policies' (it links to: none)" in no_links
    assert bond.documents() == []


def test_the_agent_cant_mark_its_own_text_source(bond, case):
    async def steps(client: Client):
        return await call(client, "write", workspace=CASE, document="note",
                          draft='<bond:cite kind="source">Trust me.</bond:cite>')

    assert session(bond, steps) == {"status": "refused", "issues": [
        '<bond:cite kind="source">: source citations are not allowed here (use one of: paraphrase, quote, unsourced)']}


def test_a_linked_unsourced_draft_stays_unsupported(bond, tmp_path):
    with Bond.local(tmp_path / "external") as source:
        written(Tools(source).write("draft", '<bond:cite kind="unsourced">No evidence.</bond:cite>'))
        original = source.document("draft").head

        async def steps(client: Client):
            [hit] = await call(client, "search", workspace=CASE, phrases=["No evidence."])
            why = await call(client, "trace", workspace=CASE, handle=hit["handle"])
            return hit, why

        hit, why = session(bond, steps, {"external": source})
    assert original.startswith(hit["handle"].split("@")[0]) and hit["workspace"] == "external"
    assert hit["support"] == "unsupported" and hit["kinds"] == ["unsourced"]  # never promoted to a source
    assert why["status"] == "unsupported" and why["issues"] == [{"reason": "unsourced", "handle": hit["handle"]}]
    assert not why["truncated"] and why["unreachable"] == []
    assert why["spans"][0]["undeclared"] == [] and why["spans"][0]["rests_on"] == [
        {"kind": "unsourced", "attrs": {}, "handles": []}]


def test_mcp_trace_reports_unreachable_dependencies(bond, tmp_path):
    from bond import Citation, Format, TextLocator

    with Bond.local(tmp_path / "external") as source, Bond.local(tmp_path / "library") as library:
        root = source.ingest("root", b"Root fact.").version
        library.set_links([source])
        library.write_document("derived", b"Root fact.", [
            Citation(CitationKind.QUOTE, TextLocator(0, 10), [Ref(root, TextLocator(0, 10))]),
        ], format=Format.MARKDOWN)
        library.set_links([])  # the library stops linking to what derived rests on

        async def steps(client: Client):
            [hit] = await call(client, "search", workspace=CASE, phrases=["Root fact."])
            why = await call(client, "trace", workspace=CASE, handle=hit["handle"])
            return hit, why

        hit, why = session(bond, steps, {"library": library})
    assert hit["support"] == "unknown" and hit["workspace"] == "library"
    assert why["status"] == "unknown" and not why["truncated"] and why["unreachable"] == [root]
    assert why["spans"][-1]["handle"] == root and why["spans"][-1]["undeclared"] == []


def test_search_and_trace_report_support_over_mcp(bond):
    from bond import Format

    bond.ingest("source", b'{"x":"known"}', format=Format.JSON)
    tools = Tools(bond)
    [hit] = tools.search("known")
    out = written(tools.write("mixed", f'<bond:cite kind="quote" from="{hit.handle}">known</bond:cite> '
                              '<bond:cite kind="unsourced">guess</bond:cite>'))
    assert out.support == "partially_supported"

    async def steps(client: Client):
        [mixed] = await call(client, "search", workspace=CASE, phrases=["known guess"])
        why = await call(client, "trace", workspace=CASE, handle=mixed["handle"])
        bad_limit = await error(client, "trace", workspace=CASE, handle=mixed["handle"], limit=0)
        return mixed, why, bad_limit

    mixed, why, bad_limit = session(bond, steps)
    assert mixed["support"] == "partially_supported" and mixed["kinds"] == ["quote", "unsourced"]
    assert why["status"] == "partially_supported"
    assert [i["reason"] for i in why["issues"]] == ["unsourced"]
    assert "limit" in bad_limit
    # What a draft's handles point at says how well supported it is too.
    [evidence] = tools.draft("mixed").evidence
    assert evidence.kinds == [CitationKind.SOURCE] and evidence.support == "supported"


def test_a_missing_json_support_is_named_by_its_version_without_a_made_up_handle(bond, tmp_path):
    from bond import Citation, Format, JsonLocator, TextLocator

    with Bond.local(tmp_path / "json-source") as source, Bond.local(tmp_path / "library") as library:
        original = source.ingest("source", b'{"evidence":"fact"}', format=Format.JSON).version
        library.set_links([source])
        library.write_document("derived", b"fact", [
            Citation(CitationKind.QUOTE, TextLocator(0, 4), [Ref(original, JsonLocator((0,)))]),
        ], format=Format.MARKDOWN)
        library.set_links([])

        async def steps(client: Client):
            [hit] = await call(client, "search", workspace=CASE, phrases=["fact"])
            why = await call(client, "trace", workspace=CASE, handle=hit["handle"])
            return hit, why

        hit, why = session(bond, steps, {"library": library})
    assert hit["support"] == "unknown"
    assert why["status"] == "unknown" and {"reason": "unreachable", "handle": original} in why["issues"]
    assert why["spans"][0]["rests_on"][0]["handles"] == [original] and why["unreachable"] == [original]
