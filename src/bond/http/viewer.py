"""Whole-workspace reads for the viewer, as the JSON shapes in ui/API.md.

Built on the core API: nothing here is a provenance rule. Display data (names,
formats, text, node ids) is added here, not by the core. Versions have no names
of their own; a version is shown under the first document it was filed in.

Locators are shown as stored, except that a JSON value is named by its pointer
(shown), and a locator sent back in that form is positioned in its version
(located): Bond stores JSON locators as positional paths, which hold no keys.

A version in a workspace this one links to is shown like one of its own, with the name of the workspace it is
in. A version no reachable workspace holds (its link was removed, say) is shown as what it is: an id that
something here rests on, with nothing else known about it here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TypedDict

from .. import details
from ..api import Bond
from ..details import covering_kinds
from ..formats import spec
from ..jsonvalue import JsonObject, JsonValue
from ..locators import JsonLocator, Locator, LocatorError, VersionLocator, locator_from_dict
from ..models import (
    CitationKind,
    DocumentId,
    DocumentRef,
    Hit,
    Ref,
    StoredCitation,
    Version,
    VersionId,
)

# A store with more versions than this is browsed document by document, not drawn whole.
GRAPH_LIMIT = 300


# --- the shapes of ui/API.md ---------------------------------------------------------


class RefView(TypedDict):
    version: str
    locator: JsonObject


class VersionSummary(TypedDict):
    version: str
    workspace: str  # the workspace it is filed in: this one, or one this one links to
    content_hash: str
    name: str
    title: str | None
    documents: list[str]
    format: str
    created_at: str
    version_index: int
    version_count: int
    latest: bool


class VersionDetail(VersionSummary):
    content: str | None
    siblings: list[VersionSummary]


class GraphNode(TypedDict):
    """A VersionSummary with its citation counts; for an unreachable version, its id with nulls."""

    version: str
    workspace: str | None
    content_hash: str | None
    name: str
    title: str | None
    documents: list[str]
    format: str | None
    created_at: str | None
    version_index: int
    version_count: int
    latest: bool
    citation_count: int
    self_citation_count: int
    is_source: bool
    unreachable: bool


GraphEdge = TypedDict("GraphEdge", {"id": str, "from": str, "to": str, "count": int, "kinds": dict[str, int]})


class Graph(TypedDict):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Neighborhood(Graph):
    center: str
    depth: int
    truncated: bool


class WorkspaceView(TypedDict):
    id: str
    name: str
    created_at: str


class Overview(TypedDict):
    workspace: WorkspaceView
    version_count: int
    document_count: int
    unreachable_count: int
    graph_limit: int


class DocumentRow(TypedDict):
    id: str
    name: str
    title: str | None
    head: str
    created_at: str
    format: str
    version_count: int
    updated_at: str


class DocumentsPage(TypedDict):
    total: int
    offset: int
    documents: list[DocumentRow]


class SupportView(TypedDict):
    version: str
    locator: JsonObject
    name: str
    format: str | None
    text: str | None
    unreachable: bool


class CitationView(TypedDict):
    id: str
    owner_version: str
    kind: str
    claim: JsonObject
    claim_text: str | None
    supports: list[SupportView]
    attrs: dict[str, str]


class UnitView(TypedDict):
    locator: JsonObject
    kinds: list[str]
    text: str | None


class Annotations(TypedDict):
    units: list[UnitView]
    gaps: list[JsonObject]


class DeclaredCitation(TypedDict):
    id: str
    owner_version: str
    kind: str
    claim: JsonObject
    supports: list[RefView]
    attrs: dict[str, str]


class DeclarationsView(TypedDict):
    ref: RefView
    citations: list[DeclaredCitation]
    undeclared: list[RefView]


class RegionView(TypedDict):
    ref: RefView
    status: str


class SupportIssueView(TypedDict):
    ref: RefView
    reason: str
    path: list[RefView]


class SupportReportView(TypedDict):
    ref: RefView
    status: str
    regions: list[RegionView]
    issues: list[SupportIssueView]


class TraceNode(TypedDict):
    id: str
    version: str
    name: str
    format: str | None
    locator: JsonObject
    kinds: list[str]
    text: str | None
    unreachable: bool


TraceEdgeView = TypedDict("TraceEdgeView", {"citation_id": str, "kind": str, "from": str, "to": list[str],
                                            "claim": JsonObject})


class TraceView(TypedDict):
    root: str
    nodes: list[TraceNode]
    edges: list[TraceEdgeView]


class SearchHitView(TypedDict):
    ref: RefView
    workspace: str
    documents: list[str]
    format: str
    snippet: str
    context: JsonObject
    kinds: list[str]
    declarations: DeclarationsView


class TextView(TypedDict):
    text: str | None


# --- reads -----------------------------------------------------------------------------------


class Summaries:
    """VersionSummary shapes, with each document's history read once.

    A version is shown under its home: the first document it was filed in, in the
    workspace that holds it (this one, or one it links to). Its
    place among siblings is its place in that document's history, where each
    version counts once, at its latest entry; so after a revert the reverted-to
    version is last, and latest.
    """

    def __init__(self, bond: Bond):
        self.bond = bond
        self._filed: dict[VersionId, list[DocumentRef]] = {}
        self._lineage: dict[DocumentId, list[VersionId]] = {}

    def home(self, v: VersionId) -> DocumentRef | None:
        return next(iter(self.filed(v)), None)

    def filed(self, v: VersionId) -> list[DocumentRef]:
        if v not in self._filed:
            self._filed[v] = details.filed_in(self.bond, v)
        return self._filed[v]

    def lineage(self, doc: DocumentRef, of: VersionId) -> list[VersionId]:
        """A document's versions, each once at its latest entry, oldest first: the head is last. The document is
        one of the workspace that holds version of."""
        if doc.id not in self._lineage:
            entries = [e.version for e in details.held_by(self.bond, of).filing.history(doc.id)]
            self._lineage[doc.id] = list(reversed(dict.fromkeys(reversed(entries))))
        return self._lineage[doc.id]

    def summary(self, version: Version) -> VersionSummary:
        home = self.home(version.id)
        lineage = self.lineage(home, version.id) if home else [version.id]
        i = lineage.index(version.id) + 1
        return {
            "version": version.id, "workspace": details.held_by(self.bond, version.id).workspace.name,
            "content_hash": version.content_hash, "format": version.format.value,
            "created_at": version.created_at, "name": home.name if home else version.id[:12],
            "title": home.title if home else None, "documents": [d.name for d in self.filed(version.id)],
            "version_index": i, "version_count": len(lineage), "latest": i == len(lineage),
        }

    def siblings(self, v: VersionId) -> list[VersionSummary]:
        """Summaries of every version in v's home document, oldest first (including v)."""
        home = self.home(v)
        return [self.summary(details.version(self.bond, s)) for s in (self.lineage(home, v) if home else [v])]

    def detail(self, version: Version) -> VersionDetail:
        """A version's summary, its text if it has text of its own, and its home document's versions."""
        summary = self.summary(version)
        text = self.bond.text(Ref(version.id, VersionLocator())) if spec(version.format).has_text else None
        return {**summary, "content": text, "siblings": self.siblings(version.id)}


