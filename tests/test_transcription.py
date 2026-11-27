"""Transcriptions: the text of a source with none of its own (a PDF) as a document citing where it was read.

Bond runs only the PDF's own text layer (pypdf); another producer's output, Reducto's say, is recorded as it was
returned. Either way the transcription is filed beside the PDF, its text cites the page, and what an agent writes
from it traces down to that page.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
from pypdf import PdfWriter

from bond import Bond, Citation, CitationKind, Format, PdfLocator, Ref, TextLocator, VersionLocator, WriteError
from bond.authoring import Tools
from bond.importers import TRANSCRIPTIONS, transcribers_for
from bond.importers.reducto import REDUCTO, import_reducto
from bond.importers.text_layer import TEXT_LAYER
from bond.models import SupportStatus, UnknownDocument, Version
from bond.parsers import ParsedDoc
from bond.policies import PRODUCERS, Policy
from bond.transcription import Transcriber, TranscriptionError, record, transcribe, transcription_name
from conftest import FIXTURES, written

FAX = (FIXTURES / "fax_case_001.pdf").read_bytes()
PARSE = (FIXTURES / "fax_case_001.reducto.json").read_bytes()


def scan(pages: int = 2) -> bytes:
    """A PDF whose pages hold no text, as a scan's or a fax's don't."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(612, 792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_a_pdfs_text_layer_is_its_transcription_and_what_cites_it_traces_to_the_page(bond: Bond):
    pdf = bond.ingest("fax", FAX, format=Format.PDF).version
    result = transcribe(bond, "fax", TEXT_LAYER)
    assert result.advanced and bond.document("fax.pypdf").head == result.version
    output = json.loads(bond.blob(result.version))
    assert output["transcriber"] == "pypdf" and [p["page"] for p in output["pages"]] == [1, 2]
    assert bond.verify(result.version) == []  # under the default policy, which knows pypdf

    tools = Tools(bond)
    [hit] = tools.search("Notice given", document="fax.pypdf")
    assert hit.support is SupportStatus.SUPPORTED and hit.kinds == [CitationKind.TRANSCRIPTION]
    assert hit.where.startswith("page 2")
    note = written(tools.write("note", f'<bond:cite kind="quote" from="{hit.handle}">Notice given</bond:cite>'))
    assert note.support is SupportStatus.SUPPORTED
    trace = bond.trace(Ref(bond.document("note").head, VersionLocator()))
    [page] = [n for n in trace.nodes if n.version == pdf]
    assert page.locator == PdfLocator(1, (0.0, 0.0, 1.0, 1.0))  # the whole of page 2
    [read] = [e.citation for e in trace.edges if e.citation.kind is CitationKind.TRANSCRIPTION]
    assert read.attrs == {"transcriber": "pypdf"}


def test_transcribing_again_changes_nothing_and_a_new_pdf_advances_its_transcription(bond: Bond):
    bond.ingest("fax", FAX, format=Format.PDF)
    first = transcribe(bond, "fax", TEXT_LAYER)
    assert not transcribe(bond, "fax", TEXT_LAYER).advanced
    bond.ingest("fax", (FIXTURES / "fax_case_002.pdf").read_bytes(), format=Format.PDF, base=bond.document("fax").head)
    second = transcribe(bond, "fax", TEXT_LAYER)
    assert second.advanced and [h.version for h in bond.history("fax.pypdf")] == [first.version, second.version]


def test_a_scan_has_no_text_layer_and_nothing_is_written(bond: Bond):
    bond.ingest("scan", scan(), format=Format.PDF)
    with pytest.raises(TranscriptionError, match="no text layer"):
        transcribe(bond, "scan", TEXT_LAYER)
    assert [d.name for d in bond.documents()] == ["scan"]


def test_only_a_source_with_no_text_of_its_own_is_transcribed(bond: Bond):
    bond.ingest("note", b"Six weeks of written notice.", format=Format.MARKDOWN)
    with pytest.raises(TranscriptionError, match="'note' is markdown, which has text of its own"):
        transcribe(bond, "note", TEXT_LAYER)
    with pytest.raises(UnknownDocument):
        transcribe(bond, "nothing", TEXT_LAYER)


