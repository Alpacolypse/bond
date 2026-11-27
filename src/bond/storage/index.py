"""The index: reverse lookups over a workspace's records and filing, derived, never stored.

What cites a version, which documents hold it, what lies outside the workspace, and the viewer's counts and edges
are all functions of records and history entries. MemoryIndex keeps them in memory and catches up before each
query, so writes by other processes on the same store are seen at the next one. Nothing that decides whether a
write is accepted, or what a version cites, reads the index: a lagging one can only under-report.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager

from ..models import CitationKind, DocumentId, DocumentRef, StoredCitation, Version, VersionId
from . import CitationCounts, CitationEdge
from .filing import Claim, Filing
from .records import Record, Records
from .store import Key

_WHOLE_VERSION = {"type": "version"}


class _Scope(threading.local):
    depth = 0
    fresh = False


class MemoryIndex:
    """Reverse lookups, held in memory and caught up before each query.

    Catching up lists the workspace's record keys, its name claims and each document's entries after the last one
    seen, and reads only what is new. Records, claims and entries are written once, so what has been read is never
    stale. Inside view(), it catches up once, so one public query costs one catch-up however many lookups it makes.
    """

    def __init__(self, records: Records, filing: Filing):
        self.records = records
        self.filing = filing
        self._lock = threading.Lock()
        self._scope = _Scope()
        self._by_id: dict[VersionId, Record] = {}
        self._order: list[Record] = []  # oldest first
        self._supporting: dict[VersionId, list[StoredCitation]] = {}  # cited version -> citations resting on it
        self._claims: dict[Key, Claim] = {}
        self._claim_order: list[Claim] = []  # oldest first
        self._held: dict[DocumentId, list[VersionId]] = {}  # each document's entries' versions, in order
        self._holders: dict[VersionId, dict[DocumentId, None]] = {}  # version -> documents whose history holds it

    @contextmanager
    def view(self) -> Generator[None]:
        """Catch up once, for every lookup made inside: what one query reads is one state of the index."""
        scope = self._scope
        if scope.depth == 0:
            scope.fresh = False
        scope.depth += 1
        try:
            yield
        finally:
            scope.depth -= 1

    def refresh(self) -> None:
        """Read what was stored since the last refresh (once per view, inside one)."""
        scope = self._scope
        if scope.depth and scope.fresh:
            return
        with self._lock:
            new = [r for v in self.records.ids() if v not in self._by_id and (r := self.records.get(v)) is not None]
            if new:
                for r in new:
                    self._by_id[r.version.id] = r
                self._order = sorted(self._by_id.values(), key=lambda r: (r.version.created_at, r.version.id))
                supporting: dict[VersionId, list[StoredCitation]] = {}
                for r in self._order:
                    for c in r.citations:
                        for v in dict.fromkeys(s.version for s in c.supports):
                            supporting.setdefault(v, []).append(c)
                self._supporting = supporting
            claims = self.filing.new_claims(self._claims)
            if claims:
                self._claims.update(claims)
                self._claim_order = sorted(self._claims.values(), key=lambda c: (c.created_at, c.id))
            for claim in self._claim_order:
                held = self._held.setdefault(claim.id, [])
                for e in self.filing.history(claim.id, after=len(held)):
                    held.append(e.version)
                    self._holders.setdefault(e.version, {})[claim.id] = None
        scope.fresh = bool(scope.depth)

    def _records(self) -> list[Record]:
        self.refresh()
        return list(self._order)

    def versions(self) -> list[Version]:
        """Every version stored, oldest first."""
        return [r.version for r in self._records()]

    def holding(self, v: VersionId) -> list[DocumentRef]:
        """The documents whose history holds v, oldest first, each with its head now."""
        self.refresh()
        holders = self._holders.get(v, {})
        return [h.ref for c in self._claim_order if c.id in holders and (h := self.filing.head(c)) is not None]

    def supported_by(self, v: VersionId) -> list[StoredCitation]:
        """Citations, from any version, with at least one support in v."""
        self.refresh()
        return list(self._supporting.get(v, ()))

    def outside(self) -> list[VersionId]:
        """Versions supports here point at that aren't here: the frontier imports left."""
        records = self._records()
        here = {r.version.id for r in records}
        found: dict[VersionId, None] = {}
        for r in records:
            for c in r.citations:
                for s in c.supports:
                    if s.version not in here:
                        found.setdefault(s.version)
        return list(found)

    def counts(self) -> dict[VersionId, CitationCounts]:
        """Per version with citations: how many, how many rest on itself, and whether a whole-version SOURCE covers
        it. For the viewer."""
        return {
            r.version.id: CitationCounts(
                len(r.citations),
                sum(1 for c in r.citations if any(s.version == r.version.id for s in c.supports)),
                any(c.kind is CitationKind.SOURCE and c.claim.to_dict() == _WHOLE_VERSION for c in r.citations),
            )
            for r in self._records() if r.citations
        }

    def edges(self) -> list[CitationEdge]:
        """Supports between distinct versions, counted by citing version, cited version and kind. For the viewer."""
        found: dict[tuple[VersionId, VersionId, CitationKind], set[str]] = {}
        for r in self._records():
            for c in r.citations:
                for s in c.supports:
                    if s.version != r.version.id:
                        found.setdefault((r.version.id, s.version, c.kind), set()).add(c.id)
        return [CitationEdge(a, b, kind, len(ids)) for (a, b, kind), ids in found.items()]
