"""Workspaces: worlds of their own, whose documents cite the workspaces they link to where they are.

A policies workspace holds an intake policy and a criteria summary that quotes it. Cases link to it and cite it, with
nothing copied; a removed link leaves what rests on it unreachable, never broken.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bond import (
    Bond,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    Ref,
    SupportStatus,
    TextLocator,
    VersionId,
    VersionLocator,
    WriteError,
    details,
)
from bond.authoring import Grounds, ToolError, Tools
from bond.storage.local import LocalBlobs
from conftest import FIXTURES, STORES, rewrite_record, store_of, written

CRITERION = "A needed repair still not made after at least 6 weeks of written notice to the landlord."


@pytest.fixture(params=STORES)
def spaces(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[Bond, Bond, Bond]]:
    """policies, case-001 and case-002: three workspaces in one database, each with blobs of its own."""
    names = ("policies", "case-001", "case-002")
    with store_of(request.param, tmp_path) as store:
        bonds = [Bond(store, LocalBlobs(tmp_path / name / "blobs"), name) for name in names]
        yield bonds[0], bonds[1], bonds[2]


@pytest.fixture
def policies(spaces: tuple[Bond, Bond, Bond]) -> Bond:
    """The policy, and a criteria summary that quotes it."""
    policies = spaces[0]
    policies.ingest("policy/IP-112", FIXTURES / "policy_ip112.md")
    [hit] = policies.search(CRITERION, documents=["policy/IP-112"])
    summary = f"Criterion 2: {CRITERION}"
    policies.write_document(
        "criteria/IP-112",
        summary.encode(),
        [
        Citation(CitationKind.UNSOURCED, TextLocator(0, 11), []),
        Citation(CitationKind.QUOTE, TextLocator(13, len(summary)), [hit.ref]),
    ],
        format=Format.MARKDOWN,
    )
    return policies


def criteria_summary(bond: Bond):
    return bond.document("criteria/IP-112").head


def test_each_workspace_sees_only_its_own_documents(spaces, policies):
    _, case, other = spaces
    assert [d.name for d in policies.documents()] == ["policy/IP-112", "criteria/IP-112"]
    assert case.documents() == [] == other.documents() and case.search("written notice") == []
    assert case.workspace.name == "case-001" and case.workspace.id != policies.workspace.id
    assert case.links() == [] and case.reachable() == []


def quote_the_criterion(case: Bond, summary: VersionId, name: str = "determination") -> VersionId:
    """A determination in case quoting the criterion where the criteria summary in policies has it."""
    quoted = Ref(summary, TextLocator(13, 13 + len(CRITERION)))
    return case.write_document(name, CRITERION.encode(), [Citation(CitationKind.QUOTE, TextLocator(0, len(CRITERION)),
                                                                    [quoted])], format=Format.MARKDOWN).version


def test_a_case_cites_a_linked_workspace_where_it_is(spaces, policies):
    _, case, _ = spaces
    case.set_links([policies])
    summary, policy = criteria_summary(policies), policies.document("policy/IP-112").head
    determination = quote_the_criterion(case, summary)
    assert [v.id for v in details.versions(case)] == [determination]  # nothing was copied
    trace = case.trace(Ref(determination, VersionLocator()))
    assert [n.version for n in trace.nodes] == [determination, summary, policy] and trace.unreachable == frozenset()
    assert case.check_support(Ref(determination, VersionLocator())).status is SupportStatus.SUPPORTED
    assert case.verify(determination) == []
    assert details.owner(case, summary) is policies and details.filed_in(case, summary)[0].name == "criteria/IP-112"
    assert case.text(Ref(summary, VersionLocator())).startswith("Criterion 2")


def test_without_a_link_nothing_of_another_workspace_can_be_cited(spaces, policies):
    _, case, _ = spaces
    with pytest.raises(WriteError) as e:
        quote_the_criterion(case, criteria_summary(policies))
    assert IssueCode.UNKNOWN_VERSION in e.value.codes and case.filing.document("determination") is None


def test_links_reach_what_linked_workspaces_link_to_and_loops_are_followed_once(spaces, policies):
    _, case, other = spaces
    other.set_links([policies])
    case.set_links([other])
    policies.set_links([case])  # a loop: Bond follows it once; a deployment's links (bond.local) never loop
    assert [b.workspace.name for b in case.reachable()] == ["case-002", "policies"]
    determination = quote_the_criterion(case, criteria_summary(policies))
    assert case.verify(determination) == []


def test_removing_a_link_leaves_what_rests_on_it_unreachable_not_broken(spaces, policies):
    _, case, _ = spaces
    case.set_links([policies])
    summary = criteria_summary(policies)
    determination = quote_the_criterion(case, summary)
    case.set_links([])
    whole = Ref(determination, VersionLocator())
    assert case.trace(whole).unreachable == {summary}
    assert case.check_support(whole).status is SupportStatus.UNKNOWN
    assert [(i.code, i.version) for i in case.verify(determination)] == [(IssueCode.UNREACHABLE, summary)]
    case.set_links([policies])
    assert case.verify(determination) == []


def test_verify_checks_versions_in_linked_workspaces_too(spaces, policies):
    _, case, _ = spaces
    case.set_links([policies])
    summary = criteria_summary(policies)
    determination = quote_the_criterion(case, summary)

    def unquote(record):
        for c in record["citations"]:
            if c["kind"] == "quote":
                c["kind"] = "paraphrase"

    rewrite_record(policies, summary, unquote)
    assert (IssueCode.ID_MISMATCH, summary) in {(i.code, i.version) for i in case.verify(determination)}


def test_a_newer_version_in_a_linked_workspace_is_seen_from_the_case(spaces, policies):
    _, case, _ = spaces
    case.set_links([policies])
    summary = criteria_summary(policies)
    determination = quote_the_criterion(case, summary)
    revised = policies.blob(summary) + b" Revised."
    newer = policies.write_document("criteria/IP-112", revised, [Citation(CitationKind.SOURCE, VersionLocator(), [])],
                                    format=Format.MARKDOWN, base=summary).version
    trace = case.trace(Ref(determination, VersionLocator()))
    assert trace.newer == {summary: {"criteria/IP-112": newer}}


# --- an agent in a workspace -------------------------------------------------------------


def test_an_agent_reads_searches_and_cites_what_its_workspace_links_to(spaces, policies):
    _, case, other = spaces
    case.set_links([policies])
    tools = Tools(case)
    assert [d.document for d in tools.documents(workspace="policies")] == ["policy/IP-112", "criteria/IP-112"]
    assert tools.documents() == []
    assert tools.read("criteria/IP-112", workspace="policies").workspace == "policies"
    hits = tools.search("at least 6 weeks of written notice")
    assert {(h.workspace, tuple(h.documents)) for h in hits} == {
        ("policies", ("policy/IP-112",)), ("policies", ("criteria/IP-112",))}
    [hit] = tools.search("at least 6 weeks of written notice", document="policy/IP-112", workspace="policies")
    draft = f'<bond:cite kind="quote" from="{hit.handle}">at least 6 weeks of written notice</bond:cite>\n'
    done = written(tools.write("note", draft))
    assert done.support is SupportStatus.SUPPORTED
    assert [d.name for d in case.documents()] == ["note"] and len(policies.documents()) == 2  # writes stay here
    with pytest.raises(ToolError, match="doesn't link to 'case-002'.*'policies'"):
        tools.documents(workspace="case-002")
    with pytest.raises(ToolError, match="no document is named 'nothing'"):
        tools.read("nothing", workspace="policies")
    assert other.documents() == []


def test_trace_stops_at_an_unreachable_version_and_says_so(spaces, policies):
    _, case, _ = spaces
    case.set_links([policies])
    summary = criteria_summary(policies)
    quote_the_criterion(case, summary)
    case.set_links([])
    tools = Tools(case)
    [hit] = tools.search("after at least 6 weeks")
    why = tools.trace(hit.handle)
    assert why.unreachable == [summary] and why.status is SupportStatus.UNKNOWN
    assert why.spans[-1] == Grounds(summary, None, [], None, "", [], [], unreachable=True)
    assert [link.handles for link in why.spans[0].rests_on] == [[summary]]
    with pytest.raises(ToolError, match="rests on 1 version.*doesn't link to.*read"):
        tools.read("determination", citations=True)
    assert CRITERION in tools.read("determination").text


# --- across workspaces -------------------------------------------------------------------


def test_a_host_asks_every_case_what_rests_on_one_span_of_a_policy(spaces, policies):
    """Cross-workspace queries need no mapping: a version's id is the same wherever it is read from."""
    _, case, other = spaces
    [hit] = policies.search(CRITERION, documents=["policy/IP-112"])
    for bond, text in [(case, "Six weeks of written notice are documented."), (other, "Notice was given six weeks ago.")]:
        bond.set_links([policies])
        bond.write_document(
            "determination",
            text.encode(),
            [
            Citation(CitationKind.PARAPHRASE, TextLocator(0, len(text)), [hit.ref]),
        ],
            format=Format.MARKDOWN,
        )
    citing = {bond.workspace.name: [c.owner_version for c in bond.cited_by(hit.ref)] for bond in (case, other)}
    assert {name: len(found) for name, found in citing.items()} == {"case-001": 1, "case-002": 1}
    # The policies workspace sees its own summary resting on the span, and never the cases that do.
    assert [c.owner_version for c in policies.cited_by(hit.ref)] == [criteria_summary(policies)]
