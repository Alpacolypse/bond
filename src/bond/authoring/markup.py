"""Markup: a document's text with its citations as inline tags, the form agents read and write.

Two tags. <bond:cite> is a citation: its claim is the text it wraps, and its `from` lists its supports.
<bond:anchor id="…"> names a span without citing anything, so another tag can rest on part of a sentence
with `from="#id"`. Tags and anchors nest but never partly overlap, and neither is ever stored.
Attribute values may be in double or single quotes.

render turns a version's content and citations into marked-up text; parse turns marked-up text back into
content, tags and anchors. Both are pure. What an evidence token in `from` means is the tools' business,
through the callback given to render.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field

from ..locators import Locator, TextLocator, VersionLocator
from ..models import Citation, CitationKind, Ref, SelfRef, Support

TAG = "bond:cite"
ANCHOR = "bond:anchor"
RESERVED = "<bond:"  # no document text may contain it, so tags are never ambiguous

_OPEN = re.compile(r"""<bond:(cite|anchor)((?:\s+[A-Za-z_][\w.-]*=(?:"[^"<>]*"|'[^'<>]*'))*)\s*(/?)>""")
_CLOSE = re.compile(r"</bond:(cite|anchor)\s*>")
_RESERVED = re.compile(r"</?bond:")
_ATTR = re.compile(r"""([A-Za-z_][\w.-]*)=(?:"([^"<>]*)"|'([^'<>]*)')""")

# Tag attributes that aren't the citation's attrs.
OWN = ("id", "kind", "from")


class Unrenderable(ValueError):
    """A version that can't be shown as markup: its text uses the reserved prefix, or its citations can't be tags."""


@dataclass
class Tag:
    """One <bond:cite> in marked-up text."""

    kind: CitationKind
    sources: list[str]  # the tokens in `from`
    attrs: dict[str, str]
    id: str | None
    span: tuple[int, int] | None  # [start, end) in the parsed content; None for a self-closing tag (the whole version)
    at: int  # its position in the marked-up text, for messages


@dataclass
class Parsed:
    content: str
    tags: list[Tag]  # in the order they open in the text
    anchors: dict[str, tuple[int, int]] = field(default_factory=dict)  # anchor id -> its span in the content
    issues: list[str] = field(default_factory=list)
    # With points: self-closing anchors, id -> their `on` (None for the unit they sit in, e.g. a table's cell).
    points: dict[str, str | None] = field(default_factory=dict)


# --- render ---------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """A tag or anchor to place around a span of the content."""

    start: int
    end: int
    order: int  # among elements with the same span: tags in the order they appear, then anchors
    open: str
    close: str


CLOSE_TAG = f"</{TAG}>"
CLOSE_ANCHOR = f"</{ANCHOR}>"


@dataclass(frozen=True)
class Names[K: Hashable]:
    """What a draft calls the things its supports inside the version rest on.

    A site is where a claim sits, in the draft's own terms (a span of text; a value, or a span of a string).
    A support on a site some tag claims is `#` that tag's id; on any other site, `#` an anchor placed there.
    Only tags something refers to get an id.
    """

    tags: dict[int, str]  # citation index -> its tag's id (c1, c2, … in the order tags appear)
    claims: dict[K, int]  # each claimed site -> the first tag (in order of appearance) claiming it
    anchors: dict[K, str]  # each site rested on that no tag claims -> its anchor's id (a1, a2, …), in order
    site: Callable[[Locator], K | None]

    def token(self, support: Support, evidence: Callable[[Ref], str]) -> str:
        """How a support is written in `from`."""
        if isinstance(support, Ref):
            return evidence(support)
        loc = support.locator
        key = self.site(loc)
        if key is None:
            raise Unrenderable(f"a support inside the document addresses a {loc.type.value} locator")
        return f"#{self.tags[self.claims[key]]}" if key in self.claims else f"#{self.anchors[key]}"


def name[K: Hashable](
    citations: Sequence[Citation], appear: Sequence[int], site: Callable[[Locator], K | None],
    anchor_order: Callable[[K], tuple[int, ...]],
) -> Names[K]:
    """The ids a draft gives tags and anchors, with citations' tags in the order they appear (appear)."""
    claims: dict[K, int] = {}
    for i in appear:
        if (key := site(citations[i].claim)) is not None:
            claims.setdefault(key, i)
    referenced: set[int] = set()
    rested: set[K] = set()
    for c in citations:
        for s in c.supports:
            if not isinstance(s, SelfRef):
                continue
            if (key := site(s.locator)) is None:
                raise Unrenderable(f"a support inside the document addresses a {s.locator.type.value} locator")
            elif key in claims:
                referenced.add(claims[key])
            else:
                rested.add(key)
    tags = {i: f"c{n}" for n, i in enumerate((i for i in appear if i in referenced), 1)}
    anchors = {key: f"a{n}" for n, key in enumerate(sorted(rested, key=anchor_order), 1)}
    return Names(tags, claims, anchors, site)


def attributes(fields: dict[str, str], quote: str = '"') -> str:
    return " ".join(f"{k}={quote}{html.escape(v, quote=True)}{quote}" for k, v in fields.items())


def open_tag(c: Citation, tag_id: str | None, sources: Sequence[str], whole: bool = False, quote: str = '"') -> str:
    """The opening <bond:cite> of a citation (self-closing if whole); raises Unrenderable for an attr a tag can't hold."""
    fields = {"id": tag_id} if tag_id else {}
    fields["kind"] = c.kind.value
    if sources:
        fields["from"] = " ".join(sources)
    for key, value in sorted(c.attrs.items()):
        if key in OWN:
            raise Unrenderable(f"a {c.kind.value} citation has an attr {key!r} that can't be a tag attribute")
        fields[key] = value
    return f"<{TAG} {attributes(fields, quote)}" + ("/>" if whole else ">")


def open_anchor(anchor_id: str, quote: str = '"') -> str:
    return f"<{ANCHOR} {attributes({'id': anchor_id}, quote)}>"


def render(content: str, citations: Sequence[Citation], evidence: Callable[[Ref], str]) -> str:
    """content with each citation as a tag around its claim, in the form agents read and edit.

    citations are as written, so supports inside the version are SELF. A support in another version
    becomes evidence(ref); one inside it is named as Names describes. Raises Unrenderable if content
    contains the reserved prefix, a claim isn't a text range or the whole version, or two spans partly
    overlap.
    """
    if RESERVED in content:
        raise Unrenderable(f"the text contains {RESERVED!r}, which is reserved for citation tags")
    spans: dict[int, tuple[int, int]] = {}
    for i, c in enumerate(citations):
        if isinstance(c.claim, TextLocator):
            spans[i] = (c.claim.start, c.claim.end)
        elif not isinstance(c.claim, VersionLocator):
            raise Unrenderable(f"a citation claims a {c.claim.type.value} locator; only text ranges can be tags")
    # The order tags appear in: whole-version tags first, then by where each claim starts, outermost first.
    appear = [i for i in range(len(citations)) if i not in spans]
    appear += sorted(spans, key=lambda i: (spans[i][0], -spans[i][1], i))
    names = name(citations, appear, _text_site, lambda span: span)  # anchors in the order their spans start

    def open_for(i: int) -> str:
        c = citations[i]
        return open_tag(c, names.tags.get(i), [names.token(s, evidence) for s in c.supports], whole=i not in spans)

    place = {i: n for n, i in enumerate(appear)}
    elements = [Element(s, e, place[i], open_for(i), CLOSE_TAG) for i, (s, e) in spans.items()]
    elements += [Element(s, e, len(citations) + n, open_anchor(a), CLOSE_ANCHOR)
                 for n, ((s, e), a) in enumerate(names.anchors.items())]
    whole = "".join(open_for(i) for i in appear if i not in spans)
    return whole + place_elements(content, elements)


def _text_site(locator: Locator) -> tuple[int, int] | None:
    return (locator.start, locator.end) if isinstance(locator, TextLocator) else None


def place_elements(content: str, elements: list[Element]) -> str:
    """content with each element's open and close around its span; raises Unrenderable if two partly overlap."""
    out: list[str] = []
    pos = 0
    stack: list[Element] = []

    def close_until(at: int) -> None:
        nonlocal pos
        while stack and stack[-1].end <= at:
            top = stack.pop()
            out.append(content[pos:top.end])
            out.append(top.close)
            pos = top.end

    for el in sorted(elements, key=lambda el: (el.start, -el.end, el.order)):
        close_until(el.start)
        if stack and el.end > stack[-1].end:
            raise Unrenderable("two citations (or a citation and a span another rests on) partly overlap, "
                               "so they can't both be tags")
        out.append(content[pos:el.start])
        out.append(el.open)
        pos = el.start
        stack.append(el)
    close_until(len(content))
    out.append(content[pos:])
    return "".join(out)


# --- parse ----------------------------------------------------------------------


def parse(text: str, points: bool = False) -> Parsed:
    """Split marked-up text into content, tags and anchors; issues say what is malformed, in the agent's terms.

    With points, a self-closing anchor is allowed: it names the unit it sits in (a table's cell, say), or the
    unit its `on` attribute says; the caller decides what those are.
    """
    content: list[str] = []
    length = 0  # of the content so far
    tags: list[Tag] = []
    anchors: dict[str, tuple[int, int]] = {}
    ids: list[str] = []
    issues: list[str] = []
    placed: dict[str, str | None] = {}
    # Open tags and anchors, innermost last: (the tag, or the anchor's id; where its span starts in the content).
    stack: list[tuple[Tag | str, int]] = []
    pos = 0
    for m in _RESERVED.finditer(text):
        if m.start() < pos:
            continue  # inside a tag already consumed
        content.append(text[pos:m.start()])
        length += m.start() - pos
        if close := _CLOSE.match(text, m.start()):
            which = close.group(1)
            if not stack:
                issues.append(f"a closing </bond:{which}> at {_where(text, m.start())} has no opening tag")
            elif (opened_which := _which(stack[-1][0])) != which:
                issues.append(f"</bond:{which}> at {_where(text, m.start())} closes a <bond:{opened_which}>: tags "
                              "and anchors must nest, never partly overlap")
                stack.pop()
            else:
                opened_item, start = stack.pop()
                name = label(opened_item) if isinstance(opened_item, Tag) else f'<{ANCHOR} id="{opened_item}">'
                if length == start:
                    issues.append(f"{name} at {_where(text, m.start())} wraps no text")
                if isinstance(opened_item, Tag):
                    opened_item.span = (start, length)
                else:
                    anchors[opened_item] = (start, length)
            pos = close.end()
        elif opened := _OPEN.match(text, m.start()):
            which, attr_text, self_closing = opened.group(1), opened.group(2), bool(opened.group(3))
            attrs = _attrs(attr_text, m.start(), issues, text)
            if which == "cite":
                tag = _tag(attrs, m.start(), issues, text)
                tags.append(tag)
                if tag.id is not None:
                    ids.append(tag.id)
                if not self_closing:  # a self-closing tag claims the whole version
                    stack.append((tag, length))
            else:
                on = attrs.pop("on", None) if points and self_closing else None
                anchor_id = _anchor_id(attrs, self_closing and not points, m.start(), issues, text)
                if anchor_id is not None:
                    ids.append(anchor_id)
                    if self_closing and points:
                        placed[anchor_id] = on
                    elif not self_closing:
                        stack.append((anchor_id, length))
            pos = opened.end()
        else:
            issues.append(f"{RESERVED!r} at {_where(text, m.start())} is reserved for citation tags, and this isn't a "
                          f'well-formed one; write <{TAG} kind="…" from="…">text</{TAG}> or '
                          f'<{ANCHOR} id="…">text</{ANCHOR}>')
            content.append(text[m.start():m.end()])
            length += m.end() - m.start()
            pos = m.end()
    content.append(text[pos:])
    for item, _ in stack:
        if isinstance(item, Tag):
            issues.append(f"{label(item)} at {_where(text, item.at)} is never closed")
            tags.remove(item)
        else:
            issues.append(f'<{ANCHOR} id="{item}"> is never closed')

    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(f'two tags or anchors have id="{dup}"; ids must be unique')
    return Parsed("".join(content), tags, anchors, issues, placed)


def _which(item: Tag | str) -> str:
    """What an open tag or anchor on the stack is: "cite" or "anchor"."""
    return "cite" if isinstance(item, Tag) else "anchor"


def _attrs(attr_text: str, at: int, issues: list[str], text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _ATTR.finditer(attr_text):
        key, value = m.group(1), m.group(2) if m.group(2) is not None else m.group(3)
        if key in attrs:
            issues.append(f"a tag at {_where(text, at)} has two {key!r} attributes")
        attrs[key] = html.unescape(value)
    return attrs


def _tag(attrs: dict[str, str], at: int, issues: list[str], text: str) -> Tag:
    kind_name = attrs.pop("kind", None)
    kinds = ", ".join(k.value for k in CitationKind)
    kind = CitationKind.PARAPHRASE  # a placeholder when the tag's kind is missing or wrong; the issue fails the write
    if kind_name is None:
        issues.append(f'a tag at {_where(text, at)} has no kind; add kind="…" (one of: {kinds})')
    elif kind_name not in {k.value for k in CitationKind}:
        issues.append(f"a tag at {_where(text, at)} has kind {kind_name!r}, which isn't a citation kind ({kinds})")
    else:
        kind = CitationKind(kind_name)
    sources = attrs.pop("from", "").split()
    tag_id = attrs.pop("id", None)
    return Tag(kind, sources, attrs, tag_id, None, at)


def _anchor_id(attrs: dict[str, str], self_closing: bool, at: int, issues: list[str], text: str) -> str | None:
    anchor_id = attrs.pop("id", None)
    if anchor_id is None:
        issues.append(f'an anchor at {_where(text, at)} has no id; write <{ANCHOR} id="…">text</{ANCHOR}>')
    if attrs:
        issues.append(f"an anchor at {_where(text, at)} has attributes besides id ({', '.join(sorted(attrs))}); "
                      "an anchor only names text, so it takes no kind or from")
    if self_closing:
        issues.append(f"an anchor at {_where(text, at)} wraps no text; an anchor names the text it wraps")
    return anchor_id


def _where(text: str, at: int) -> str:
    return f"line {text.count(chr(10), 0, at) + 1}"


def label(tag: Tag) -> str:
    """How a message names a tag: by its id, or else by its kind."""
    return f'<{TAG} id="{tag.id}">' if tag.id else f'<{TAG} kind="{tag.kind.value}">'
