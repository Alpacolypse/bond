"""Per-format parsed documents: the format-specific code the core asks questions of.

Parsers store nothing. Each one turns a version's bytes into a parsed document
that answers the questions the core asks: is this locator valid here, what text
is at it, which claim units need citations, which parts of them no citation
covers, where does a phrase occur, and how to show a reader where something is.
Which parser reads which format is the format registry's business (formats.py).

Bytes that aren't well-formed in their format are Malformed, a value, not an
exception: content from outside is checked here, and a caller says what that
means (an issue for a write, a corrupt version for a read).
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from markdown_it import MarkdownIt

from .jsonvalue import JsonObject, JsonValue
from .locators import (
    RANGED,
    CsvLocator,
    JsonLocator,
    Locator,
    LocatorError,
    PdfLocator,
    TextLocator,
    VersionLocator,
    contains,
    format_pointer,
    narrowed,
    overlaps,
    parse_pointer,
    range_of,
)
from .normalize import find_all, normalize

WHOLE = VersionLocator()  # the whole version: where gaps are looked for unless a caller narrows it


@dataclass(frozen=True)
class Malformed:
    """Content that is not well-formed in its format, and why."""

    reason: str


@dataclass(frozen=True)
class LineContext:
    """Where a match in text is: its line, from 1, and the headings it sits under, outermost first."""

    line: int
    headings: tuple[str, ...]

    def to_dict(self) -> JsonObject:
        return {"line": self.line, "headings": list(self.headings)}


@dataclass(frozen=True)
class KeysContext:
    """Where a match in JSON is: the keys of the value it is in, from the root."""

    keys: tuple[str, ...]

    def to_dict(self) -> JsonObject:
        return {"keys": list(self.keys)}


@dataclass(frozen=True)
class CellContext:
    """Where a match in a table is: its cell's row and column, from 0."""

    row: int
    column: int

    def to_dict(self) -> JsonObject:
        return {"row": self.row, "column": self.column}


Context = LineContext | KeysContext | CellContext


@dataclass(frozen=True)
class Match:
    """A search match: the locator of the matched text, a display snippet, and where it is."""

    locator: Locator
    snippet: str
    context: Context


class ParsedDoc(Protocol):
    def check(self, locator: Locator) -> str | None:
        """None if the locator is valid in this document, else why it isn't."""

    def text(self, locator: Locator) -> str | None:
        """The text at a locator, if it addresses text."""

    def claims(self) -> list[Locator]:
        """Claim units: the parts of the document that must be cited."""

    def gaps(self, covered: list[Locator], within: Locator = WHOLE) -> list[Locator]:
        """Uncovered claim text inside within. Punctuation and syntax need no coverage."""

    def coverage_units(self, within: Locator) -> list[Locator]:
        """Content to inspect under format coverage rules, independent of declarations.

        Text formats omit syntax and punctuation-only content; PDF selections use geometry.
        These are not semantically detected factual claims.
        """

    def search(self, pattern: str) -> list[Match]:
        """Every match of a literal phrase, in document order."""

    def where(self, locator: Locator) -> str:
        """Where a locator is, as a reader would find it: a heading path and line, a JSON path, a page."""

    def excerpt(self, locator: Locator, pad: int = 40) -> tuple[str, str, str]:
        """(text before, text at, text after) a locator, up to pad characters each side; empty if no text."""

    def position(self, locator: Locator) -> tuple[float, ...]:
        """A sort key putting locators in reading order: the whole document first, then by where each starts."""