def node(summary: VersionSummary, citation_count: int, self_citation_count: int, is_source: bool) -> GraphNode:
    """A version the workspace can read as a graph node."""
    return {**summary, "citation_count": citation_count, "self_citation_count": self_citation_count,
            "is_source": is_source, "unreachable": False}


def unreachable_node(v: VersionId) -> GraphNode:
    """A GraphNode for a version no reachable workspace holds: only its id is known here."""
    return {
        "version": v, "workspace": None, "content_hash": None, "name": v[:12], "title": None, "documents": [],
        "format": None, "created_at": None, "version_index": 1, "version_count": 1, "latest": True,
        "citation_count": 0, "self_citation_count": 0, "is_source": False, "unreachable": True,
    }


def read_node(bond: Bond, summaries: Summaries, v: VersionId, cited: list[StoredCitation]) -> GraphNode:
    """A graph node for a version bond can read, from the citations it was written with."""
    return node(summaries.summary(details.version(bond, v)), len(cited),
                sum(any(s.version == v for s in c.supports) for c in cited),
                any(c.kind is CitationKind.SOURCE and isinstance(c.claim, VersionLocator) for c in cited))


def citations_of(bond: Bond, v: VersionId) -> list[StoredCitation]:
    """The citations a version was written with, wherever bond reads it from; none if it is unreachable."""
    owner = details.owner(bond, v)
    return [] if owner is None else owner.records.citations(v)


