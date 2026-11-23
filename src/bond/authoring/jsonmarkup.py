"""JSON drafts: a JSON document with its citations written into it, the form agents read and write JSON in.

A value is cited whole by wrapping it in an object with two keys, "$bond" and "value":

    {"$bond": {"cite": {"kind": "quote", "from": "c3a863cb/result/chunks/1/blocks/4/content"}}, "value": "…"}

Everything Bond adds is inside "$bond", which mirrors the tags: "cite" is one citation object or a list of
them (its kind, its supports in `from` written as in a tag, an optional id and its attrs), and "anchor":
"<id>" names the value without citing it, so a citation can rest on it with `#<id>`. "$bond" has "cite",
"anchor" or both. Part of a string is cited with the same <bond:cite> and <bond:anchor> tags as in text
(markup.py), their attributes in single quotes so the JSON needs no escapes. `#id` rests on what a tag, a
cited value with that id or an anchor names. Keys aren't claims. "$bond" is
the one reserved key, and "<bond:" is reserved inside strings.

render and compile are inverses. Content is always stored in one layout (canonical), so a draft always
compiles to the same bytes, however it was laid out. Drafts and messages name values by their keys; the
citations compile gives, like every stored JSON locator, name them by position (see JsonLocator).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

from ..jsonvalue import JsonObject, JsonValue
from ..locators import JsonLocator, Locator, Span, format_pointer
from ..models import Citation, CitationKind, Ref
from ..parsers import Malformed, load_json
from . import markup
from .drafts import Compiled, Entry
from .markup import CLOSE_ANCHOR, CLOSE_TAG, OWN, Element, Unrenderable

BOND = "$bond"
VALUE = "value"
CITE = "cite"
ANCHOR = "anchor"
_WRAPPER = frozenset({BOND, VALUE})
_CONTAINER = frozenset({CITE, ANCHOR})
_KINDS = {k.value: k for k in CitationKind}

Path = tuple[str, ...]  # a value by its keys (pointer tokens), as drafts and messages name it
Position = tuple[int, ...]  # the same value by position, as its JsonLocator names it
Site = tuple[Path, Span | None]  # a value, or a span of a string value


def canonical(value: JsonValue) -> str:
    """The one layout JSON content is stored in: two-space indent, non-ASCII as is, a trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _where(path: Path) -> str:
    return format_pointer(path) if path else "the root"


# --- render ---------------------------------------------------------------------