def decode(data: bytes) -> str | Malformed:
    """Bytes as UTF-8 text."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        return Malformed(f"content is not valid UTF-8: {e}")


_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"  # a byte-order mark


def _wrong_type(locator: Locator, fmt: str, allowed: str) -> str:
    return f"{locator.type.value} locator is not valid in a {fmt} document (use {allowed})"


def _snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    s = " ".join(text[a:b].split())
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def _literal_matches(pattern: str, text: str) -> list[tuple[int, int]]:
    return find_all(normalize(text), normalize(pattern).text)


_WORD = re.compile(r"\w")


def _uncovered(text: str, start: int, end: int, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Parts of text[start:end] outside every range that hold a word character, trimmed of whitespace.

    Punctuation needs no citation; words and numbers do.
    """
    out: list[tuple[int, int]] = []
    pos = start
    for s, e in sorted(ranges) + [(end, end)]:
        s, e = min(max(s, start), end), min(e, end)
        if s > pos:
            a, b = pos, s
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if _WORD.search(text, a, b):
                out.append((a, b))
        pos = max(pos, e)
    return out


def _scope_gaps(doc: ParsedDoc, gaps: list[Locator], within: Locator) -> list[Locator]:
    """Clip leaf gaps to a valid scope, then recheck for words: a scope may select only punctuation
    from a gap that contains words elsewhere. JSON and table gaps are always individual leaves/cells."""
    if isinstance(within, VersionLocator):
        return gaps
    result: list[Locator] = []
    for gap in gaps:
        if not overlaps(gap, within):
            continue
        if contains(within, gap):
            result.append(gap)
            continue
        # Overlapping without containing: within is a range of the same text, value or cell as the gap.
        if not isinstance(gap, RANGED) or not isinstance(within, RANGED):
            continue
        whole = (0, len(doc.text(gap) or ""))
        start, end = range_of(gap) or whole
        lo, hi = range_of(within) or whole
        clipped = narrowed(gap, max(start, lo), min(end, hi))
        if _WORD.search(doc.text(clipped) or ""):
            result.append(clipped)
    return result


# --- Markdown ----------------------------------------------------------------

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
# Block markers before a block's first character: blockquote, list, task box, ATX heading.
_LEAD_MARKERS = re.compile(
    r"(?:[ \t]*>)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?(?:#{1,6}(?=[ \t\r\n]|$))?"
)
_FRONT_MATTER = re.compile(r"\A---[ \t]*(?:\r\n|\r|\n).*?(?:\r\n|\r|\n)(?:---|\.\.\.)[ \t]*(?=\r\n|\r|\n|\Z)", re.S)

_md = MarkdownIt("commonmark").enable("table")


