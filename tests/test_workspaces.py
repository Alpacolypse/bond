"""Workspaces: closed worlds that bring material in from each other by import.

A policies workspace holds an intake policy and a criteria summary that quotes it. Cases import from it, with their provenance, leaving a frontier that importing a version closes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bond import (
    Bond,
    BondError,
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
from bond.models import Imported
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
    assert case.documents() == [] == other.documents() and case.search("notice") == []
    assert case.workspace.name == "case-001" and case.workspace.id != policies.workspace.id


def test_a_source_import_has_the_same_id_in_every_workspace(spaces, policies):
    _, case, other = spaces
    in_case = case.import_document(policies, "policy/IP-112")
    in_other = other.import_document(policies, "policy/IP-112")
    assert in_case.version == in_other.version == policies.document("policy/IP-112").head
    [entry] = case.history("policy/IP-112")
    assert entry.imported == Imported(policies.workspace.id, "policies", in_case.version)
    assert case.verify(in_case.version) == [] and case.frontier() == []


def test_an_origin_stays_in_the_workspace_that_ingested_the_file(spaces):
    """Where a file came from is filed where it was ingested; an import records the workspace it came from instead."""
    policies, case, _ = spaces
    ingested = policies.ingest("policy", FIXTURES / "policy_ip112.md", origin="s3://legal-aid/IP-112.md")
    imported = case.import_document(policies, "policy")
    assert imported.version == ingested.version
    [here] = policies.history("policy")
    [there] = case.history("policy")
    assert (here.origin, here.imported) == ("s3://legal-aid/IP-112.md", None)
    assert (there.origin, there.imported) == (None, Imported(policies.workspace.id, "policies", ingested.version))


def test_an_import_with_provenance_keeps_its_id_and_leaves_a_frontier(spaces, policies):
    _, case, _ = spaces
    summary, policy = criteria_summary(policies), policies.document("policy/IP-112").head
    result = case.import_document(policies, "criteria/IP-112")
    assert result.version == summary and case.frontier() == [policy]
    assert [c.kind for c in case.citations(summary)] == [CitationKind.UNSOURCED, CitationKind.QUOTE]

    quoted = Ref(summary, TextLocator(13, 13 + len(CRITERION)))
    trace = case.trace(quoted)
    assert trace.frontier == {policy} and [n.version for n in trace.nodes] == [summary, policy]
    assert trace.edges_at(trace.nodes[1]) == []

    issues = case.verify(summary)  # the quote can't be checked here, and nothing else is wrong
    assert [(i.code, i.version) for i in issues] == [(IssueCode.OUTSIDE_WORKSPACE, policy)]


def test_importing_a_frontier_version_closes_the_frontier_without_changing_anything(spaces, policies):
    _, case, _ = spaces
    summary, policy = criteria_summary(policies), policies.document("policy/IP-112").head
    case.import_document(policies, "criteria/IP-112")
    result = case.import_version(policies, policy)
    assert result.version == policy and result.advanced
    assert case.frontier() == [] and case.verify(summary) == []
    assert case.document("criteria/IP-112").head == summary
    assert details.filed_in(case, policy) == []  # evidence the summary rests on, not a document of the case
    assert case.import_version(policies, policy).advanced is False  # here already: nothing to copy


def test_importing_a_version_needs_something_here_to_rest_on_it(spaces, policies):
    _, case, _ = spaces
    policy = policies.document("policy/IP-112").head
    with pytest.raises(ValueError, match="nothing in this workspace rests on"):
        case.import_version(policies, policy)
    with pytest.raises(ValueError, match="nothing in this workspace rests on"):
        case.import_version(policies, VersionId("0" * 64))
    assert case.documents() == [] and details.versions(case) == []


def test_an_import_with_all_its_provenance_leaves_no_frontier(spaces, policies):
    _, case, _ = spaces
    result = case.import_document(policies, "criteria/IP-112", depth=None)
    assert case.frontier() == [] and case.verify(result.version) == []


def test_a_policy_imported_twice_closes_the_frontier_of_a_summary_that_quotes_it(spaces, policies):
    """Content addressing: the policy imported as a source is the very version the summary rests on."""
    _, case, _ = spaces
    case.import_document(policies, "criteria/IP-112")
    case.import_document(policies, "policy/IP-112")
    assert case.frontier() == [] and case.verify(criteria_summary(policies)) == []


def test_a_case_cites_what_it_imported_but_never_what_it_left_outside(spaces, policies):
    _, case, _ = spaces
    summary, policy = criteria_summary(policies), policies.document("policy/IP-112").head
    case.import_document(policies, "criteria/IP-112")
    quoted = Ref(summary, TextLocator(13, 13 + len(CRITERION)))
    text = "Criterion 2 needs six weeks of written notice."
    case.write_document(
        "determination",
        text.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, len(text)), [quoted]),
    ],
        format=Format.MARKDOWN,
    )
    with pytest.raises(WriteError) as e:
        case.write_document(
            "shortcut",
            text.encode(),
            [
            Citation(CitationKind.PARAPHRASE, TextLocator(0, len(text)), [Ref(policy, VersionLocator())]),
        ],
            format=Format.MARKDOWN,
        )
    assert IssueCode.UNKNOWN_VERSION in e.value.codes and case.filing.document("shortcut") is None


def test_an_import_refuses_a_version_whose_records_were_changed(spaces, policies):
    _, case, _ = spaces
    summary = criteria_summary(policies)
    def unquote(record):
        for c in record["citations"]:
            if c["kind"] == "quote":
                c["kind"] = "paraphrase"

    for v in list(policies.records.ids()):
        rewrite_record(policies, v, unquote)
    with pytest.raises(BondError, match="doesn't hash to its id"):
        case.import_document(policies, "criteria/IP-112")
    assert case.documents() == [] and case.records.version(summary) is None


def test_a_document_is_imported_from_another_workspace_only(policies):
    with pytest.raises(ValueError, match="another workspace"):
        policies.import_document(policies, "policy/IP-112")


def test_a_newer_version_is_imported_from_the_head_it_replaces(spaces, policies):
    _, case, _ = spaces
    first = case.import_document(policies, "policy/IP-112")
    revised = (FIXTURES / "policy_ip112.md").read_bytes() + b"\nRevised October 2026.\n"
    policies.ingest("policy/IP-112", revised, format=Format.MARKDOWN, base=policies.document("policy/IP-112").head)
    with pytest.raises(WriteError, match="already exists"):
        case.import_document(policies, "policy/IP-112")
    second = case.import_document(policies, "policy/IP-112", base=first.version)
    assert [e.version for e in case.history("policy/IP-112")] == [first.version, second.version]


# --- an agent in a workspace -------------------------------------------------------------


def test_an_agent_imports_only_from_the_workspaces_it_was_given(spaces, policies):
    _, case, other = spaces
    tools = Tools(case, sources={"policies": policies})
    assert [d.document for d in tools.documents(workspace="policies")] == ["policy/IP-112", "criteria/IP-112"]
    assert tools.documents() == [] and tools.search("written notice") == []
    imported = tools.import_document("policies", "policy/IP-112")
    assert (imported.document, imported.format, imported.frontier, imported.truncated) == (
        "policy/IP-112", Format.MARKDOWN, [], False)
    [hit] = tools.search("at least 6 weeks of written notice")
    draft = f'<bond:cite kind="quote" from="{hit.handle}">at least 6 weeks of written notice</bond:cite>\n'
    written(tools.write("note", draft))
    with pytest.raises(ToolError, match="isn't a workspace you can import from.*'policies'"):
        tools.documents(workspace="case-002")
    with pytest.raises(ToolError, match="no document named 'nothing'"):
        tools.import_document("policies", "nothing")
    policies.ingest("note", b"Another note.", format=Format.MARKDOWN)
    with pytest.raises(ToolError, match="already has a document named 'note' that wasn't imported"):
        tools.import_document("policies", "note")
    assert other.documents() == []


def test_importing_again_brings_the_newest_version(spaces, policies):
    _, case, _ = spaces
    tools = Tools(case, sources={"policies": policies})
    tools.import_document("policies", "policy/IP-112")
    head = policies.document("policy/IP-112").head
    policies.ingest("policy/IP-112", policies.blob(head) + b"\nRevised.\n", format=Format.MARKDOWN, base=head)
    tools.import_document("policies", "policy/IP-112")
    assert len(case.history("policy/IP-112")) == 2
    assert case.blob(case.document("policy/IP-112").head).endswith(b"Revised.\n")


def test_trace_stops_at_the_frontier_and_says_so(spaces, policies):
    _, case, _ = spaces
    case.import_document(policies, "criteria/IP-112")
    tools = Tools(case)
    [hit] = tools.search("after at least 6 weeks")
    why = tools.trace(hit.handle)
    policy = policies.document("policy/IP-112").head
    assert why.frontier == [policy] and why.status is SupportStatus.UNKNOWN
    assert why.spans[-1] == Grounds(policy, [], None, "", [], [], outside=True)
    assert [link.handles for link in why.spans[0].rests_on] == [[policy]]
    with pytest.raises(ToolError, match="rests on 1 version.*outside this workspace.*read"):
        tools.draft("criteria/IP-112")
    assert "Criterion 2" in tools.read("criteria/IP-112").text


# --- across workspaces -------------------------------------------------------------------


def test_a_host_asks_every_case_what_rests_on_one_span_of_a_policy(spaces, policies):
    """Cross-workspace queries need no mapping: a version's id is the same in every workspace that holds it."""
    _, case, other = spaces
    [hit] = policies.search(CRITERION, documents=["policy/IP-112"])
    for bond, text in [(case, "Six weeks of notice are documented."), (other, "Notice lasted six weeks.")]:
        bond.import_document(policies, "policy/IP-112")
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
