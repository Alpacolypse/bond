"""Locators: the canonical address of a range of content inside one version.

Each format addresses its content with one locator type, which several formats
may share. Locators of different versions are never compared; within a version,
containment and overlap are computable. One locator works in every format: the
whole version. Every locator addresses content, so every provenance edge runs
between spans of versions.

Each locator type declares what it addresses (Addresses), so rules elsewhere ask
"does this address text?" or "a region of a page?", never which format it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeGuard

from .jsonvalue import JsonObject, JsonValue


class Addresses(Enum):
    """What a locator type addresses."""

    TEXT = "text"  # text that can be read and quoted: a range of characters, a JSON value
    REGION = "region"  # a region of a visual page, which holds no text of its own
    WHOLE = "whole"  # the whole version


class LocatorType(Enum):
    TEXT = "text"  # Markdown / plain text
    JSON = "json"
    PDF = "pdf"
    CSV = "csv"  # a cell, row or column of a table
    VERSION = "version"  # the whole version


# [x0, y0, x1, y1], normalized 0-1, top-left origin.
BBox = tuple[float, float, float, float]
# [start, end): a code-point range.
Span = tuple[int, int]


class LocatorError(ValueError):
    """A locator is malformed, independent of any document."""


@dataclass(frozen=True)
class TextLocator:
    """Code-point range [start, end) over the decoded file."""

    start: int
    end: int

    type = LocatorType.TEXT
    addresses = Addresses.TEXT

    def __post_init__(self) -> None:
        _check_range((self.start, self.end))

    def to_dict(self) -> JsonObject:
        return {"type": "text", "start": self.start, "end": self.end}


@dataclass(frozen=True)
class JsonLocator:
    """A JSON value by its position, optionally refined by a code-point range inside a string value.

    The path holds one index per step from the root: in an object, the member's place in the version's bytes
    (0 for the first key written); in an array, the element's index. A version's bytes never change and keys
    are unique, so a path names one value exactly, as a JSON Pointer does, and translates to and from one
    losslessly given the version (JsonDoc.locate, JsonDoc.pointer). Unlike a pointer it holds no keys, so a
    stored locator carries none of the document's content.
    """

    path: tuple[int, ...] = ()
    span: Span | None = None  # a range inside a string value; None for the whole value

    type = LocatorType.JSON
    addresses = Addresses.TEXT

    def __post_init__(self) -> None:
        for step in self.path:
            if not _is_int(step) or step < 0:
                raise LocatorError(f"json path steps must be non-negative integers, got {step!r}")
        if self.span is not None:
            _check_range(self.span)

    def to_dict(self) -> JsonObject:
        d: JsonObject = {"type": "json", "path": list(self.path)}
        if self.span is not None:
            d["start"], d["end"] = self.span
        return d


@dataclass(frozen=True)
class CsvLocator:
    """A cell, a whole row or a whole column of a table, by 0-based index; a cell optionally refined by a range.

    A cell has both row and column, a row only row, a column only column. A range is a code-point range
    [start, end) inside a cell's decoded value.
    """

    row: int | None = None
    column: int | None = None
    span: Span | None = None  # a range inside a cell's value; None for the whole cell, row or column

    type = LocatorType.CSV
    addresses = Addresses.TEXT

    def __post_init__(self) -> None:
        if self.row is None and self.column is None:
            raise LocatorError("csv locator needs a row, a column or both (a cell)")
        if self.row is not None and (not _is_int(self.row) or self.row < 0):
            raise LocatorError(f"csv row must be a non-negative integer, got {self.row!r}")
        if self.column is not None and (not _is_int(self.column) or self.column < 0):
            raise LocatorError(f"csv column must be a non-negative integer, got {self.column!r}")
        if self.span is not None:
            if not self.is_cell:
                raise LocatorError("only a cell can have a range")
            _check_range(self.span)

    @property
    def is_cell(self) -> bool:
        return self.row is not None and self.column is not None

    def to_dict(self) -> JsonObject:
        d: JsonObject = {"type": "csv"}
        if self.row is not None:
            d["row"] = self.row
        if self.column is not None:
            d["column"] = self.column
        if self.span is not None:
            d["start"], d["end"] = self.span
        return d


@dataclass(frozen=True)
class PdfLocator:
    """Page index plus bbox [x0, y0, x1, y1], normalized 0-1, top-left origin."""

    page: int
    bbox: BBox

    type = LocatorType.PDF
    addresses = Addresses.REGION

    def __post_init__(self) -> None:
        if not _is_int(self.page) or self.page < 0:
            raise LocatorError(f"pdf page must be a non-negative integer, got {self.page!r}")
        if problem := bbox_problem(self.bbox):
            raise LocatorError(problem)

    def to_dict(self) -> JsonObject:
        # Stored and hashed as floats, however the box was written: (0, 0, 1, 1) is (0.0, 0.0, 1.0, 1.0).
        return {"type": "pdf", "page": self.page, "bbox": [float(v) for v in self.bbox]}


@dataclass(frozen=True)
class VersionLocator:
    """The whole version, no coordinates."""

    type = LocatorType.VERSION
    addresses = Addresses.WHOLE

    def to_dict(self) -> JsonObject:
        return {"type": "version"}


Locator = TextLocator | JsonLocator | CsvLocator | PdfLocator | VersionLocator
LOCATOR_TYPES: tuple[type[Locator], ...] = (TextLocator, JsonLocator, CsvLocator, PdfLocator, VersionLocator)

# The locators of a unit of text that a range narrows: the document's text, a JSON value, a table cell.
Ranged = TextLocator | JsonLocator | CsvLocator
RANGED: tuple[type[Ranged], ...] = (TextLocator, JsonLocator, CsvLocator)


def names_of(addresses: Addresses) -> str:
    """The locator types that address something, for messages: e.g. "text, json or csv"."""
    names = [t.type.value for t in LOCATOR_TYPES if t.addresses is addresses]
    return " or ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " or " + names[-1]


def range_of(locator: Ranged) -> Span | None:
    """The range a locator selects in its unit of text: a text range's own; a JSON value's or cell's span, if any."""
    match locator:
        case TextLocator():
            return locator.start, locator.end
        case JsonLocator() | CsvLocator():
            return locator.span