def render(content: str, citations: Sequence[Citation], evidence: Callable[[Ref], str]) -> str:
    """The draft of a JSON version: each value cited whole wrapped with $bond, each span of a string tagged.

    citations are as written (supports inside the version SelfRefs). Raises Unrenderable if a citation's
    claim isn't a JSON value or a span of one (a claim on the whole version can't be shown), if the content
    uses a reserved key or "<bond:" in a string, or if two spans of a string partly overlap.
    """
    value = load_json(content)
    if isinstance(value, Malformed):
        raise Unrenderable(f"the content isn't valid JSON: {value.reason}")
    places, paths = _places(value)
    whole: dict[Path, list[int]] = {}
    inner: dict[Path, list[int]] = {}
    spans: dict[int, Span] = {}  # each citation of part of a string: the part
    for i, c in enumerate(citations):
        if not isinstance(c.claim, JsonLocator):
            raise Unrenderable(f"a {c.kind.value} citation claims a {c.claim.type.value} locator; in a JSON draft "
                               "every citation claims a value or part of a string")
        if c.claim.path not in paths:
            raise Unrenderable(f"a citation claims json path {list(c.claim.path)}, which isn't in the document")
        if c.claim.span is None:
            whole.setdefault(paths[c.claim.path], []).append(i)
        else:
            inner.setdefault(paths[c.claim.path], []).append(i)
            spans[i] = c.claim.span

    def site(locator: Locator) -> Site | None:
        if isinstance(locator, JsonLocator) and locator.path in paths:
            return paths[locator.path], locator.span
        return None

    def site_order(at: Site) -> tuple[int, ...]:
        start, end = at[1] or (-1, 0)
        return places.get(at[0], -1), start, end

    # The order tags appear in: values in document order; at each, the citations of it whole, then the tags in it.
    appear: list[int] = []
    for path in places:
        appear += whole.get(path, [])
        appear += sorted(inner.get(path, []), key=lambda i: (spans[i][0], -spans[i][1], i))
    names = markup.name(citations, appear, site, site_order)
    order = {i: n for n, i in enumerate(appear)}

    def sources(i: int) -> list[str]:
        return [names.token(s, evidence) for s in citations[i].supports]

    def citation_object(i: int) -> JsonObject:
        c = citations[i]
        obj: JsonObject = {"id": names.tags[i]} if i in names.tags else {}
        obj["kind"] = c.kind.value
        if c.supports:
            obj["from"] = " ".join(sources(i))
        for key, v in sorted(c.attrs.items()):
            if key in OWN:
                raise Unrenderable(f"a {c.kind.value} citation has an attr {key!r}, which a citation object can't "
                                   "hold")
            obj[key] = v
        return obj

    def string(path: Path, s: str) -> str:
        if markup.RESERVED in s:
            raise Unrenderable(f"the string at {_where(path)} contains {markup.RESERVED!r}, which is reserved for "
                               "citation tags")
        elements = [Element(*spans[i], order[i], markup.open_tag(citations[i], names.tags.get(i), sources(i),
                                                                    quote="'"), CLOSE_TAG)
                    for i in inner.get(path, [])]
        elements += [Element(*span, len(citations) + n, markup.open_anchor(a, quote="'"), CLOSE_ANCHOR)
                     for n, ((at, span), a) in enumerate(names.anchors.items()) if at == path and span]
        return markup.place_elements(s, elements) if elements else s

    def build(path: Path, v: JsonValue) -> JsonValue:
        out: JsonValue
        if isinstance(v, dict):
            if BOND in v:
                raise Unrenderable(f"the object at {_where(path)} has the key {BOND!r}, which is reserved for "
                                   "citations")
            out = {k: build(path + (k,), c) for k, c in v.items()}
        elif isinstance(v, list):
            out = [build(path + (str(n),), c) for n, c in enumerate(v)]
        elif isinstance(v, str):
            out = string(path, v)
        else:
            out = v
        if inner.get(path) and not isinstance(v, str):
            raise Unrenderable(f"a citation claims a range of {_where(path)}, which isn't a string")
        cites = [citation_object(i) for i in whole.get(path, [])]
        anchor = names.anchors.get((path, None))
        if not cites and anchor is None:
            return out
        bond: JsonObject = {}
        if cites:
            bond[CITE] = cites[0] if len(cites) == 1 else cites
        if anchor is not None:
            bond[ANCHOR] = anchor
        return {BOND: bond, VALUE: out}

    return canonical(build((), value))


def _places(value: JsonValue) -> tuple[dict[Path, int], dict[Position, Path]]:
    """Each value's place in the document, containers before what they hold, and each position's keys."""
    places: dict[Path, int] = {}
    paths: dict[Position, Path] = {}
    stack: list[tuple[Path, Position, JsonValue]] = [((), (), value)]
    while stack:
        path, position, v = stack.pop()
        places[path] = len(places)
        paths[position] = path
        if isinstance(v, dict):
            stack.extend((path + (k,), position + (n,), c) for n, (k, c) in reversed(list(enumerate(v.items()))))
        elif isinstance(v, list):
            stack.extend((path + (str(n),), position + (n,), c) for n, c in reversed(list(enumerate(v))))
    return places, paths


# --- compile --------------------------------------------------------------------