class MarkdownDoc:
    """All text is claim text. Claim units are blocks: paragraphs (including list item text),
    headings, code blocks, HTML blocks, table rows, front matter, and any other line holding text
    (e.g. a link reference definition).

    Only syntax is left out: block markers, list numbering, heading markers and fence lines.
    """

    @classmethod
    def from_bytes(cls, data: bytes) -> MarkdownDoc | Malformed:
        text = decode(data)
        return text if isinstance(text, Malformed) else cls(text)

    def __init__(self, text: str):
        self.content = text
        self.line_starts = [0] + [m.end() for m in _LINE_BREAK.finditer(text)]
        units: list[TextLocator] = []
        claimed: set[int] = set()  # lines inside a block, whether text or syntax

        def block(first: int, last: int, strip_markers: bool = False) -> None:
            if (unit := self._unit(first, last, strip_markers)) is not None:
                units.append(unit)
            claimed.update(range(first, last))

        source = text
        fm = _FRONT_MATTER.match(text)
        if fm:
            # Blank the front matter but keep its line breaks, so line numbers hold.
            blanked = "".join(c if c in "\r\n" else "" for c in fm.group(0))
            source = blanked + text[fm.end():]
            last = self._line_of(fm.end())
            block(1, last)  # between the delimiter lines
            claimed.update((0, last))
        tokens = _md.parse(source)

        self._headings: list[tuple[int, int, str]] = []  # (line, level, text)
        for i, tok in enumerate(tokens):
            if not tok.map:
                continue
            first, last = tok.map
            if tok.type == "heading_open":
                self._headings.append((first, int(tok.tag[1:]), tokens[i + 1].content))
                block(first, last, strip_markers=True)
            elif tok.type == "paragraph_open":
                block(first, last, strip_markers=True)
            elif tok.type in ("tr_open", "code_block", "html_block"):
                block(first, last)
            elif tok.type == "fence":
                closed = last - 1 > first and self._line(last - 1).strip().startswith(tok.markup)
                block(first + 1, last - 1 if closed else last)
                claimed.update((first, last - 1))
            elif tok.type in ("hr", "table_open"):
                claimed.update(range(first, last))  # a rule, or a table's delimiter row

        for line in range(len(self.line_starts)):
            if line not in claimed:
                block(line, line + 1)
        self._units = sorted(units, key=lambda u: u.start)

    def _line_span(self, first: int, last: int) -> tuple[int, int]:
        start = self.line_starts[first]
        end = self.line_starts[last] if last < len(self.line_starts) else len(self.content)
        return start, end

    def _line(self, line: int) -> str:
        start, end = self._line_span(line, line + 1)
        return self.content[start:end]

    def _unit(self, first: int, last: int, strip_markers: bool) -> TextLocator | None:
        if first >= last:
            return None
        start, end = self._line_span(first, last)
        if strip_markers and (markers := _LEAD_MARKERS.match(self.content, start, end)):
            start = markers.end()
        while start < end and self.content[start].isspace():
            start += 1
        while end > start and self.content[end - 1].isspace():
            end -= 1
        return TextLocator(start, end) if end > start else None

    def check(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return None
        if not isinstance(locator, TextLocator):
            return _wrong_type(locator, "markdown", "text")
        if locator.end > len(self.content):
            return f"range [{locator.start}, {locator.end}) ends past the document ({len(self.content)} characters)"
        return None

    def text(self, locator: Locator) -> str | None:
        if isinstance(locator, TextLocator):
            return self.content[locator.start : locator.end]
        if isinstance(locator, VersionLocator):
            return self.content
        return None

    def claims(self) -> list[Locator]:
        return list(self._units)

    def position(self, locator: Locator) -> tuple[float, ...]:
        if isinstance(locator, TextLocator):
            return (locator.start, -locator.end)
        return (-1,)

    def gaps(self, covered: list[Locator], within: Locator = WHOLE) -> list[Locator]:
        if any(isinstance(c, VersionLocator) for c in covered):
            return []
        ranges = [(c.start, c.end) for c in covered if isinstance(c, TextLocator)]
        return _scope_gaps(self, [
            TextLocator(s, e)
            for unit in self._units
            for s, e in _uncovered(self.content, unit.start, unit.end, ranges)
        ], within)

    def coverage_units(self, within: Locator) -> list[Locator]:
        return self.gaps([], within)

    def search(self, pattern: str) -> list[Match]:
        return [
            Match(TextLocator(s, e), _snippet(self.content, s, e), self._context(s))
            for s, e in _literal_matches(pattern, self.content)
        ]

    def where(self, locator: Locator) -> str:
        if not isinstance(locator, TextLocator):
            return ""
        context = self._context(locator.start)
        headings = " › ".join(context.headings)
        return f"{headings} · line {context.line}" if headings else f"line {context.line}"

    def excerpt(self, locator: Locator, pad: int = 40) -> tuple[str, str, str]:
        if isinstance(locator, VersionLocator):
            return "", self.content, ""
        if not isinstance(locator, TextLocator):
            return "", "", ""
        s, e = locator.start, locator.end
        return self.content[max(0, s - pad):s], self.content[s:e], self.content[e:e + pad]

    def _context(self, offset: int) -> LineContext:
        line = self._line_of(offset)
        path: list[tuple[int, str]] = []
        for hline, level, text in self._headings:
            if hline > line:
                break
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, text))
        return LineContext(line + 1, tuple(t for _, t in path))

    def _line_of(self, offset: int) -> int:
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo


# --- JSON --------------------------------------------------------------------