def narrowed(locator: Ranged, start: int, end: int) -> Ranged:
    """The same unit of text (the document's, a JSON value's, a cell's), narrowed to the range [start, end) of it."""
    match locator:
        case TextLocator():
            return TextLocator(start, end)
        case JsonLocator():
            return JsonLocator(locator.path, (start, end))
        case CsvLocator():
            return CsvLocator(locator.row, locator.column, (start, end))


# --- dict form ---------------------------------------------------------------

# Per locator type: the keys its dict form may have.
_KEYS: dict[str, frozenset[str]] = {
    "text": frozenset({"type", "start", "end"}),
    "json": frozenset({"type", "path", "start", "end"}),
    "csv": frozenset({"type", "row", "column", "start", "end"}),
    "pdf": frozenset({"type", "page", "bbox"}),
    "version": frozenset({"type"}),
}


def locator_from_dict(d: Mapping[str, JsonValue]) -> Locator:
    """Build a locator from its dict form. Raises LocatorError if malformed."""
    kind = d.get("type")
    if not isinstance(kind, str) or kind not in _KEYS:
        raise LocatorError(f"unknown locator type {kind!r}")
    if kind == "json" and "pointer" in d:
        raise LocatorError("a json locator is a positional path, {\"type\": \"json\", \"path\": [...]}, not a pointer; "
                           "translate a pointer in its version with JsonDoc.locate or details.locate_pointer")
    if extra := set(d) - _KEYS[kind]:
        raise LocatorError(f"unexpected locator keys: {sorted(extra)}")
    match kind:
        case "text":
            return TextLocator(_int(d, kind, "start"), _int(d, kind, "end"))
        case "json":
            path = _required(d, kind, "path")
            if not isinstance(path, list):
                raise LocatorError(f"json path must be a list of indexes, got {path!r}")
            return JsonLocator(tuple(_step(step) for step in path), _span(d, kind))
        case "csv":
            row = _int(d, kind, "row") if "row" in d else None
            column = _int(d, kind, "column") if "column" in d else None
            return CsvLocator(row, column, _span(d, kind))
        case "pdf":
            return PdfLocator(_int(d, kind, "page"), _bbox(_required(d, kind, "bbox")))
        case _:
            return VersionLocator()


