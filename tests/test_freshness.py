"""Freshness: whether evidence is still current where it is filed, asked, never stored; and transcriptions, found by
what they cite."""

from __future__ import annotations

import pytest

from bond import (
    Bond,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    PdfLocator,
    Ref,
    SpanStatus,
    TextLocator,
    VersionLocator,
    WriteError,
)
from bond.authoring import AGENT_POLICY, Tools
from bond.importers.text_layer import TEXT_LAYER
from bond.policies import Policy
from bond.transcription import transcribe
from conftest import FIXTURES, written

NOTE = "Six weeks of written notice."


def span(text: str, phrase: str) -> TextLocator:
    start = text.index(phrase)
    return TextLocator(start, start + len(phrase))


def test_a_head_is_current(bond):
    v = bond.ingest("notes", NOTE.encode(), format=Format.MARKDOWN).version
    fresh = bond.freshness(Ref(v, span(NOTE, "Six weeks")))
    [d] = fresh.documents
    assert d.current and d.head == v and d.superseded_at is None and d.span is None and not fresh.superseded


@pytest.mark.parametrize(("next_text", "status", "at"), [
    ("Six weeks of written notice. Then a repair.", SpanStatus.UNCHANGED, "Six weeks"),
    ("Before: six weeks of written notice.", SpanStatus.MOVED, "six weeks"),
    ("Two weeks of silence.", SpanStatus.GONE, None),
])
def test_a_superseded_span_says_what_became_of_its_text(bond, next_text, status, at):
    v1 = bond.ingest("notes", NOTE.encode(), format=Format.MARKDOWN).version
    v2 = bond.ingest("notes", next_text.encode(), format=Format.MARKDOWN, base=v1).version
    fresh = bond.freshness(Ref(v1, span(NOTE, "Six weeks")))
    [d] = fresh.documents
    assert fresh.superseded and not d.current and d.head == v2
    assert d.superseded_at == bond.history("notes")[1].at
    assert d.span is not None and d.span.status is status
    assert d.span.at == (None if at is None else Ref(v2, span(next_text, at)))


def test_freshness_is_relative_to_a_document(bond):
    v = bond.ingest("a", NOTE.encode(), format=Format.MARKDOWN).version
    bond.ingest("b", NOTE.encode(), format=Format.MARKDOWN)
    bond.ingest("b", b"Moved on.", format=Format.MARKDOWN, base=v)
    fresh = bond.freshness(Ref(v, VersionLocator()))
    assert {d.document: d.current for d in fresh.documents} == {"a": True, "b": False}
    assert not fresh.superseded  # still a's head
    [only_b] = bond.freshness(Ref(v, VersionLocator()), document="b").documents
    assert only_b.document == "b" and only_b.span is None  # a whole version has no span to follow


def test_whether_a_version_was_current_at_a_time(bond):
    v1 = bond.ingest("notes", NOTE.encode(), format=Format.MARKDOWN).version
    written_at = bond.history("notes")[0].at
    bond.ingest("notes", b"Later.", format=Format.MARKDOWN, base=v1)
    [d] = bond.freshness(Ref(v1, VersionLocator()), as_of=written_at).documents
    assert d.current_as_of and not d.current
    [later] = bond.freshness(Ref(v1, VersionLocator()), as_of=bond.history("notes")[1].at).documents
    assert later.current_as_of is False


def test_an_imported_version_says_so(bond):
    policies = Bond(bond.store, bond.blobs, "policies")
    policies.ingest("policy", NOTE.encode(), format=Format.MARKDOWN)
    v = bond.import_document(policies, "policy").version
    assert bond.freshness(Ref(v, VersionLocator())).imported
    assert not policies.freshness(Ref(v, VersionLocator())).imported


def test_search_within_an_older_version_names_what_is_newer(bond):
    v1 = bond.ingest("notes", NOTE.encode(), format=Format.MARKDOWN).version
    v2 = bond.ingest("notes", b"Six weeks of silence.", format=Format.MARKDOWN, base=v1).version
    [old] = bond.search("Six weeks", within=Ref(v1, VersionLocator()))
    [new] = bond.search("Six weeks", documents=["notes"])
    assert old.newer == {"notes": v2} and new.newer == {}