# How deep JSON may nest: deeper than any document needs, and shallow enough to walk without running out of stack.
MAX_JSON_DEPTH = 200


class _Rejected(Exception):
    """Raised inside json.loads by a hook, which has no other way to stop it."""


def _no_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    d: dict[str, JsonValue] = {}
    for k, v in pairs:
        if k in d:
            raise _Rejected(f"it has the key {k!r} twice in one object, so JSON pointers into it would be ambiguous")
        d[k] = v
    return d


def _reject_constant(name: str) -> None:
    raise _Rejected(f"{name} is not a JSON value")


def load_json(text: str) -> JsonValue | Malformed:
    """Strict JSON: no key twice in one object, no NaN or Infinity, and nested at most MAX_JSON_DEPTH deep."""
    try:
        value: JsonValue = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        return Malformed(f"{e.msg} at line {e.lineno}, column {e.colno}")
    except _Rejected as e:
        return Malformed(str(e))
    except RecursionError:
        return Malformed(f"it nests more than {MAX_JSON_DEPTH} deep")
    if _depth(value) > MAX_JSON_DEPTH:
        return Malformed(f"it nests more than {MAX_JSON_DEPTH} deep")
    return value


def _depth(value: JsonValue) -> int:
    """How many objects and arrays deep value nests: [[]] is 2, a scalar 0."""
    deepest = 0
    stack: list[tuple[JsonValue, int]] = [(value, 1)]
    while stack:
        v, depth = stack.pop()
        if isinstance(v, dict):
            stack.extend((c, depth + 1) for c in v.values())
        elif isinstance(v, list):
            stack.extend((c, depth + 1) for c in v)
        else:
            continue
        deepest = max(deepest, depth)
    return deepest


class Missing(Enum):
    """What a JSON path that names no value names."""

    MISSING = "missing"


MISSING = Missing.MISSING

# A value in a parsed JSON document: its path of positions, its pointer tokens, the value.
_Node = tuple[tuple[int, ...], tuple[str, ...], JsonValue]