def _required(d: Mapping[str, JsonValue], kind: str, key: str) -> JsonValue:
    if key not in d:
        raise LocatorError(f"{kind} locator is missing {key!r}")
    return d[key]


def _int(d: Mapping[str, JsonValue], kind: str, key: str) -> int:
    value = _required(d, kind, key)
    if not _is_int(value):
        raise LocatorError(f"{kind} locator's {key} must be an integer, got {value!r}")
    return value


def _step(step: JsonValue) -> int:
    if not _is_int(step):
        raise LocatorError(f"json path steps must be non-negative integers, got {step!r}")
    return step


def _span(d: Mapping[str, JsonValue], kind: str) -> Span | None:
    if "start" not in d and "end" not in d:
        return None
    if "start" not in d or "end" not in d:
        raise LocatorError(f"{kind} locator needs both start and end, or neither")
    return _int(d, kind, "start"), _int(d, kind, "end")


def _bbox(value: JsonValue) -> BBox:
    if not isinstance(value, list) or len(value) != 4:
        raise LocatorError(f"pdf bbox must be four numbers, got {value!r}")
    x0, y0, x1, y1 = value
    if not (_is_number(x0) and _is_number(y0) and _is_number(x1) and _is_number(y1)):
        raise LocatorError(f"pdf bbox must be four numbers, got {value!r}")
    return float(x0), float(y0), float(x1), float(y1)


# --- JSON Pointer (RFC 6901) -------------------------------------------------
# How people and agents name JSON values. Bond stores positional paths (JsonLocator) and translates at the edges.


def pointer_problem(pointer: str) -> str | None:
    """Why a string isn't a JSON Pointer; None if it is one."""
    if pointer and not pointer.startswith("/"):
        return f"json pointer must be empty or start with '/', got {pointer!r}"
    if any("~" in raw.replace("~0", "").replace("~1", "") for raw in pointer[1:].split("/")):
        return f"invalid '~' escape in json pointer {pointer!r}"
    return None


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """A JSON Pointer's tokens; raises LocatorError if it isn't one."""
    if problem := pointer_problem(pointer):
        raise LocatorError(problem)
    if pointer == "":
        return ()
    return tuple(raw.replace("~1", "/").replace("~0", "~") for raw in pointer[1:].split("/"))


def format_pointer(tokens: tuple[str, ...] | list[str]) -> str:
    return "".join("/" + t.replace("~", "~0").replace("/", "~1") for t in tokens)


# --- Containment and overlap -------------------------------------------------


def contains(outer: Locator, inner: Locator) -> bool:
    """True if inner lies entirely within outer. Both must be in the same version."""
    if isinstance(outer, VersionLocator):
        return True
    if isinstance(outer, TextLocator) and isinstance(inner, TextLocator):
        return _range_contains((outer.start, outer.end), (inner.start, inner.end))
    if isinstance(outer, JsonLocator) and isinstance(inner, JsonLocator):
        return _json_contains(outer, inner)
    if isinstance(outer, CsvLocator) and isinstance(inner, CsvLocator):
        return _csv_contains(outer, inner)
    if isinstance(outer, PdfLocator) and isinstance(inner, PdfLocator):
        return outer.page == inner.page and _box_contains(outer.bbox, inner.bbox)
    return False  # different types, or inner is the whole version


