"""Handles: a readable, self-contained name for an exact span of a version, so citing needs no lookup table.

A handle is the version's id prefix followed by its locator, written out:

    88dd9008a1f3                                        the whole version
    88dd9008a1f3@305-393                                code points [305, 393) of a text version
    c3a863cb52e0/result/chunks/1/blocks/4/content       a JSON value
    c3a863cb52e0/result/chunks/1/blocks/4/content@6-33  code points [6, 33) inside that string
    9f1e2a7c04bd:p2:0.114,0.294,0.581,0.352             a region of a PDF page (pages from 1)
    4b7e0d21c96a:r3:c2                                  a table's cell: row 3, column 2 (both from 1)
    4b7e0d21c96a:r3:c2@0-5                              code points [0, 5) inside that cell
    4b7e0d21c96a:r3  4b7e0d21c96a:c2                    a whole row; a whole column

A JSON value is named by its JSON Pointer, written in its URI form (RFC 6901, section 6): characters outside
a safe set are percent-encoded, so a handle never contains a space, a quote or an unescaped "@". Bond stores
JSON locators as positional paths, so a handle's pointer is a Pointer here, and becomes a locator only in its
version (details.locate_pointer); the tools translate both ways. Both functions are pure; resolving the id
prefix to a version is the tools' business.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import quote, unquote

from ..locators import (
    CsvLocator,
    JsonLocator,
    Locator,
    LocatorError,
    PdfLocator,
    Span,
    TextLocator,
    VersionLocator,
    bbox_problem,
    pointer_problem,
    span_problem,
)

_SAFE = "/~-._!$&()*+,;=:"  # kept as is in a pointer; "@", spaces, quotes and the rest are percent-encoded
_RANGE = re.compile(r"@(\d+)-(\d+)")
_HANDLE = re.compile(r"(?P<version>[0-9a-f]{4,64})(?P<rest>.*)", re.S)
_NUMBER = r"(\d+(?:\.\d+)?(?:e-?\d+)?)"  # a coordinate as repr writes a float
_REGION = re.compile(rf":p(\d+):{_NUMBER},{_NUMBER},{_NUMBER},{_NUMBER}")
_TABLE = re.compile(r"(?::r(\d+))?(?::c(\d+))?(?:@(\d+)-(\d+))?")


@dataclass(frozen=True)
class Pointer:
    """A JSON value as a handle names it: by JSON Pointer, optionally a code-point range in it."""

    pointer: str
    span: Span | None = None

    def __post_init__(self) -> None:
        if problem := pointer_problem(self.pointer) or (None if self.span is None else span_problem(self.span)):
            raise LocatorError(problem)


class Handle(NamedTuple):
    """What a handle names: a version, by a prefix of its id, and a span of it."""

    prefix: str
    locator: Locator | Pointer


@dataclass(frozen=True)
class BadHandle:
    """A token that isn't a well-formed handle, and why."""

    message: str


def format_handle(version_prefix: str, locator: Locator | Pointer) -> str:
    """A handle for a locator; a JSON value is given as its Pointer, since its locator holds no keys."""
    match locator:
        case VersionLocator():
            return version_prefix
        case TextLocator():
            return f"{version_prefix}@{locator.start}-{locator.end}"
        case Pointer():
            # The root pointer is "", which would read as the whole version; its URI form is "#".
            pointer = quote(locator.pointer, safe=_SAFE) if locator.pointer else "#"
            return version_prefix + pointer + _span(locator.span)
        case JsonLocator():
            raise TypeError("a JSON value's handle names it by pointer: pass its Pointer (details.pointer)")
        case PdfLocator():
            return f"{version_prefix}:p{locator.page + 1}:" + ",".join(repr(float(v)) for v in locator.bbox)
        case CsvLocator():
            lines = (f":r{locator.row + 1}" if locator.row is not None else "") + (
                f":c{locator.column + 1}" if locator.column is not None else "")
            return version_prefix + lines + _span(locator.span)


def _span(span: Span | None) -> str:
    return "" if span is None else f"@{span[0]}-{span[1]}"


def parse_handle(token: str) -> Handle | BadHandle:
    """The version id prefix and locator a handle names, a JSON value as its Pointer; or why it isn't a handle."""
    m = _HANDLE.fullmatch(token)
    if m is None:
        return BadHandle(f"{token!r} isn't a handle: it should start with a version id, like the ones search() returns")
    located = _locator(m.group("rest"))
    if isinstance(located, BadHandle):
        return BadHandle(f"{token!r} isn't a handle: {located.message}")
    return Handle(m.group("version"), located)


def _locator(rest: str) -> Locator | Pointer | BadHandle:
    if rest == "":
        return VersionLocator()
    if r := _RANGE.fullmatch(rest):
        span = (int(r.group(1)), int(r.group(2)))
        return BadHandle(problem) if (problem := span_problem(span)) else TextLocator(*span)
    if rest.startswith(("/", "#")):
        written, _, after = rest.partition("@")  # "@" inside the pointer is percent-encoded
        pointer = "" if written == "#" else unquote(written)
        if problem := pointer_problem(pointer):
            return BadHandle(problem)
        if not after:
            return Pointer(pointer)
        if (r := _RANGE.fullmatch("@" + after)) is None:
            return BadHandle(f"the range after @ should be start-end, got {after!r}")
        span = (int(r.group(1)), int(r.group(2)))
        return BadHandle(problem) if (problem := span_problem(span)) else Pointer(pointer, span)
    if m := _REGION.fullmatch(rest):
        page = int(m.group(1))
        if page < 1:
            return BadHandle("pages are numbered from 1")
        bbox = (float(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5)))
        return BadHandle(problem) if (problem := bbox_problem(bbox)) else PdfLocator(page - 1, bbox)
    if m := _TABLE.fullmatch(rest):
        row, column = (None if g is None else int(g) - 1 for g in m.group(1, 2))
        if row == -1 or column == -1:
            return BadHandle("rows and columns are numbered from 1")
        if m.group(3) is None:
            return CsvLocator(row, column)
        span = (int(m.group(3)), int(m.group(4)))
        if row is None or column is None:
            return BadHandle("only a cell can have a range")
        return BadHandle(problem) if (problem := span_problem(span)) else CsvLocator(row, column, span)
    return BadHandle(f"{rest!r} after the version id isn't a locator (@start-end, /json/pointer, :pN:box, or "
                     ":rN:cN for a table)")