class JsonDoc:
    """Claim units are non-null, non-empty leaf values, addressed by position (see JsonLocator).

    A value's position is its path of indexes from the root in this version's bytes. People and agents name
    values by JSON Pointer instead: locate turns a pointer into a locator and pointer turns one back, losslessly,
    because keys are unique and the bytes never change. Messages and display show pointers; what is stored and
    hashed is the path.
    """

    @classmethod
    def from_bytes(cls, data: bytes) -> JsonDoc | Malformed:
        text = decode(data)
        return text if isinstance(text, Malformed) else cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> JsonDoc | Malformed:
        value = load_json(text)
        return Malformed(f"invalid JSON: {value.reason}") if isinstance(value, Malformed) else cls(text, value)

    def __init__(self, text: str, value: JsonValue):
        """A JSON document: its text, and the value load_json read from it."""
        self.content = text
        self.value = value
        # Every value in document order, containers before what they hold.
        self._nodes: list[_Node] = []
        stack: list[_Node] = [((), (), self.value)]
        while stack:
            path, tokens, v = stack.pop()
            self._nodes.append((path, tokens, v))
            if isinstance(v, dict):
                stack.extend((path + (i,), tokens + (k,), c) for i, (k, c) in reversed(list(enumerate(v.items()))))
            elif isinstance(v, list):
                stack.extend((path + (i,), tokens + (str(i),), c) for i, c in reversed(list(enumerate(v))))
        self._by_path = {path: (place, tokens, v) for place, (path, tokens, v) in enumerate(self._nodes)}
        self._by_tokens = {tokens: path for path, tokens, _ in self._nodes}

    def _leaves(self) -> list[tuple[tuple[int, ...], JsonValue]]:
        """(path, value) of every scalar in the document, in document order."""
        return [(path, v) for path, _, v in self._nodes if not isinstance(v, (dict, list))]

    def _value(self, locator: JsonLocator) -> JsonValue | Missing:
        node = self._by_path.get(locator.path)
        return MISSING if node is None else node[2]

    def locate(self, pointer: str, span: tuple[int, int] | None = None) -> JsonLocator | None:
        """The locator of the value a JSON Pointer names, optionally a range in it; None if it names nothing here.

        Raises LocatorError for a malformed pointer or range."""
        path = self._by_tokens.get(parse_pointer(pointer))
        return None if path is None else JsonLocator(path, span)

    def pointer(self, locator: JsonLocator) -> str:
        """The JSON Pointer of the value a locator names; raises LocatorError if it names nothing here."""
        node = self._by_path.get(locator.path)
        if node is None:
            raise LocatorError(f"json path {list(locator.path)} does not exist in the document")
        return format_pointer(node[1])

    def check(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return None
        if not isinstance(locator, JsonLocator):
            return _wrong_type(locator, "json", "json")
        v = self._value(locator)
        if v is MISSING:
            return f"json path {list(locator.path)} does not exist in the document"
        if (span := locator.span) is not None:
            at = self.pointer(locator)
            if not isinstance(v, str):
                return f"a range needs a string value, but {at!r} is {type(v).__name__}"
            if span[1] > len(v):
                return f"range [{span[0]}, {span[1]}) ends past the string at {at!r} ({len(v)} characters)"
        return None

    def text(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return self.content
        if not isinstance(locator, JsonLocator):
            return None
        v = self._value(locator)
        if isinstance(v, str):
            return v if locator.span is None else v[locator.span[0] : locator.span[1]]
        if v is MISSING or v is None or isinstance(v, (dict, list)):
            return None
        return json.dumps(v)

    def claims(self) -> list[Locator]:
        return [JsonLocator(path) for path, v in self._leaves() if v is not None and v != ""]

    def gaps(self, covered: list[Locator], within: Locator = WHOLE) -> list[Locator]:
        if any(isinstance(c, VersionLocator) for c in covered):
            return []
        located = [c for c in covered if isinstance(c, JsonLocator)]
        gaps: list[Locator] = []
        for path, v in self._leaves():
            if v is None or v == "":
                continue
            # The whole value is cited, or an object or array holding it.
            if any(path[: len(c.path)] == c.path and (c.span is None or c.path != path) for c in located):
                continue
            ranges = [span for c in located if c.path == path and (span := c.span) is not None]
            if not ranges:  # nothing cites any of it: the gap is the whole value
                if not isinstance(v, str) or _WORD.search(v):
                    gaps.append(JsonLocator(path))
                continue
            if isinstance(v, str):
                gaps += [JsonLocator(path, gap) for gap in _uncovered(v, 0, len(v), ranges)]
        return _scope_gaps(self, gaps, within)

    def where(self, locator: Locator) -> str:
        if not isinstance(locator, JsonLocator) or locator.path not in self._by_path:
            return ""
        return self.pointer(locator)

    def position(self, locator: Locator) -> tuple[float, ...]:
        if not isinstance(locator, JsonLocator):
            return (-1,)
        span = locator.span or (0, -1)
        node = self._by_path.get(locator.path)
        return (-1 if node is None else node[0], span[0], -span[1])

    def excerpt(self, locator: Locator, pad: int = 40) -> tuple[str, str, str]:
        if isinstance(locator, JsonLocator) and (span := locator.span) is not None:
            value = self.text(JsonLocator(locator.path)) or ""
            s, e = span
            return value[max(0, s - pad):s], value[s:e], value[e:e + pad]
        return "", self.text(locator) or "", ""

    def coverage_units(self, within: Locator) -> list[Locator]:
        return self.gaps([], within)

    def search(self, pattern: str) -> list[Match]:
        out: list[Match] = []
        for path, v in self._leaves():
            if not isinstance(v, str):
                continue
            keys = KeysContext(self._by_path[path][1][:-1])
            out += [Match(JsonLocator(path, (s, e)), _snippet(v, s, e), keys) for s, e in _literal_matches(pattern, v)]
        return out


# --- CSV ---------------------------------------------------------------------


def read_rows(text: str, delimiter: str) -> list[list[str]] | Malformed:
    """A table's rows, read strictly; a leading UTF-8 byte-order mark is skipped, as spreadsheet exports write one."""
    body = text[1:] if text.startswith(_BOM) else text
    try:
        return [list(r) for r in csv.reader(io.StringIO(body, newline=""), delimiter=delimiter, strict=True)]
    except csv.Error as e:
        return Malformed(f"invalid {'TSV' if delimiter == chr(9) else 'CSV'}: {e}")


class CsvDoc:
    """A table: every row is data (nothing guesses at a header), and claim units are its non-empty cells.

    Cells are addressed by 0-based row and column and hold their decoded values: a quoted field's quotes
    and doubled quotes are syntax, not text. Rows may differ in length.
    """

    @classmethod
    def parse(cls, text: str, delimiter: str) -> CsvDoc | Malformed:
        rows = read_rows(text, delimiter)
        return rows if isinstance(rows, Malformed) else cls(text, rows, "tsv" if delimiter == "\t" else "csv")

    @classmethod
    def comma(cls, data: bytes) -> CsvDoc | Malformed:
        text = decode(data)
        return text if isinstance(text, Malformed) else cls.parse(text, ",")

    @classmethod
    def tab(cls, data: bytes) -> CsvDoc | Malformed:
        text = decode(data)
        return text if isinstance(text, Malformed) else cls.parse(text, "\t")

    def __init__(self, text: str, rows: Sequence[Sequence[str]], name: str):
        """A table: its text, the rows read_rows read from it, and its format's name for messages."""
        self.content = text
        self.rows = rows
        self.name = name
        self.width = max((len(r) for r in self.rows), default=0)

    def _cells(self) -> Iterator[tuple[int, int, str]]:
        for r, row in enumerate(self.rows):
            for c, value in enumerate(row):
                yield r, c, value

    def _cell(self, locator: CsvLocator) -> str | None:
        if locator.row is None or locator.column is None or locator.row >= len(self.rows):
            return None
        row = self.rows[locator.row]
        return row[locator.column] if locator.column < len(row) else None

    def check(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return None
        if not isinstance(locator, CsvLocator):
            return _wrong_type(locator, self.name, "csv")
        if locator.row is not None and locator.row >= len(self.rows):
            return f"row {locator.row} does not exist (the table has {len(self.rows)} rows, indexed from 0)"
        if locator.column is not None and locator.column >= self.width:
            return f"column {locator.column} does not exist (the table has {self.width} columns, indexed from 0)"
        if locator.row is not None and locator.column is not None:
            value = self._cell(locator)
            if value is None:
                return f"row {locator.row} has no column {locator.column} (it has {len(self.rows[locator.row])} cells)"
            if (span := locator.span) is not None and span[1] > len(value):
                return (f"range [{span[0]}, {span[1]}) ends past the cell at row {locator.row}, column {locator.column} "
                        f"({len(value)} characters)")
        return None

    def text(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return self.content
        if not isinstance(locator, CsvLocator) or not locator.is_cell:
            return None  # a row or column is many values, like a JSON object
        value = self._cell(locator)
        if value is None or locator.span is None:
            return value
        return value[locator.span[0]:locator.span[1]]

    def claims(self) -> list[Locator]:
        return [CsvLocator(r, c) for r, c, v in self._cells() if v != ""]

    def gaps(self, covered: list[Locator], within: Locator = WHOLE) -> list[Locator]:
        if any(isinstance(c, VersionLocator) for c in covered):
            return []
        located = [c for c in covered if isinstance(c, CsvLocator)]
        gaps: list[Locator] = []
        for r, c, value in self._cells():
            if value == "":
                continue
            cell = CsvLocator(r, c)
            if any(contains(loc, cell) for loc in located if loc.span is None):  # the cell, its row or its column
                continue
            ranges = [span for loc in located if (loc.row, loc.column) == (r, c) and (span := loc.span) is not None]
            gaps += [CsvLocator(r, c, gap) for gap in _uncovered(value, 0, len(value), ranges)] if ranges else (
                [cell] if _WORD.search(value) else [])
        return _scope_gaps(self, gaps, within)

    def coverage_units(self, within: Locator) -> list[Locator]:
        return self.gaps([], within)

    def search(self, pattern: str) -> list[Match]:
        out: list[Match] = []
        for r, c, value in self._cells():
            out += [Match(CsvLocator(r, c, (a, b)), _snippet(value, a, b), CellContext(r, c))
                    for a, b in _literal_matches(pattern, value)]
        return out

    def where(self, locator: Locator) -> str:
        if not isinstance(locator, CsvLocator):
            return ""
        parts = ([f"row {locator.row + 1}"] if locator.row is not None else []) + (
            [f"column {locator.column + 1}"] if locator.column is not None else [])
        return ", ".join(parts)

    def excerpt(self, locator: Locator, pad: int = 40) -> tuple[str, str, str]:
        if isinstance(locator, CsvLocator) and (span := locator.span) is not None:
            value = self._cell(CsvLocator(locator.row, locator.column)) or ""
            a, b = span
            return value[max(0, a - pad):a], value[a:b], value[b:b + pad]
        return "", self.text(locator) or "", ""

    def position(self, locator: Locator) -> tuple[float, ...]:
        if not isinstance(locator, CsvLocator):
            return (-1,)
        span = locator.span or (-1, 0)
        return (-1 if locator.row is None else locator.row, -1 if locator.column is None else locator.column,
                span[0], -span[1])


# --- PDF ---------------------------------------------------------------------


class PdfDoc:
    """A PDF holds no text claims and is not searched; its text is in documents that transcribe it."""

    @classmethod
    def from_bytes(cls, data: bytes) -> PdfDoc | Malformed:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        if not data.startswith(b"%PDF-"):
            return Malformed("not a PDF: missing %PDF- header")
        try:
            page_count = len(PdfReader(io.BytesIO(data), strict=False).pages)
        except (PyPdfError, ValueError, KeyError, TypeError) as e:  # what pypdf raises for a damaged file
            return Malformed(f"unreadable PDF: {e}")
        if page_count == 0:
            return Malformed("PDF has no pages")
        return cls(page_count)

    def __init__(self, page_count: int):
        self.page_count = page_count

    def check(self, locator: Locator) -> str | None:
        if isinstance(locator, VersionLocator):
            return None
        if not isinstance(locator, PdfLocator):
            return _wrong_type(locator, "pdf", "pdf")
        if locator.page >= self.page_count:
            return f"page {locator.page} does not exist (the PDF has {self.page_count} pages, indexed from 0)"
        return None

    def text(self, locator: Locator) -> str | None:
        return None

    def claims(self) -> list[Locator]:
        return []

    def gaps(self, covered: list[Locator], within: Locator = WHOLE) -> list[Locator]:
        return []

    def coverage_units(self, within: Locator) -> list[Locator]:
        if isinstance(within, PdfLocator):
            return [within]
        return [PdfLocator(page, (0.0, 0.0, 1.0, 1.0)) for page in range(self.page_count)]

    def search(self, pattern: str) -> list[Match]:
        return []

    def where(self, locator: Locator) -> str:
        return f"page {locator.page + 1}" if isinstance(locator, PdfLocator) else ""

    def position(self, locator: Locator) -> tuple[float, ...]:
        if isinstance(locator, PdfLocator):
            return (locator.page, locator.bbox[1], locator.bbox[0])
        return (-1,)

    def excerpt(self, locator: Locator, pad: int = 40) -> tuple[str, str, str]:
        return "", "", ""
