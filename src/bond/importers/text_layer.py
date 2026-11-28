"""A PDF's own text layer, read with pypdf, as its transcription (bond.transcription).

A PDF made from a document (a report, a letter, an export) carries its text; pypdf reads it with nothing but the
PDF's bytes: no network, no vendor, nothing leaves the machine. A scan or a fax is a picture of pages with no text
layer, so it has nothing to read here and needs an OCR transcriber; TEXT_LAYER refuses one rather than writing an
empty transcription.

The output is Bond's own JSON, one entry per page:

    {"transcriber": "pypdf", "version": "6.4.0", "pages": [{"page": 1, "text": "..."}, ...]}

Each page's text is a TRANSCRIPTION of the whole of that page. pypdf gives text in reading order but no box for
each line, so the region is the page, which certainly holds it, rather than a box that might not. Everything else
(the transcriber and its version, page numbers) is UNSOURCED. The version is pypdf's, so a newer pypdf that reads a
page differently writes the transcription's next version, and one that reads it the same still does: the output
says which pypdf read it.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from dataclasses import dataclass

from ..jsonvalue import JsonValue
from ..locators import JsonLocator, PdfLocator
from ..models import Citation, CitationKind, Format, Ref, Version
from ..parsers import JsonDoc, ParsedDoc
from ..transcription import TranscriptionError

PRODUCER = "pypdf"  # the producer its citations name
WHOLE_PAGE = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class TextLayer:
    """A Transcriber that reads a PDF's text layer with pypdf."""

    producer: str = PRODUCER
    reads: frozenset[Format] = frozenset({Format.PDF})
    output: Format = Format.JSON

    def transcribe(self, source: bytes, format: Format) -> bytes:
        import pypdf
        from pypdf.errors import PyPdfError

        try:
            reader = pypdf.PdfReader(io.BytesIO(source), strict=False)
            texts = [page.extract_text() or "" for page in reader.pages]
        except (PyPdfError, ValueError, KeyError, TypeError) as e:  # what pypdf raises for a damaged file
            raise TranscriptionError(f"pypdf couldn't read the PDF: {e}") from None
        if not any(text.strip() for text in texts):
            raise TranscriptionError("the PDF has no text layer to read (a scan or a fax is a picture of its pages); "
                                     "transcribe it with an OCR transcriber")
        pages = [{"page": n, "text": text} for n, text in enumerate(texts, 1)]
        output = {"transcriber": PRODUCER, "version": pypdf.__version__, "pages": pages}
        return (json.dumps(output, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    def citations(self, output: ParsedDoc, source: Version) -> list[Citation]:
        if not isinstance(output, JsonDoc):
            raise ValueError(f"not {PRODUCER} output: it should be JSON")
        pdf = source.id
        doc = _object(output.value, "the output")
        if doc.get("transcriber") != PRODUCER:
            raise ValueError(f"not {PRODUCER} output: its transcriber should be {PRODUCER!r}")
        cited = [Citation(CitationKind.UNSOURCED, JsonLocator((n,)), []) for n, key in enumerate(doc) if key != "pages"]
        pages = doc.get("pages")
        if not isinstance(pages, list):
            raise ValueError(f"not {PRODUCER} output: pages should be an array")
        pages_at = (list(doc).index("pages"),)
        for i, value in enumerate(pages):
            at, page = pages_at + (i,), _object(value, f"page entry {i}")
            number, text = page.get("page"), page.get("text")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1 or not isinstance(text, str):
                raise ValueError(f"not {PRODUCER} output: page entry {i} should have a page number from 1 and text")
            for n, key in enumerate(page):
                if key != "text":
                    cited.append(Citation(CitationKind.UNSOURCED, JsonLocator(at + (n,)), []))
                elif text.strip():  # an empty page has no text to read
                    cited.append(Citation(CitationKind.TRANSCRIPTION, JsonLocator(at + (n,)),
                                          [Ref(pdf, PdfLocator(number - 1, WHOLE_PAGE))], PRODUCER))
        return cited


TEXT_LAYER = TextLayer()


def _object(value: JsonValue, what: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"not {PRODUCER} output: {what} should be an object")
    return value