def test_output_that_isnt_the_producers_is_refused_and_nothing_is_written(bond: Bond):
    bond.ingest("fax", FAX, format=Format.PDF)
    for output, problem in ((b"not json", "not pypdf output"),
                            (PARSE, "its transcriber should be 'pypdf'"),
                            (json.dumps({"transcriber": "pypdf", "pages": [{"page": 0, "text": "x"}]}).encode(),
                             "page number from 1")):
        with pytest.raises(TranscriptionError, match=problem):
            record(bond, "fax", TEXT_LAYER, output)
    with pytest.raises(WriteError, match="page 8 does not exist"):  # the PDF has 2: the write's own checks
        record(bond, "fax", TEXT_LAYER, json.dumps({"transcriber": "pypdf", "pages": [{"page": 9, "text": "x"}]}).encode())
    assert [d.name for d in bond.documents()] == ["fax"]


def test_a_vendors_output_is_recorded_as_returned_and_is_the_same_however_it_came_in(bond: Bond, tmp_path):
    bond.ingest("fax_case_001", FAX, format=Format.PDF)
    recorded = record(bond, "fax_case_001", REDUCTO, PARSE)
    assert bond.blob(recorded.version) == PARSE  # byte-for-byte
    with Bond.local(tmp_path / "other") as other:
        imported = import_reducto(other, FAX, PARSE, "fax_case_001")
        assert imported.parse.version == recorded.version  # the same version, recorded or imported
    assert transcription_name("fax_case_001", "reducto") == "fax_case_001.reducto"


def test_bond_runs_only_the_text_layer_and_finds_it_by_the_sources_format():
    assert set(TRANSCRIPTIONS) == {"pypdf", "reducto"}
    assert transcribers_for(Format.PDF) == [TEXT_LAYER] and transcribers_for(Format.MARKDOWN) == []
    assert not isinstance(REDUCTO, Transcriber)  # Bond reads Reducto's parse; it doesn't run Reducto


@dataclass(frozen=True)
class Reader:
    """A made-up producer: it reads the formats given and stores its output as Markdown, one line per page, each
    line read from the whole of its page. Nothing in Bond knows it."""

    producer: str = "acme-ocr"
    reads: frozenset[Format] = frozenset({Format.PDF})
    output: Format = Format.MARKDOWN

    def citations(self, output: ParsedDoc, source: Version) -> list[Citation]:
        text = output.text(VersionLocator()) or ""
        cited, at = [], 0
        for page, line in enumerate(text.splitlines(keepends=True)):
            if line.strip():
                cited.append(Citation(CitationKind.TRANSCRIPTION, TextLocator(at, at + len(line.rstrip("\n"))),
                                      [Ref(source.id, PdfLocator(page, (0.0, 0.0, 1.0, 1.0)))],
                                      {"transcriber": self.producer}))
            at += len(line)
        return cited


def test_a_new_producer_is_one_transcription_in_any_output_format(bond: Bond):
    pdf = bond.ingest("fax", FAX, format=Format.PDF).version
    result = record(bond, "fax", Reader(), b"Cover sheet, to the intake unit.\nIntake note: ceiling leak.\n")
    assert bond.document("fax.acme-ocr").head == result.version
    assert bond.verify(result.version, Policy(producers=PRODUCERS | {"acme-ocr"})) == []
    [hit] = Tools(bond).search("ceiling leak", document="fax.acme-ocr")
    assert hit.support is SupportStatus.SUPPORTED and hit.where.startswith("page 2")
    with pytest.raises(WriteError, match="page 2 does not exist"):  # the PDF has 2 pages, from 0
        record(bond, "fax", Reader(), b"one\ntwo\nthree\n")
    with pytest.raises(TranscriptionError, match="acme-ocr doesn't read pdf \\(it reads csv\\)"):
        record(bond, "fax", Reader(reads=frozenset({Format.CSV})), b"x\n")
    assert [h.version for h in bond.history("fax")] == [pdf]


def test_an_agent_reading_an_untranscribed_pdf_is_told_why_it_cant(bond: Bond):
    from bond.authoring import ToolError

    bond.ingest("fax", FAX, format=Format.PDF)
    tools = Tools(bond)
    with pytest.raises(ToolError, match="no document transcribes its current version yet"):
        tools.read("fax")
    transcribe(bond, "fax", TEXT_LAYER)
    with pytest.raises(ToolError, match="documents that transcribe it: 'fax.pypdf'"):
        tools.read("fax")
