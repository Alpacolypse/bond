"""Reducto's parse of a PDF, cited as its transcription (bond.transcription).

Bond doesn't call Reducto: whoever runs it (a deployment, an ingestion pipeline) records its parse with
bond.transcription.record(bond, document, REDUCTO, parse), or import_reducto(...) for a PDF not added yet. The
parse is stored unchanged and cited without converting any vendor value:

- a block's content is a TRANSCRIPTION of its region on the page;
- its type is a CLASSIFICATION of that region;
- its confidence is an ASSESSMENT resting on the block's transcribed text;
- everything else rests on no source: UNSOURCED. That is the job's metadata,
  a chunk's own fields (their text repeats the chunk's blocks), and a block's
  layout metadata (its bbox is already the PDF locator of its citations).

The core never sees the vendor schema. Locators are positional: a field's place
among its object's members in the parse's own bytes, which the importer counts
as it walks them.

Reducto bboxes are normalized 0-1 with a top-left origin, and pages are
1-indexed; Bond pages are 0-indexed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..api import Bond
from ..jsonvalue import JsonValue
from ..locators import JsonLocator, PdfLocator
from ..models import Citation, CitationKind, Format, Ref, SelfRef, Version, VersionId, WriteResult
from ..parsers import JsonDoc, Malformed, ParsedDoc
from ..transcription import TranscriptionError, record

TRANSCRIBER = "reducto"  # the producer its citations name

Path = tuple[int, ...]  # a value's position in the parse, as its JsonLocator holds it


@dataclass(frozen=True)
class ImportResult:
    pdf: WriteResult
    parse: WriteResult


@dataclass(frozen=True)
class Reducto:
    """Reducto's parse, as a Transcription: Bond reads it, and doesn't run Reducto.

    Reducto parses images too; reading one is a region locator for images in the core, then a bbox converted to it
    here (to_pdf_locator converts to a PDF's)."""

    producer: str = TRANSCRIBER
    reads: frozenset[Format] = frozenset({Format.PDF})
    output: Format = Format.JSON

    def citations(self, output: ParsedDoc, source: Version) -> list[Citation]:
        if not isinstance(output, JsonDoc):
            raise ValueError("not a Reducto parse: it should be JSON")
        return parse_citations(output, source.id)


REDUCTO = Reducto()


def import_reducto(bond: Bond, pdf: bytes, parse: bytes, name: str) -> ImportResult:
    """Ingest a PDF into document `name` and record its Reducto parse as `name.reducto`.

    Each block's fields are cited to the page. `parse` is the vendor's JSON
    output, stored byte-for-byte. Importing again writes the next version of
    either document whose content changed: the importer owns both, so it
    advances them from their current heads. Raises TranscriptionError if parse
    isn't JSON, before anything is stored, or isn't a Reducto parse.
    """
    parsed = JsonDoc.from_bytes(parse)
    if isinstance(parsed, Malformed):
        raise TranscriptionError(f"not a Reducto parse: {parsed.reason}")
    pdf_result = bond.ingest(name, pdf, format=Format.PDF, base=_head(bond, name))
    return ImportResult(pdf_result, record(bond, name, REDUCTO, parse, source=pdf_result.version))


def _head(bond: Bond, name: str) -> VersionId | None:
    """The head of the document with this name, or None if there is none yet."""
    doc = bond.find(name)
    return None if doc is None else doc.head


def parse_citations(parsed: JsonDoc, pdf: VersionId) -> list[Citation]:
    """The citations of a Reducto parse of the PDF at version pdf, parsed from the bytes to be stored."""
    doc = _object(parsed.value, "the parse")
    citations = _unsourced((), doc, {"result"})  # job metadata
    result_at = _member((), doc, "result")
    result = _object(doc["result"], "result")
    citations += _unsourced(result_at, result, {"chunks"})
    chunks_at = _member(result_at, result, "chunks")
    for ci, chunk_value in enumerate(_array(result["chunks"], "result.chunks")):
        chunk_at, chunk = chunks_at + (ci,), _object(chunk_value, f"chunk {ci}")
        citations += _unsourced(chunk_at, chunk, {"blocks"})
        blocks_at = _member(chunk_at, chunk, "blocks")
        for bi, block_value in enumerate(_array(chunk["blocks"], f"chunk {ci}'s blocks")):
            block = _object(block_value, f"chunk {ci}, block {bi}")
            region = Ref(pdf, to_pdf_locator(_object(block["bbox"], f"chunk {ci}, block {bi}'s bbox")))
            citations += _block(blocks_at + (bi,), block, region)
    return citations


def _block(path: Path, block: Mapping[str, JsonValue], region: Ref) -> list[Citation]:
    """A block's citations.

    content is read from the region, type is a label for the region, and
    confidence judges the text read, so it rests on content.
    """
    out = _unsourced(path, block, {"content", "type", "confidence"})  # layout metadata
    content = block.get("content")
    transcribed = isinstance(content, str) and bool(content.strip())
    if transcribed:
        out.append(Citation(CitationKind.TRANSCRIPTION, JsonLocator(_member(path, block, "content")), [region],
                            TRANSCRIBER))
    if "type" in block:
        out.append(Citation(CitationKind.CLASSIFICATION, JsonLocator(_member(path, block, "type")), [region],
                            TRANSCRIBER))
    if "confidence" in block:
        confidence = JsonLocator(_member(path, block, "confidence"))
        if not transcribed:
            out.append(Citation(CitationKind.UNSOURCED, confidence, []))  # confidence in no text
        else:
            out.append(Citation(CitationKind.ASSESSMENT, confidence,
                                [SelfRef(JsonLocator(_member(path, block, "content")))], TRANSCRIBER))
    return out


def _unsourced(path: Path, obj: Mapping[str, JsonValue], keep: set[str]) -> list[Citation]:
    """One UNSOURCED citation per field of obj (the object at path) except those in keep."""
    return [Citation(CitationKind.UNSOURCED, JsonLocator(path + (n,)), []) for n, k in enumerate(obj) if k not in keep]


def _member(path: Path, obj: Mapping[str, JsonValue], key: str) -> Path:
    """The position of obj's member key: its place among obj's members, in the bytes they were read from."""
    return path + (list(obj).index(key),)


def to_pdf_locator(bbox: Mapping[str, JsonValue]) -> PdfLocator:
    """Convert a Reducto bbox to Bond's normalized, top-left, 0-indexed form."""
    left, top = _number(bbox, "left"), _number(bbox, "top")
    x0, y0 = _clamp(left), _clamp(top)
    x1, y1 = _clamp(left + _number(bbox, "width")), _clamp(top + _number(bbox, "height"))
    page = int(_number(bbox, "original_page" if "original_page" in bbox else "page")) - 1
    return PdfLocator(page, (x0, y0, x1, y1))


def _object(value: JsonValue, what: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"not a Reducto parse: {what} should be an object")
    return value


def _array(value: JsonValue, what: str) -> Sequence[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"not a Reducto parse: {what} should be an array")
    return value


def _number(obj: Mapping[str, JsonValue], key: str) -> float:
    value = obj.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"not a Reducto parse: a bbox's {key} should be a number, got {value!r}")
    return float(value)


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))