def shown(bond: Bond, ref: Ref) -> JsonObject:
    """A ref's locator as the viewer shows it: as stored, but a JSON value by its pointer.

    A JSON value in an unreachable version is shown by its stored position: only its version knows the keys."""
    locator = ref.locator
    if not isinstance(locator, JsonLocator) or details.owner(bond, ref.version) is None:
        return locator.to_dict()
    d: JsonObject = {"type": "json", "pointer": details.pointer(bond, ref)}
    if locator.span is not None:
        d["start"], d["end"] = locator.span
    return d


def shown_ref(bond: Bond, ref: Ref) -> RefView:
    return {"version": ref.version, "locator": shown(bond, ref)}


def located(bond: Bond, version: VersionId, d: Mapping[str, JsonValue]) -> Locator:
    """A locator in version from the form shown gives; raises LocatorError or ValueError if it names nothing.

    A JSON value is named by its pointer only: a pointer from the wrong version fails, where a position would
    name some other value.
    """
    if d.get("type") != "json":
        return locator_from_dict(d)
    if extra := set(d) - {"type", "pointer", "start", "end"}:
        raise LocatorError(f"unexpected locator keys: {sorted(extra)}; a json value here is named by its "
                           '"pointer" (details.pointer translates a stored path)')
    pointer, start, end = d.get("pointer"), d.get("start"), d.get("end")
    if not isinstance(pointer, str):
        raise LocatorError('a json locator here names its value by "pointer", a JSON Pointer string')
    if start is None and end is None:
        return details.locate_pointer(bond, version, pointer).locator
    if not (isinstance(start, int) and isinstance(end, int)):
        raise LocatorError(f"a json locator's range is two integers, start and end; got {start!r} and {end!r}")
    return details.locate_pointer(bond, version, pointer, (start, end)).locator


def citations(bond: Bond, cited: list[StoredCitation]) -> list[CitationView]:
    """Citations with the text and version names a viewer shows beside them."""
    versions: dict[VersionId, Version | None] = {}
    names = Summaries(bond)

    def version(v: VersionId) -> Version | None:
        if v not in versions:
            versions[v] = None if details.owner(bond, v) is None else details.version(bond, v)
        return versions[v]

    def name(v: VersionId) -> str:
        home = names.home(v) if version(v) else None
        return home.name if home else v[:12]

    def snippet(r: Ref) -> str | None:
        # A whole version's text is the document itself; viewers already have it.
        return None if isinstance(r.locator, VersionLocator) or version(r.version) is None else bond.text(r)

    def support(s: Ref) -> SupportView:
        stored = version(s.version)
        return {"version": s.version, "locator": shown(bond, s), "name": name(s.version),
                "format": stored.format.value if stored else None, "text": snippet(s), "unreachable": stored is None}

    return [
        {"id": c.id, "owner_version": c.owner_version, "kind": c.kind.value,
         "claim": shown(bond, Ref(c.owner_version, c.claim)), "claim_text": snippet(Ref(c.owner_version, c.claim)),
         "supports": [support(s) for s in c.supports], "attrs": c.attrs}
        for c in cited
    ]


def graph(bond: Bond) -> Graph:
    """Every version as a node, with citations between versions aggregated into edges.

    Computed from citation rows alone, without parsing any version.
    """
    counts = bond.index.counts()
    summaries = Summaries(bond)
    nodes = []
    for version in details.versions(bond):
        c = counts.get(version.id)
        nodes.append(node(summaries.summary(version), c.citations if c else 0, c.self_supported if c else 0,
                          c.whole_source if c else False))
    for v in bond.index.outside():  # cited here, held by a workspace this one links to or by none
        nodes.append(unreachable_node(v) if details.owner(bond, v) is None else
                     read_node(bond, summaries, v, citations_of(bond, v)))
    edges: dict[tuple[str, str], GraphEdge] = {}
    for edge in bond.index.edges():
        a, b = edge.citing, edge.cited
        e = edges.setdefault((a, b), {"id": f"{a}->{b}", "from": a, "to": b, "count": 0, "kinds": {}})
        e["count"] += edge.citations
        e["kinds"][edge.kind.value] = edge.citations
    return {"nodes": nodes, "edges": list(edges.values())}


