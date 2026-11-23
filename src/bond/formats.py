"""The format registry: everything the library knows about a format, in one place.

Each format has one entry: its media type, its file suffixes, how to recognise its bytes, the parser that
answers the core's questions about its content, and whether that content has text of its own to read,
search and quote. The rest of the library asks the registry (or the parsed document, or what a locator
addresses) and never branches on a particular format. Adding a format means a Format value, a parser and
an entry here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .models import Format
from .parsers import CsvDoc, JsonDoc, Malformed, MarkdownDoc, ParsedDoc, PdfDoc, decode, load_json


@dataclass(frozen=True)
class FormatSpec:
    format: Format
    media_type: str
    suffixes: tuple[str, ...]
    parse: Callable[[bytes], ParsedDoc | Malformed]
    looks_like: Callable[[bytes], bool]  # whether unlabelled bytes are this format; see sniff
    has_text: bool  # its content is text to read, search and quote; a PDF's is a picture of pages


def _is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def _is_json(data: bytes) -> bool:
    """A JSON object or array. A bare number or string is more likely a note than a JSON document."""
    text = decode(data)
    return not isinstance(text, Malformed) and isinstance(load_json(text), (dict, list))


def _never(data: bytes) -> bool:
    return False


def _always(data: bytes) -> bool:
    return True


# In the order sniff tries them; the last accepts anything.
FORMATS: dict[Format, FormatSpec] = {spec.format: spec for spec in (
    FormatSpec(Format.PDF, "application/pdf", (".pdf",), PdfDoc.from_bytes, _is_pdf, has_text=False),
    FormatSpec(Format.JSON, "application/json", (".json",), JsonDoc.from_bytes, _is_json, has_text=True),
    # A table is only ever named by its suffix or its writer: any text would pass for one, and the delimiter is
    # fixed by the format, never guessed.
    FormatSpec(Format.CSV, "text/csv; charset=utf-8", (".csv",), CsvDoc.comma, _never, has_text=True),
    FormatSpec(Format.TSV, "text/tab-separated-values; charset=utf-8", (".tsv", ".tab"), CsvDoc.tab, _never,
               has_text=True),
    FormatSpec(Format.MARKDOWN, "text/markdown; charset=utf-8", (".md", ".markdown", ".txt"), MarkdownDoc.from_bytes,
               _always, has_text=True),
)}


def spec(fmt: Format) -> FormatSpec:
    return FORMATS[fmt]


def parse(fmt: Format, data: bytes) -> ParsedDoc | Malformed:
    """A version's bytes, parsed in its format, or why they aren't well-formed in it."""
    return FORMATS[fmt].parse(data)


def from_suffix(suffix: str) -> Format | None:
    """The format a file suffix (".md", ".json", …) names, if any."""
    return next((s.format for s in FORMATS.values() if suffix.lower() in s.suffixes), None)


def sniff(data: bytes) -> Format:
    """The format of unlabelled bytes: the first whose looks_like accepts them."""
    return next(s.format for s in FORMATS.values() if s.looks_like(data))
