"""Bounded structural support checks. Declarations and graph reachability are not semantic evidence quality."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Protocol

from .locators import RANGED, CsvLocator, JsonLocator, Locator, PdfLocator, contains, narrowed, overlaps, range_of
from .models import (
    CitationKind,
    Ref,
    StoredCitation,
    SupportIssue,
    SupportReason,
    SupportRegion,
    SupportReport,
    SupportStatus,
    TraceEdge,
    VersionId,
)
from .parsers import ParsedDoc


class Graph(Protocol):
    """What a support check reads: a workspace's versions, parsed, and the citations they were written with."""

    def parsed(self, version: VersionId) -> ParsedDoc | None:
        """A version, parsed; None if it is in neither the workspace nor any it links to."""
        ...

    def citations(self, version: VersionId) -> list[StoredCitation]:
        """The citations a version the workspace can read was written with."""
        ...


S = SupportStatus
R = SupportReason


def partition(doc: ParsedDoc, within: Locator, claims: list[Locator]) -> list[Locator]:
    """Disjoint inspectable regions at declaration boundaries. No semantic subdivision is inferred."""
    result: list[Locator] = []
    for unit in doc.coverage_units(within):
        relevant = [c for c in claims if overlaps(c, unit)]
        if isinstance(unit, PdfLocator):
            x0, y0, x1, y1 = unit.bbox
            xs, ys = {x0, x1}, {y0, y1}
            for c in relevant:
                if isinstance(c, PdfLocator):
                    xs.update(x for x in (c.bbox[0], c.bbox[2]) if x0 < x < x1)
                    ys.update(y for y in (c.bbox[1], c.bbox[3]) if y0 < y < y1)
            xx, yy = sorted(xs), sorted(ys)
            result.extend(PdfLocator(unit.page, (a, c, b, d))
                          for a, b in pairwise(xx) for c, d in pairwise(yy))
            continue
        if not isinstance(unit, RANGED):
            result.append(unit)
            continue
        text = doc.text(unit) or ""
        start, end = range_of(unit) or (0, len(text))
        # JSON numbers and booleans are indivisible; only strings accept ranges.
        if isinstance(unit, JsonLocator) and doc.check(JsonLocator(unit.path, (start, end))) is not None:
            result.append(unit)
            continue
        cuts = {start, end}
        for c in relevant:
            if isinstance(c, RANGED) and (bounds := range_of(c)) is not None:
                cuts.update(n for n in bounds if start < n < end)
        if len(cuts) == 2:
            # A range over a whole value is that value; take the claim's form so containment sees it covers it.
            whole = None if range_of(unit) is not None else next(
                (c for c in relevant if isinstance(c, (JsonLocator, CsvLocator)) and c.span == (start, end)), None)
            result.append(whole or unit)
            continue
        ordered = sorted(cuts)
        for a, b in pairwise(ordered):
            while a < b and text[a - start].isspace():
                a += 1
            while b > a and text[b - start - 1].isspace():
                b -= 1
            if a == b:
                continue
            result.extend(doc.coverage_units(narrowed(unit, a, b)))  # discard punctuation-only pieces and trim gaps
    return result


def aggregate(statuses: list[SupportStatus]) -> SupportStatus:
    values = set(statuses) - {S.NOT_APPLICABLE}
    if S.UNKNOWN in values:
        return S.UNKNOWN
    if not values:
        return S.NOT_APPLICABLE
    if values == {S.SUPPORTED}:
        return S.SUPPORTED
    if values == {S.UNSUPPORTED}:
        return S.UNSUPPORTED
    return S.PARTIALLY_SUPPORTED


@dataclass
class _Node:
    parts: list[tuple[Locator, list[StoredCitation]]] = field(default_factory=list)
    dependencies: list[Ref] = field(default_factory=list)
    fixed: SupportStatus | None = None
    cycle: bool = False


@dataclass(frozen=True)
class Walk:
    """One walk of a reference's provenance: the trace it followed and the support it found.

    max_depth bounds the hops of citations followed, for the trace and its support alike: a node max_depth hops
    from the root is reached but not stepped into, so if citations cover it, its support is unknown (depth_limit).
    nodes are the references reached, nearest first, each once at its shortest distance (those past limit left
    out); edges, a citation from each stepped node whose claim covers part of it; complete, whether neither
    budget cut the walk short.
    """

    nodes: list[Ref]
    edges: list[TraceEdge]
    complete: bool
    report: SupportReport


def check_support(graph: Graph, root: Ref, max_depth: int | None, limit: int) -> SupportReport:
    """The support report of one walk from root (see walk)."""
    return walk(graph, root, max_depth, limit).report