def overview(bond: Bond, graph_limit: int) -> Overview:
    """Which workspace this is and how big, so a viewer can choose between the whole graph and browsing
    documents. Only versions stored in the workspace itself are counted, besides the unreachable ones it cites."""
    ws = bond.workspace
    unreachable = [v for v in bond.index.outside() if details.owner(bond, v) is None]
    return {"workspace": {"id": ws.id, "name": ws.name, "created_at": ws.created_at},
            "version_count": len(details.versions(bond)), "document_count": len(bond.documents()),
            "unreachable_count": len(unreachable), "graph_limit": graph_limit}


def documents(bond: Bond, q: str = "", offset: int = 0, limit: int = 50) -> DocumentsPage:
    """A page of documents, by name, whose names or titles contain q (ignoring case), with how many match in all."""
    needle = q.casefold()
    matching = sorted(
        (d for d in bond.documents() if needle in d.name.casefold() or needle in (d.title or "").casefold()),
        key=lambda d: d.name,
    )
    page: list[DocumentRow] = []
    for d in matching[offset:offset + limit]:
        history = bond.filing.history(d.id)
        page.append({
            "id": d.id, "name": d.name, "title": d.title, "head": d.head, "created_at": d.created_at,
            "format": details.version(bond, d.head).format.value, "version_count": len({e.version for e in history}),
            "updated_at": history[-1].at,
        })
    return {"total": len(matching), "offset": offset, "documents": page}


def neighborhood(
    bond: Bond, center: VersionId, depth: int = 2, limit: int = 200, fanout: int = 25,
) -> Neighborhood:
    """The versions within depth citation hops of center, either way, as a graph like graph()'s.

    Breadth first from center, through supports (what a version rests on, here
    or in a linked workspace) and citers in this workspace (what rests on it).
    Every version next to center is included, but
    each one further out brings at most fanout new ones, so a hub (a policy
    every summary cites) doesn't fill the graph with its other citers. At most
    limit versions in all, nearest first; truncated says some were left out.
    Edges are the citations between included versions.
    """
    details.version(bond, center)  # UnknownVersion if there is none
    owned: dict[VersionId, list[StoredCitation]] = {}

    def cited_by_v(v: VersionId) -> list[StoredCitation]:
        if v not in owned:
            owned[v] = citations_of(bond, v)
        return owned[v]

    included, frontier, truncated = [center], [center], False
    seen = {center}
    for _ in range(depth):
        next_frontier = []
        for v in frontier:
            near = [s.version for c in cited_by_v(v) for s in c.supports]
            near += [c.owner_version for c in bond.index.supported_by(v)]
            brought = 0
            for n in dict.fromkeys(near):
                if n in seen:
                    continue
                if len(included) >= limit or (v != center and brought >= fanout):
                    truncated = True
                    break
                seen.add(n)
                included.append(n)
                next_frontier.append(n)
                brought += 1
        frontier = next_frontier

    summaries = Summaries(bond)
    nodes: list[GraphNode] = []
    edges: dict[tuple[str, str], GraphEdge] = {}
    for v in included:
        if details.owner(bond, v) is None:
            nodes.append(unreachable_node(v))
            continue
        cited = cited_by_v(v)
        nodes.append(read_node(bond, summaries, v, cited))
        for c in cited:
            for to in dict.fromkeys(s.version for s in c.supports):
                if to == v or to not in seen:
                    continue
                e = edges.setdefault((v, to), {"id": f"{v}->{to}", "from": v, "to": to, "count": 0, "kinds": {}})
                e["count"] += 1
                e["kinds"][c.kind.value] = e["kinds"].get(c.kind.value, 0) + 1
    return {"center": center, "depth": depth, "truncated": truncated, "nodes": nodes, "edges": list(edges.values())}