def compile(draft: str) -> Compiled:
    """The content a JSON draft stands for, in the canonical layout, and its citations as entries."""
    value = load_json(draft)
    if isinstance(value, Malformed):
        return Compiled("", [], {}, [f"the draft isn't valid JSON: {value.reason}"])
    entries: list[Entry] = []
    names: dict[str, Locator] = {}
    ids: list[str] = []
    issues: list[str] = []

    # A wrapper stands in for its value, so a value's position in the compiled content is its position here.
    def walk(path: Path, at: Position, v: JsonValue) -> JsonValue:
        if isinstance(v, dict):
            if BOND in v:
                return wrapper(path, at, v)
            return {k: walk(path + (k,), at + (n,), c) for n, (k, c) in enumerate(v.items())}
        if isinstance(v, list):
            return [walk(path + (str(n),), at + (n,), c) for n, c in enumerate(v)]
        if isinstance(v, str):
            return string(path, at, v)
        return v

    def wrapper(path: Path, at: Position, v: Mapping[str, JsonValue]) -> JsonValue:
        where = _where(path)
        if set(v) != _WRAPPER:
            issues.append(f'the object at {where} has {BOND!r}, so it wraps a value: its keys must be {BOND!r} and '
                          f'"{VALUE}" and nothing else (it has {", ".join(map(repr, v))})')
        inside = v.get(VALUE)
        if isinstance(inside, dict) and BOND in inside:
            issues.append(f"the value at {where} is wrapped twice; put everything in one {BOND!r}")
        bond = v[BOND]
        if not isinstance(bond, dict) or not bond or set(bond) - _CONTAINER:
            issues.append(f'{BOND!r} at {where} must be an object with "{CITE}", "{ANCHOR}" or both: '
                          f'{{"{CITE}": {{"kind": "…", "from": "…"}}, "{ANCHOR}": "…"}}')
            return walk(path, at, inside)
        if ANCHOR in bond:
            anchor = bond[ANCHOR]
            if not isinstance(anchor, str) or not anchor:
                issues.append(f'"{ANCHOR}" at {where} must be a non-empty string, the id other citations use')
            else:
                ids.append(anchor)
                names.setdefault(anchor, JsonLocator(at))
        if CITE in bond:
            cited = bond[CITE]
            cites = cited if isinstance(cited, list) else [cited]
            if not cites:
                issues.append(f'"{CITE}" at {where} is an empty list; give it a citation, or remove it')
            for obj in cites:
                entry = _entry(path, at, obj, issues)
                if entry is not None:
                    entries.append(entry)
                    if entry.id is not None:
                        ids.append(entry.id)
                        names.setdefault(entry.id, entry.claim)
        return walk(path, at, inside)

    def string(path: Path, at: Position, s: str) -> str:
        if "bond:" not in s:
            return s
        parsed = markup.parse(s)
        where = _where(path)
        issues.extend(f"in the string at {where}: {i}" for i in parsed.issues)
        for tag in parsed.tags:
            label = f"{markup.label(tag)} in {where}"
            if tag.span is None:
                issues.append(f'{label} is self-closing; to cite a whole value, wrap it: {{"{BOND}": {{"{CITE}": '
                              f'{{…}}}}, "{VALUE}": …}}')
                continue
            entries.append(Entry(tag.kind, JsonLocator(at, tag.span), list(tag.sources), dict(tag.attrs), label,
                                 tag.id))
            if tag.id is not None:
                ids.append(tag.id)
                names.setdefault(tag.id, JsonLocator(at, tag.span))
        for anchor, span in parsed.anchors.items():
            ids.append(anchor)
            names.setdefault(anchor, JsonLocator(at, span))
        return parsed.content

    content = canonical(walk((), (), value))
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(f'two citations or anchors have the id "{dup}"; ids must be unique in the draft')
    return Compiled(content, entries, names, list(dict.fromkeys(issues)))


def _entry(path: Path, at: Position, obj: JsonValue, issues: list[str]) -> Entry | None:
    """A citation object in "cite" as an entry claiming the value at path (position at), or None (with issues) if
    it isn't one."""
    where = _where(path)
    kinds = ", ".join(_KINDS)
    if not isinstance(obj, dict):
        issues.append(f'"{CITE}" at {where} must be a citation object, {{"kind": "…", "from": "…"}}, or a list of them')
        return None
    fields = dict(obj)
    kind_name, sources, tag_id = fields.pop("kind", None), fields.pop("from", ""), fields.pop("id", None)
    label = f"{CITE} at {where}" if tag_id is None else f'{CITE} "{tag_id}" at {where}'
    found = len(issues)
    if kind_name is None:
        issues.append(f"{label} has no kind; add \"kind\" (one of: {kinds})")
    elif not isinstance(kind_name, str) or kind_name not in _KINDS:
        issues.append(f"{label} has kind {kind_name!r}, which isn't a citation kind ({kinds})")
    if not isinstance(sources, str):
        issues.append(f'{label}: "from" must be a string of handles and #ids, separated by spaces')
    if tag_id is not None and (not isinstance(tag_id, str) or not tag_id):
        issues.append(f'{label}: "id" must be a non-empty string')
    attrs = {key: v for key, v in fields.items() if isinstance(v, str)}
    if bad := sorted(fields.keys() - attrs.keys()):
        issues.append(f"{label}: {', '.join(map(repr, bad))} must be a producer's id, a string")
    if len(issues) > found or not isinstance(kind_name, str) or not isinstance(sources, str) \
            or not (tag_id is None or isinstance(tag_id, str)):
        return None
    return Entry(_KINDS[kind_name], JsonLocator(at), sources.split(), attrs, label, tag_id)