def walk(graph: Graph, root: Ref, max_depth: int | None, limit: int) -> Walk:
    """Read each distinct reference at most once, breadth first, recording the trace it follows; then evaluate the
    graph without recursion.

    Paths retain one route to each issue, avoiding exponential expansion of shared ancestry.
    The limit counts references, including the root, rather than versions or citation edges.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be non-negative or None")
    nodes = {root: _Node()}
    paths: dict[Ref, tuple[Ref, ...]] = {root: (root,)}
    pending = deque([(root, 0)])
    admitted = 1
    issues: list[SupportIssue] = []
    owned: dict[VersionId, list[StoredCitation]] = {}
    reached: list[Ref] = [root]  # the trace's nodes, nearest first
    edges: list[TraceEdge] = []
    complete = True

    def issue(ref: Ref, reason: SupportReason, via: Ref) -> None:
        path = paths[via]
        if path[-1] != ref:
            path += (ref,)
        item = SupportIssue(ref, reason, path)
        if item not in issues:
            issues.append(item)

    while pending:
        ref, depth = pending.popleft()
        node = nodes[ref]
        doc = graph.parsed(ref.version)
        if doc is None:
            node.fixed = S.UNKNOWN
            issue(ref, R.UNREACHABLE, ref)
            continue
        if doc.check(ref.locator) is not None:
            node.fixed = S.UNSUPPORTED
            issue(ref, R.INVALID_REFERENCE, ref)
            continue
        if ref.version not in owned:
            owned[ref.version] = graph.citations(ref.version)
        citations = [c for c in owned[ref.version] if overlaps(c.claim, ref.locator)]
        if citations and max_depth is not None and depth >= max_depth:  # reached, but not stepped into
            node.fixed = S.UNKNOWN
            issue(ref, R.DEPTH_LIMIT, ref)
            complete = False
            continue
        relevant: dict[str, StoredCitation] = {}
        for part in partition(doc, ref.locator, [c.claim for c in citations]):
            covering = [c for c in citations if contains(c.claim, part)]
            node.parts.append((part, covering))
            if not covering:
                issue(Ref(ref.version, part), R.UNDECLARED, ref)
            relevant.update((c.id, c) for c in covering)
        edges.extend(TraceEdge(ref, c) for c in relevant.values())
        for c in relevant.values():
            if c.kind is CitationKind.SOURCE:
                continue
            if c.kind is CitationKind.UNSOURCED:
                issue(Ref(ref.version, c.claim), R.UNSOURCED, ref)
                continue
            if not c.supports:
                issue(Ref(ref.version, c.claim), R.UNDECLARED, ref)
            for support in c.supports:
                if support not in node.dependencies:
                    node.dependencies.append(support)
                if support in nodes:
                    continue
                paths[support] = paths[ref] + (support,)
                nodes[support] = _Node()
                if admitted >= limit:
                    nodes[support].fixed = S.UNKNOWN
                    issue(support, R.LIMIT, support)
                    complete = False
                else:
                    admitted += 1
                    reached.append(support)
                    pending.append((support, depth + 1))

    # Iterative DFS finds cycles and produces dependency-before-parent evaluation order.
    order: list[Ref] = []
    finished: set[Ref] = set()
    active: list[Ref] = []  # the path being explored, root first
    stack = [(root, False)]
    while stack:
        ref, leaving = stack.pop()
        if leaving:
            active.pop()
            finished.add(ref)
            order.append(ref)
            continue
        if ref in finished:
            continue
        if ref in active:
            for member in active[active.index(ref):]:
                nodes[member].cycle = True
            issue(ref, R.CYCLE, ref)
            continue
        active.append(ref)
        stack.append((ref, True))
        stack.extend((d, False) for d in reversed(nodes[ref].dependencies))

    # Unknown propagates through cycles too; DFS order alone cannot propagate a back-edge's unknown.
    parents: dict[Ref, list[Ref]] = {}
    for ref, node in nodes.items():
        for dep in node.dependencies:
            parents.setdefault(dep, []).append(ref)
    unresolved = {ref for ref, node in nodes.items() if node.fixed is S.UNKNOWN}
    queue = deque(unresolved)
    while queue:
        for parent in parents.get(queue.popleft(), []):
            if parent not in unresolved:
                unresolved.add(parent)
                queue.append(parent)

    results: dict[Ref, SupportStatus] = {}
    regions: dict[Ref, tuple[SupportRegion, ...]] = {}
    for ref in order:
        node = nodes[ref]
        if node.fixed is not None:
            results[ref] = node.fixed
            regions[ref] = (SupportRegion(ref, node.fixed),)
            continue
        parts = []
        for loc, citations in node.parts:
            states = []
            if not citations or node.cycle:
                states.append(S.UNSUPPORTED)
            for c in citations:
                if c.kind is CitationKind.SOURCE:
                    states.append(S.SUPPORTED)
                elif c.kind is CitationKind.UNSOURCED or not c.supports:
                    states.append(S.UNSUPPORTED)
                else:
                    for dep in c.supports:
                        status = S.UNKNOWN if dep in unresolved else results.get(dep, S.UNSUPPORTED)
                        # An unresolved back edge without missing data is a detected cycle.
                        if status is S.NOT_APPLICABLE:
                            issue(dep, R.NO_REQUIRED_CONTENT, ref)
                        # A whole cited support must pass; its partial result cannot be mapped onto claim words.
                        states.append(S.SUPPORTED if status is S.SUPPORTED else
                                      S.UNKNOWN if status is S.UNKNOWN else S.UNSUPPORTED)
            status = (S.UNKNOWN if S.UNKNOWN in states else
                      S.UNSUPPORTED if S.UNSUPPORTED in states else S.SUPPORTED)
            parts.append(SupportRegion(Ref(ref.version, loc), status))
        regions[ref] = tuple(parts)
        results[ref] = aggregate([p.status for p in parts])
    return Walk(reached, edges, complete, SupportReport(root, results[root], regions[root], tuple(issues)))