def overlaps(a: Locator, b: Locator) -> bool:
    """True if a and b share any content. Both must be in the same version."""
    if isinstance(a, VersionLocator) or isinstance(b, VersionLocator):
        return True
    if isinstance(a, TextLocator) and isinstance(b, TextLocator):
        return _ranges_overlap((a.start, a.end), (b.start, b.end))
    if isinstance(a, JsonLocator) and isinstance(b, JsonLocator):
        return _json_overlap(a, b)
    if isinstance(a, CsvLocator) and isinstance(b, CsvLocator):
        return _csv_overlap(a, b)
    if isinstance(a, PdfLocator) and isinstance(b, PdfLocator):
        return a.page == b.page and _boxes_overlap(a.bbox, b.bbox)
    return False


def _json_contains(outer: JsonLocator, inner: JsonLocator) -> bool:
    """inner's value is outer's or nested in it, and within outer's range if outer has one."""
    if inner.path[: len(outer.path)] != outer.path:
        return False
    if outer.span is None or len(inner.path) > len(outer.path):
        return True
    # Same value, and outer has a range: inner must be a range inside it.
    return inner.span is not None and _range_contains(outer.span, inner.span)


def _json_overlap(a: JsonLocator, b: JsonLocator) -> bool:
    """One value is nested in the other, and their ranges overlap if both are ranges in one string."""
    n = min(len(a.path), len(b.path))
    if a.path[:n] != b.path[:n]:
        return False
    if a.path != b.path or a.span is None or b.span is None:
        return True
    return _ranges_overlap(a.span, b.span)


def _csv_contains(outer: CsvLocator, inner: CsvLocator) -> bool:
    """A row holds its cells, a column its cells, a cell its ranges."""
    if outer.column is None:  # a whole row
        return inner.row == outer.row and (inner.is_cell or inner.column is None)
    if outer.row is None:  # a whole column
        return inner.column == outer.column and (inner.is_cell or inner.row is None)
    if (inner.row, inner.column) != (outer.row, outer.column):
        return False
    return outer.span is None or (inner.span is not None and _range_contains(outer.span, inner.span))


def _csv_overlap(a: CsvLocator, b: CsvLocator) -> bool:
    """A row and a column cross at a cell; a cell overlaps its row, its column and its own ranges."""
    if a.is_cell and b.is_cell:
        if (a.row, a.column) != (b.row, b.column):
            return False
        return a.span is None or b.span is None or _ranges_overlap(a.span, b.span)
    if a.row is not None and b.row is not None and a.row != b.row:
        return False
    if a.column is not None and b.column is not None and a.column != b.column:
        return False
    return True  # e.g. a row and a column cross; ragged rows are not consulted, so this may over-approximate


def _range_contains(outer: Span, inner: Span) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _ranges_overlap(a: Span, b: Span) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _box_contains(outer: BBox, inner: BBox) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _boxes_overlap(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# --- helpers -----------------------------------------------------------------


def _is_number(v: object) -> TypeGuard[int | float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def span_problem(span: Span) -> str | None:
    """Why a pair of numbers isn't a range [start, end); None if it is one."""
    start, end = span
    if not (_is_int(start) and _is_int(end)):
        return f"range bounds must be integers, got start={start!r} end={end!r}"
    if not 0 <= start < end:
        return f"range must satisfy 0 <= start < end, got [{start}, {end})"
    return None


def bbox_problem(bbox: BBox) -> str | None:
    """Why four numbers aren't a box on a page [x0, y0, x1, y1], normalized 0-1; None if they are one."""
    if len(bbox) != 4 or not all(_is_number(v) for v in bbox):
        return f"pdf bbox must be four numbers, got {bbox!r}"
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return f"pdf bbox must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, got {list(bbox)}"
    return None


def _check_range(span: Span) -> None:
    if problem := span_problem(span):
        raise LocatorError(problem)
