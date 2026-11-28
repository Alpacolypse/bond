"""Compiled drafts: the content to store and its citations as entries, before the tools resolve their supports."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..locators import Locator
from ..models import CitationKind


@dataclass(frozen=True)
class Entry:
    """One citation in a compiled draft, before its supports are resolved."""

    kind: CitationKind
    claim: Locator  # in the content being written
    sources: list[str]  # its supports as written: handles, or #id for a span this draft names
    producer: str | None  # the tool whose output the claim is, by id
    label: str  # how messages name it, e.g. '<bond:cite id="c2">'
    id: str | None = None  # what #id calls the span it claims


@dataclass(frozen=True)
class Compiled:
    """A compiled draft: the content to store, its citations in the order written, and the spans its ids name."""

    content: str
    entries: list[Entry]
    names: dict[str, Locator] = field(default_factory=dict)  # what #id refers to: a tag, an anchor, …
    issues: list[str] = field(default_factory=list)  # what is malformed, in the writer's terms