def claim_annotations(bond: Bond, version: VersionId) -> Annotations:
    """A version's claim units, each with the kinds of the citations covering it, and its gaps."""
    owned = bond.citations(version)
    return {
        "units": [
            {"locator": shown(bond, u), "kinds": _values(covering_kinds(owned, u.locator)), "text": bond.text(u)}
            for u in details.claim_units(bond, version)
        ],
        "gaps": [shown(bond, g) for g in bond.declarations(Ref(version, VersionLocator())).undeclared]
        if spec(details.version(bond, version).format).has_text else [],
    }


def declarations(bond: Bond, ref: Ref) -> DeclarationsView:
    """Direct declarations, with display locators (JSON Pointers rather than stored positions)."""
    p = bond.declarations(ref)
    return {"ref": shown_ref(bond, ref), "undeclared": [shown_ref(bond, r) for r in p.undeclared],
            "citations": [{"id": c.id, "owner_version": c.owner_version, "kind": c.kind.value,
                           "claim": shown(bond, Ref(c.owner_version, c.claim)),
                           "supports": [shown_ref(bond, r) for r in c.supports], "attrs": c.attrs}
                          for c in p.citations]}


def search_hits(bond: Bond, hits: list[Hit]) -> list[SearchHitView]:
    return [{"ref": shown_ref(bond, h.ref), "workspace": h.workspace, "documents": h.documents,
             "format": h.format.value,
             "snippet": h.snippet, "context": h.context.to_dict(), "kinds": _values(h.kinds),
             "declarations": declarations(bond, h.ref)} for h in hits]


def check_support(bond: Bond, ref: Ref, *, max_depth: int | None = None, limit: int = 1000) -> SupportReportView:
    report = bond.check_support(ref, max_depth=max_depth, limit=limit)

    def view(r: Ref) -> RefView:
        # A missing or invalid ancestor can't be shown by pointer: it is shown as stored.
        if details.locator_problem(bond, r) is not None:
            return {"version": r.version, "locator": r.locator.to_dict()}
        return shown_ref(bond, r)

    return {"ref": view(ref), "status": report.status.value,
            "regions": [{"ref": view(r.ref), "status": r.status.value} for r in report.regions],
            "issues": [{"ref": view(i.ref), "reason": i.reason.value, "path": [view(r) for r in i.path]}
                       for i in report.issues]}


def trace(bond: Bond, ref: Ref, max_depth: int | None = None) -> TraceView:
    """bond.trace with node ids and each node's name, format, covering kinds and text; an unreachable node has
    only its id and locator."""
    t = bond.trace(ref, max_depth)
    owned: dict[VersionId, list[StoredCitation]] = {}
    summaries = Summaries(bond)
    nodes: list[TraceNode] = []
    for r in t.nodes:
        v = r.version
        if v in t.unreachable:
            nodes.append({"id": node_id(bond, r), "version": v, "name": v[:12], "format": None,
                          "locator": shown(bond, r), "kinds": [], "text": None, "unreachable": True})
            continue
        if v not in owned:
            owned[v] = bond.citations(v)
        version = details.version(bond, v)
        home = summaries.home(v)
        nodes.append({
            "id": node_id(bond, r), "version": v, "name": home.name if home else v[:12],
            "format": version.format.value, "locator": shown(bond, r),
            "kinds": _values(covering_kinds(owned[v], r.locator)), "text": bond.text(r), "unreachable": False,
        })
    edges: list[TraceEdgeView] = [
        {"citation_id": e.citation.id, "kind": e.citation.kind.value, "from": node_id(bond, e.node),
         "to": [node_id(bond, s) for s in e.citation.supports],
         "claim": shown(bond, Ref(e.citation.owner_version, e.citation.claim))}
        for e in t.edges
    ]
    return {"root": node_id(bond, t.root), "nodes": nodes, "edges": edges}


def node_id(bond: Bond, ref: Ref) -> str:
    return f"{ref.version}:{json.dumps(shown(bond, ref), sort_keys=True, separators=(',', ':'))}"


def _values(kinds: frozenset[CitationKind]) -> list[str]:
    return sorted(k.value for k in kinds)
