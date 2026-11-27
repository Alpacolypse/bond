"""The app view: which tools render it, what the view can fetch, and that only a client that renders apps (whose host
keeps app-only tools from its model) can reach the tools a person uses; grants made from it last."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp_types import TextContent

from bond import Bond, Format
from bond.local import DECISIONS, Home
from bond.local.grants import GrantedWorkspaces
from bond.local.grants import create_server as grant_server
from bond.mcp.server import create_server
from bond.mcp.view import BLOB_CHUNK, VIEW_URI
from bond.workspaces import Fixed, Workspaces

VIEW = "<!doctype html><html><head><title>Bond</title></head><body><p>the view</p></body></html>"
APPS = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})  # what a host that renders apps declares
APP_ONLY = {"view_api", "view_head", "view_blob", "upload_part", "add_sources"}
GRANTING = {"view_workspaces", "grant_workspace", "new_workspace"}  # app-only too, in grant mode


def serve(workspaces: Workspaces, steps: Callable[[Client], Awaitable[Any]], *, apps: bool = True,
          grants: GrantedWorkspaces | None = None) -> Any:
    async def run() -> Any:
        server = (grant_server(grants, view=VIEW) if grants is not None
                  else create_server(workspaces, view=VIEW))
        async with Client(server, extensions=[APPS] if apps else None) as client:
            return await steps(client)

    return asyncio.run(run())


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content.get("result", result.structured_content)


async def person(client: Client) -> str:
    """The secret the server wrote into the view: what the view, and only the view, sends to act as the person."""
    [content] = (await client.read_resource(VIEW_URI)).contents
    found = re.search(r'<meta name="bond-person" content="([^"]+)">', getattr(content, "text", ""))
    assert found is not None
    return found.group(1)


async def upload(client: Client, me: str, data: bytes, upload: str = "u-12345678") -> str:
    """Send data in parts, as the view does, and return the upload to name to add_sources."""
    for offset in range(0, max(len(data), 1), BLOB_CHUNK):
        await call(client, "upload_part", person=me, upload=upload, offset=offset,
                   data=base64.b64encode(data[offset:offset + BLOB_CHUNK]).decode())
    return upload


async def error(client: Client, tool: str, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    assert result.is_error
    [message] = result.content
    assert isinstance(message, TextContent)
    return message.text


def test_show_write_and_edit_render_the_view_and_the_view_is_served(bond):
    async def steps(client: Client):
        tools = {t.name: t for t in (await client.list_tools()).tools}
        resource = await client.read_resource(VIEW_URI)
        return tools, resource

    tools, resource = serve(Fixed({"case": bond}), steps)
    renders = {n for n, t in tools.items() if (t.meta or {}).get("ui", {}).get("resourceUri") == VIEW_URI}
    assert renders == {"show", "write", "edit", *APP_ONLY} and "workspaces" not in tools  # only in grant mode
    assert {n for n, t in tools.items() if t.meta and t.meta["ui"].get("visibility") == ["app"]} == APP_ONLY
    assert "visibility" not in tools["show"].meta["ui"]  # the model calls it; the host shows its result
    [content] = resource.contents
    text = getattr(content, "text", "")
    assert content.mime_type == APP_MIME_TYPE and re.fullmatch(
        r'<!doctype html><html><head><meta name="bond-person" content="[\w-]{32}"><title>Bond</title>.*', text)


def test_a_client_that_doesnt_render_apps_neither_sees_nor_calls_the_tools_a_person_uses(bond):
    async def steps(client: Client):
        me = await person(client)
        names = {t.name for t in (await client.list_tools()).tools}
        return names, await error(client, "add_sources", person=me, workspace="case",
                                  files=[{"name": "fax.md", "upload": "u-12345678"}])

    names, refused = serve(Fixed({"case": bond}), steps, apps=False)
    assert not names & (APP_ONLY | {"show"}) and {"read", "write"} <= names  # read is the whole document there
    assert "only offered in a host that shows Bond's view" in refused
    assert bond.documents() == []


def test_without_the_view_the_server_is_text_only(bond):
    async def steps(client: Client):
        return {t.name: t for t in (await client.list_tools()).tools}

    async def run() -> Any:
        async with Client(create_server(Fixed({"case": bond})), extensions=[APPS]) as client:
            return await steps(client)

    tools = asyncio.run(run())
    assert not set(tools) & APP_ONLY and all(not (t.meta or {}).get("ui") for t in tools.values())


def test_show_names_the_document_for_the_view_and_says_whether_it_rests_on_sources(bond):
    bond.ingest("fax.md", b"Fax.", format=Format.MARKDOWN)

    async def steps(client: Client):
        return (await call(client, "show", workspace="case", document="fax.md"),
                await error(client, "show", workspace="case", document="fax"),
                await error(client, "show", workspace="elsewhere", document="fax.md"))

    shown, missing, elsewhere = serve(Fixed({"case": bond}), steps)
    assert shown == {"document": "fax.md", "support": "supported", "status": "shown"}  # the view fetches the rest
    assert "no document named 'fax'" in missing and "isn't a workspace you can work in" in elsewhere


def test_the_view_reads_the_viewers_api_and_a_versions_bytes_in_chunks(bond):
    big = ("# Notes\n\n" + "Repair request sent twice weekly. " * 8000).encode()  # several chunks
    version = bond.ingest("notes.md", big, format=Format.MARKDOWN).version

    async def steps(client: Client):
        overview = await call(client, "view_api", workspace="case", url="/api/overview")
        missing = await call(client, "view_api", workspace="case", url="/api/versions/" + "0" * 64)
        page = await call(client, "view_api", workspace="case", url="/api/documents?q=notes&offset=0&limit=5")
        chunks, offset = [], 0
        while offset is not None:
            chunk = await call(client, "view_blob", workspace="case", version=version, offset=offset)
            chunks.append(chunk)
            offset = chunk["next"]
        refused = await error(client, "view_api", workspace="case", url=f"/api/versions/{version}/blob")
        head = await call(client, "view_head", workspace="case", document="notes.md")
        nameless = await error(client, "view_head", workspace="case", document="notes")  # names match exactly
        assert head == {"version": version} and "no document named 'notes'" in nameless
        return overview, missing, page, chunks, refused

    overview, missing, page, chunks, refused = serve(Fixed({"case": bond}), steps)
    assert overview["status"] == 200 and overview["body"]["workspace"]["name"] == bond.workspace.name
    assert missing["status"] == 404 and "detail" in missing["body"]
    assert page["status"] == 200 and [d["name"] for d in page["body"]["documents"]] == ["notes.md"]
    assert len(chunks) == -(-len(big) // BLOB_CHUNK) > 1
    assert b"".join(base64.b64decode(c["data"]) for c in chunks) == big
    assert {c["media_type"] for c in chunks} == {"text/markdown; charset=utf-8"} and "isn't a JSON route" in refused


def test_a_person_adds_sources_from_the_view(bond):
    async def steps(client: Client):
        me = await person(client)

        async def add(name: str, data: bytes, **kw: Any) -> Any:
            return await call(client, "add_sources", person=me, workspace="case",
                              files=[{"name": name, "upload": await upload(client, me, data)}], **kw)

        added = await add("fax.md", b"Fax.")
        taken = await error(client, "add_sources", person=me, workspace="case",
                            files=[{"name": "fax.md", "upload": await upload(client, me, b"2", "u-taken000")}])
        updated = await add("fax.md", b"Fax, again.", update=True)
        garbled = await error(client, "upload_part", person=me, upload="u-garbled0", offset=0, data="not base64!")
        unread = await error(client, "add_sources", person=me, workspace="case",
                             files=[{"name": "x.docx", "upload": await upload(client, me, b"x", "u-docx0000")}])
        return added, taken, updated, garbled, unread

    added, taken, updated, garbled, unread = serve(Fixed({"case": bond}), steps)
    assert added == {"added": [{"document": "fax.md", "added": True}], "note": None}
    assert "already has a document named 'fax.md'" in taken
    assert updated["added"] == [{"document": "fax.md", "added": True}] and len(bond.history("fax.md")) == 2
    assert "didn't arrive whole" in garbled and "can't read x.docx" in unread


def workspace(home: Home, name: str, text: str | None = None) -> None:
    with Bond.local(home.path(name)) as bond:
        if text is not None:
            bond.ingest("notes.md", text.encode(), format=Format.MARKDOWN)


def test_in_grant_mode_a_person_grants_from_the_picker_and_the_agent_works_there_at_once(tmp_path):
    home = Home(tmp_path / "home")
    workspace(home, "smith", "Ceiling leak for six weeks.")
    workspace(home, "jones")
    grants = GrantedWorkspaces(home, "desktop")

    async def steps(client: Client):
        me = await person(client)
        names = {t.name for t in (await client.list_tools()).tools}
        before = await call(client, "workspaces")
        refused = await error(client, "documents", workspace="smith")
        await call(client, "grant_workspace", person=me, name="smith", granted=True)
        listed = await call(client, "documents", workspace="smith")
        picker = await call(client, "view_workspaces")
        await call(client, "new_workspace", person=me, name="brown")
        after = await call(client, "workspaces")
        await call(client, "grant_workspace", person=me, name="smith", granted=False)
        revoked = await error(client, "documents", workspace="smith")
        return names, before, refused, listed, picker, after, revoked

    try:
        names, before, refused, listed, picker, after, revoked = serve(grants, steps, grants=grants)
    finally:
        grants.close()
    assert GRANTING | {"workspaces"} <= names
    assert before == {"workspaces": []} and "isn't a workspace you can work in" in refused
    assert listed == [{"document": "notes.md", "format": "markdown"}]
    assert picker["workspaces"] == [{"name": "jones", "granted": False, "links": []},
                                    {"name": "smith", "granted": True, "links": []}]
    assert after == {"workspaces": ["smith", "brown"]}
    assert "isn't a workspace you can work in" in revoked and home.names() == ["brown", "jones", "smith"]


def test_grants_belong_to_a_configuration_and_outlast_its_processes(tmp_path):
    home = Home(tmp_path / "home")
    workspace(home, "smith")
    home.grant("desktop", "smith")
    again, other = GrantedWorkspaces(home, "desktop"), GrantedWorkspaces(home, "claude-code")
    try:
        assert again.names() == ["smith"] and other.names() == []  # a new process for desktop; another configuration
        assert again.open("smith").workspace.name == "smith"
    finally:
        again.close(), other.close()
    assert Home(home.root).grantees("smith") == ["desktop"]
    assert home.revoke("desktop", "smith") and not home.revoke("desktop", "smith")
    assert (tmp_path / "home" / DECISIONS).is_file()  # in the home, beside the workspaces

def test_a_big_file_comes_in_parts_and_is_added_as_one(bond):
    big = ("# Notes\n\n" + "Repair request sent twice weekly. " * 12000).encode()  # several parts
    parts = [big[i:i + BLOB_CHUNK] for i in range(0, len(big), BLOB_CHUNK)]

    async def steps(client: Client):
        me = await person(client)
        offset = 0
        for part in parts:
            sent = await call(client, "upload_part", person=me, upload="u-12345678", offset=offset,
                              data=base64.b64encode(part).decode())
            offset = sent["size"]
        out_of_order = await error(client, "upload_part", person=me, upload="u-12345678", offset=0, data="eA==")
        bad_name = await error(client, "upload_part", person=me, upload="../../etc", offset=0, data="eA==")
        added = await call(client, "add_sources", person=me, workspace="case", files=[{"name": "notes.md", "upload": "u-12345678"}])
        gone = await error(client, "add_sources", person=me, workspace="case", files=[{"name": "n2.md", "upload": "u-12345678"}])
        return offset, out_of_order, bad_name, added, gone

    offset, out_of_order, bad_name, added, gone = serve(Fixed({"case": bond}), steps)
    assert len(parts) > 2 and offset == len(big)
    assert f"next part goes at offset {len(big)}, not 0" in out_of_order and "can't name an upload" in bad_name
    assert added["added"] == [{"document": "notes.md", "added": True}]
    assert bond.blob(bond.filing.document("notes.md").head) == big  # the parts, in order, as one file
    assert "has no parts here" in gone  # an upload is added once


def test_a_model_that_sees_the_app_only_tools_still_cant_act_as_the_person(tmp_path):
    # As if a host let its model call app-only tools: without the secret only the view holds, nothing changes.
    home = Home(tmp_path / "home")
    workspace(home, "smith")
    grants = GrantedWorkspaces(home, "desktop")
    fax = [{"name": "fax.md", "upload": "u-12345678"}]

    async def steps(client: Client):
        refusals = [await error(client, "grant_workspace", name="smith", granted=True, person=guess)
                    for guess in ("", "guess")]
        refusals.append(await error(client, "new_workspace", name="brown", person="guess"))
        refusals.append(await error(client, "upload_part", upload="u-12345678", offset=0, data="eA==", person="x"))
        await call(client, "grant_workspace", name="smith", granted=True, person=await person(client))  # the view can
        refusals.append(await error(client, "add_sources", workspace="smith", files=fax, person="guess"))
        refusals.append(await error(client, "grant_workspace", name="smith", granted=False, person="guess"))
        return refusals

    try:
        refusals = serve(grants, steps, grants=grants)
    finally:
        grants.close()
    assert all("only the person using Bond's view can do this" in r for r in refusals), refusals
    assert grants.names() == ["smith"] and home.names() == ["smith"]  # granted by the view only; nothing created
    with Bond.local(home.path("smith"), create=False) as smith:
        assert smith.documents() == []