def test_a_trace_names_what_is_newer_for_each_node(bond):
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = bond.search("Six weeks", documents=["src"])
    summary = bond.write_document("summary", "Six weeks", [Citation(CitationKind.QUOTE, TextLocator(0, 9), [hit.ref])],
                                  format=Format.MARKDOWN).version
    v2 = bond.ingest("src", b"Revised.", format=Format.MARKDOWN, base=v1).version
    trace = bond.trace(Ref(summary, VersionLocator()))
    assert trace.newer == {v1: {"src": v2}}


def test_citing_a_superseded_version_warns_and_a_policy_can_refuse_it(bond):
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = bond.search("Six weeks", documents=["src"])
    bond.ingest("src", b"Six weeks of written notice, then a repair.", format=Format.MARKDOWN, base=v1)
    cite = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [hit.ref])]
    result = bond.write_document("summary", "Six weeks", cite, format=Format.MARKDOWN)
    [warning] = result.warnings
    assert warning.code is IssueCode.CITES_SUPERSEDED and "unchanged there" in warning.message
    assert (warning.citation_index, warning.support_index) == (0, 0)
    with pytest.raises(WriteError) as e:
        bond.write_document("strict", "Six weeks", cite, format=Format.MARKDOWN, policy=Policy(refuse_superseded=True))
    assert e.value.codes == [IssueCode.CITES_SUPERSEDED]
    assert IssueCode.CITES_SUPERSEDED in {i.code for i in bond.check_write(
        "strict", "Six weeks", cite, format=Format.MARKDOWN, policy=Policy(refuse_superseded=True))}


def test_a_loaded_plan_is_held_to_refuse_superseded(bond):
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = bond.search("Six weeks", documents=["src"])
    cite = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [hit.ref])]
    plan = bond.plan_write("summary", "Six weeks", cite, format=Format.MARKDOWN, policy=Policy(refuse_superseded=True))
    staged = bond.stage(plan)
    bond.ingest("src", b"Revised.", format=Format.MARKDOWN, base=v1)
    with pytest.raises(WriteError) as e:
        bond.commit(bond.load_plan(staged))
    assert e.value.codes == [IssueCode.CITES_SUPERSEDED]


def test_an_agent_is_told_what_is_newer_and_warned_when_it_cites_older(bond):
    tools = Tools(bond, AGENT_POLICY)
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = tools.search("Six weeks", document="src")
    v2 = bond.ingest("src", b"Notes: Six weeks of written notice.", format=Format.MARKDOWN, base=v1).version
    [old] = tools.search("Six weeks", within=hit.handle)
    assert list(old.newer) == ["src"] and old.newer["src"].startswith(v2[:8])
    out = written(tools.write("summary.md", f'<bond:cite kind="quote" from="{hit.handle}">Six weeks</bond:cite>\n'))
    [warning] = out.warnings
    assert warning.startswith(hit.handle) and "where the same text is" in warning
    [chain_node] = [g for g in tools.trace(out.base).spans if g.handle == hit.handle]
    assert list(chain_node.newer) == ["src"]


# --- transcriptions ------------------------------------------------------------------------------


def test_transcriptions_are_found_by_what_they_cite(bond):
    pdf = bond.ingest("fax", (FIXTURES / "fax_case_001.pdf").read_bytes(), format=Format.PDF).version
    t = transcribe(bond, "fax", TEXT_LAYER).version
    [found] = bond.transcriptions_of(pdf)
    assert (found.version, found.producer, found.documents) == (t, "pypdf", ["fax.pypdf"])


def test_reading_a_pdf_whose_transcriptions_are_of_an_earlier_version_says_so(bond):
    from bond.authoring import ToolError

    v1 = bond.ingest("fax", (FIXTURES / "fax_case_001.pdf").read_bytes(), format=Format.PDF).version
    transcribe(bond, "fax", TEXT_LAYER)
    v2 = bond.ingest("fax", (FIXTURES / "fax_case_002.pdf").read_bytes(), format=Format.PDF, base=v1).version
    assert bond.transcriptions_of(v2) == []
    with pytest.raises(ToolError, match=r"transcribe an earlier version of it.*'fax\.pypdf'"):
        Tools(bond).read("fax")
    # And a region of the old page has no text to follow into the new one.
    [d] = bond.freshness(Ref(v1, PdfLocator(0, (0.1, 0.1, 0.5, 0.2)))).documents
    assert d.span is not None and d.span.status is SpanStatus.UNKNOWN
