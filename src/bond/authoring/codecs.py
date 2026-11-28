"""Codecs: how each writable format is shown as a draft, and how a draft is compiled back into content and citations.

A codec is the only format-specific part of authoring. render turns a version's content and citations (as
written, supports inside it SELF) into the draft a writer edits; compile turns a draft back into the content
to store and its citations, as entries the tools resolve. The two are inverses, so an unchanged draft compiles
to the same version. The tools look a document's codec up here and never branch on its format.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from ..locators import JsonLocator, Locator, TextLocator, VersionLocator
from ..models import Citation, Format, Ref
from ..parsers import JsonDoc, Malformed
from . import jsonmarkup, markup, tablemarkup
from .drafts import Compiled, Entry


class Codec(Protocol):
    how_to_cite: str  # what a message about uncited words tells the writer to do

    def render(self, content: str, citations: Sequence[Citation], handle: Callable[[Ref], str]) -> str:
        """The draft a writer edits; raises markup.Unrenderable if these citations can't be shown in it."""

    def compile(self, draft: str) -> Compiled:
        """The content and citations a draft stands for."""

    def where(self, content: str, locator: Locator) -> str:
        """Where a claim in content is, for a message; empty if the message's quoted words are enough."""


class TaggedText:
    """Text formats addressed by character ranges: citations are <bond:cite> tags around the text they claim."""

    how_to_cite = "wrap it in a <bond:cite> tag"

    def render(self, content: str, citations: Sequence[Citation], handle: Callable[[Ref], str]) -> str:
        return markup.render(content, citations, handle)

    def compile(self, draft: str) -> Compiled:
        parsed = markup.parse(draft)
        names: dict[str, Locator] = {a: TextLocator(*span) for a, span in parsed.anchors.items()}
        names.update({t.id: TextLocator(*t.span) for t in parsed.tags if t.id is not None and t.span is not None})
        entries = [
            Entry(t.kind, VersionLocator() if t.span is None else TextLocator(*t.span), list(t.sources), t.producer,
                  markup.label(t), t.id)
            for t in parsed.tags
        ]
        strays = [m for t in parsed.tags if (m := markup.stray(t, markup.label(t))) is not None]
        return Compiled(parsed.content, entries, names, list(parsed.issues) + strays)

    def where(self, content: str, locator: Locator) -> str:
        return ""


class JsonDraft:
    """JSON: a value cited whole is wrapped with $bond; part of a string is tagged as in text (see jsonmarkup.py)."""

    how_to_cite = (f'wrap the value: {{"{jsonmarkup.BOND}": {{"{jsonmarkup.CITE}": {{"kind": …, "from": …}}}}, '
                   f'"{jsonmarkup.VALUE}": …}}, or tag part of a string with <bond:cite>')

    def render(self, content: str, citations: Sequence[Citation], handle: Callable[[Ref], str]) -> str:
        return jsonmarkup.render(content, citations, handle)

    def compile(self, draft: str) -> Compiled:
        return jsonmarkup.compile(draft)

    def where(self, content: str, locator: Locator) -> str:
        doc = JsonDoc.parse(content)
        if not isinstance(locator, JsonLocator) or isinstance(doc, Malformed):
            return ""
        return doc.where(locator) or "the root"


class TableDraft:
    """CSV and TSV: the table itself, with citations as tags in its cells (see tablemarkup.py)."""

    how_to_cite = ("tag it in its cell: <bond:cite …>part of it</bond:cite>, or a self-closing <bond:cite …/> for the "
                   "whole cell (with on='row' or on='column' for its row or column)")

    def __init__(self, delimiter: str):
        self.delimiter = delimiter

    def render(self, content: str, citations: Sequence[Citation], handle: Callable[[Ref], str]) -> str:
        return tablemarkup.render(content, self.delimiter, citations, handle)

    def compile(self, draft: str) -> Compiled:
        return tablemarkup.compile(draft, self.delimiter)

    def where(self, content: str, locator: Locator) -> str:
        return tablemarkup.where(locator)


# The formats that can be written, and how. A new document is written in DEFAULT_FORMAT unless the writer says.
CODECS: dict[Format, Codec] = {
    Format.MARKDOWN: TaggedText(), Format.JSON: JsonDraft(), Format.CSV: TableDraft(","), Format.TSV: TableDraft("\t"),
}
DEFAULT_FORMAT = Format.MARKDOWN
