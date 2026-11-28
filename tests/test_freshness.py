"""Staleness: what is newer than the evidence a hit, a trace or a write names, told and never enforced; and
transcriptions, found by what they cite."""

from __future__ import annotations

import pytest

from bond import (
    Bond,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    Ref,
    TextLocator,
    VersionLocator,
)
from bond.authoring import AGENT_POLICY, Tools
from bond.importers.text_layer import TEXT_LAYER
from bond.transcription import transcribe
from conftest import FIXTURES, planned_issues, written

NOTE = "Six weeks of written notice."


def span(text: str, phrase: str) -> TextLocator:
    start = text.index(phrase)
    return TextLocator(start, start + len(phrase))


def test_a_version_held_as_a_head_somewhere_isnt_superseded(bond):
    """Newer is relative to a document: a version some document still holds as its head draws no warning."""
    v = bond.ingest("a", NOTE.encode(), format=Format.MARKDOWN).version
    bond.ingest("b", NOTE.encode(), format=Format.MARKDOWN)
    bond.ingest("b", b"Moved on.", format=Format.MARKDOWN, base=v)
    [hit] = bond.search("Six weeks", documents=["a"])
    assert hit.newer == {"b": bond.document("b").head}
    cite = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [hit.ref])]
    assert bond.write_document("summary", "Six weeks", cite, format=Format.MARKDOWN).warnings == ()


def test_a_linked_version_is_newer_or_not_in_the_workspace_that_holds_it(bond):
    policies = Bond(bond.store, bond.blobs, "policies")
    v1 = policies.ingest("policy", NOTE.encode(), format=Format.MARKDOWN).version
    bond.set_links([policies])
    cite = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [Ref(v1, span(NOTE, "Six weeks"))])]
    v2 = policies.ingest("policy", b"Eight weeks of rest.", format=Format.MARKDOWN, base=v1).version
    [warning] = bond.write_document("summary", "Six weeks", cite, format=Format.MARKDOWN).warnings
    assert warning.code is IssueCode.CITES_SUPERSEDED and f"'policy' has moved past it to {v2[:12]}" in warning.message


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


def test_citing_a_superseded_version_warns_and_is_never_refused(bond):
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = bond.search("Six weeks", documents=["src"])
    bond.ingest("src", b"Six weeks of written notice, then a repair.", format=Format.MARKDOWN, base=v1)
    cite = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [hit.ref])]
    result = bond.write_document("summary", "Six weeks", cite, format=Format.MARKDOWN)
    [warning] = result.warnings
    assert warning.code is IssueCode.CITES_SUPERSEDED and "'src' has moved past it" in warning.message
    assert (warning.citation_index, warning.support_index) == (0, 0)
    assert planned_issues(bond, "again", "Six weeks", cite, format=Format.MARKDOWN) == []  # a warning, not an issue
    assert bond.verify(result.version) == []  # the citation is exactly as true as when it was written


def test_an_agent_is_told_what_is_newer_and_warned_when_it_cites_older(bond):
    tools = Tools(bond, AGENT_POLICY)
    v1 = bond.ingest("src", NOTE.encode(), format=Format.MARKDOWN).version
    [hit] = tools.search("Six weeks", document="src")
    v2 = bond.ingest("src", b"Notes: Six weeks of written notice.", format=Format.MARKDOWN, base=v1).version
    [old] = tools.search("Six weeks", within=hit.handle)
    assert list(old.newer) == ["src"] and old.newer["src"] == v2[:12]
    out = written(tools.write("summary.md", f'<bond:cite kind="quote" from="{hit.handle}">Six weeks</bond:cite>\n'))
    [warning] = out.warnings
    assert warning.startswith(hit.handle) and "'src' is at " in warning and old.newer["src"] in warning
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


def test_a_transcription_cites_the_version_its_producer_read_though_the_source_moved_meanwhile(bond):
    v1 = bond.ingest("fax", (FIXTURES / "fax_case_001.pdf").read_bytes(), format=Format.PDF).version

    class MovingTextLayer:  # the text layer, while someone files a new version of the fax
        producer, reads, output, citations = (TEXT_LAYER.producer, TEXT_LAYER.reads, TEXT_LAYER.output,
                                              TEXT_LAYER.citations)

        def transcribe(self, source: bytes, format: Format) -> bytes:
            bond.ingest("fax", (FIXTURES / "fax_case_002.pdf").read_bytes(), format=Format.PDF, base=v1)
            return TEXT_LAYER.transcribe(source, format)

    t = transcribe(bond, "fax", MovingTextLayer()).version
    assert {s.version for c in bond.citations(t) for s in c.supports} == {v1}
    assert [x.version for x in bond.transcriptions_of(v1)] == [t]


def test_recording_output_for_a_version_the_document_never_held_is_refused(bond):
    from bond.transcription import TranscriptionError, record

    v = bond.ingest("other", b"Not the fax.", format=Format.MARKDOWN).version
    bond.ingest("fax", (FIXTURES / "fax_case_001.pdf").read_bytes(), format=Format.PDF)
    with pytest.raises(TranscriptionError, match="never held"):
        record(bond, "fax", TEXT_LAYER, b"{}", source=v)


def test_reading_a_pdf_names_only_documents_whose_head_transcribes_it(bond):
    from bond.authoring import ToolError

    pdf1 = (FIXTURES / "fax_case_001.pdf").read_bytes()
    v1 = bond.ingest("fax", pdf1, format=Format.PDF).version
    transcribe(bond, "fax", TEXT_LAYER)
    v2 = bond.ingest("fax", (FIXTURES / "fax_case_002.pdf").read_bytes(), format=Format.PDF, base=v1).version
    transcribe(bond, "fax", TEXT_LAYER)  # fax.pypdf now transcribes v2
    bond.ingest("fax", pdf1, format=Format.PDF, base=v2)  # and the fax goes back to v1
    [t] = bond.transcriptions_of(v1)
    assert t.documents == ["fax.pypdf"] and t.current == []
    with pytest.raises(ToolError, match="no document transcribes its current version"):
        Tools(bond).read("fax")
