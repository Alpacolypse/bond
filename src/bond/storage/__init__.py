"""Storage: where a workspace's rows and bytes live, behind two protocols.

A Catalog holds the rows of one workspace: versions, their citations, and the
filing layer (documents and their histories). Blobs holds the bytes, named by
their hash. They are separate because a host pairs them independently: SQLite
with a local directory on one machine, Postgres with an object store in
production.

A catalog is opened on one workspace, and nothing it does reaches another: one
database can hold many workspaces (a case each), each a closed world. Versions
are named by their content everywhere, so the same version in two workspaces
has the same id, and a host with access to several can relate them (a policy
every case cites) without any mapping. Blobs are named by hash and may be
shared between workspaces, or kept apart, as the host chooses.

Backends store; they never judge. Hashing, check, kinds and policies stay in
Bond, so a backend can't weaken provenance, and verify recomputes every id from
what a backend returns.

Evidence never conflicts: blobs and versions are content-addressed, so two
writers of the same name write the same thing, and an insert that finds it
there already is done. The one point of coordination is filing, a
compare-and-swap: a document moves to a new version only from the head its
writer started from. Catalog.file does that atomically, with the store's own
unique constraints rather than locks, so it holds across threads, processes and
machines.

Bond writes a version's blob before its rows, so a crash can leave an orphan
blob but never a row without bytes, whichever backends are paired.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from ..models import (
    BondError,
    Citation,
    CitationKind,
    DocumentId,
    DocumentRef,
    Format,
    HistoryEntry,
    Imported,
    IssueCode,
    StoredCitation,
    Version,
    VersionId,
    Workspace,
)


@dataclass(frozen=True)
class NewVersion:
    """A checked version, ready to store: its id, blob and citations as written (supports inside it are SelfRefs)."""

    id: VersionId
    content_hash: str
    format: Format
    data: bytes
    citations: Sequence[Citation]


@dataclass(frozen=True)
class CitationCounts:
    citations: int
    self_supported: int  # citations with a support inside their own version
    whole_source: bool  # a whole-version SOURCE citation covers the version


class CitationEdge(NamedTuple):
    """Citations of one kind from one version resting on another. For the viewer."""

    citing: VersionId
    cited: VersionId
    kind: CitationKind
    citations: int


@dataclass(frozen=True)
class Filed:
    """A version filed in a document: the document after, and whether a history entry was added."""

    document: DocumentRef
    advanced: bool  # False when the version already was the document's head


@dataclass(frozen=True)
class Conflict:
    """Why a version can't be filed where asked: the document isn't where the writer thought it was."""

    code: IssueCode
    message: str
    hint: str


class BlobMissing(BondError):
    """No blob is stored under a content hash that a stored version names."""

    def __str__(self) -> str:
        return f"no blob {self.args[0]!r}"


class Blobs(Protocol):
    """Immutable bytes, named by the sha256 of their bytes. Safe to use from any thread."""

    def put(self, content_hash: str, data: bytes) -> None:
        """Store data under its hash, durably before returning. Idempotent: the same hash is always the same bytes."""
        ...

    def get(self, content_hash: str) -> bytes | None:
        """The bytes stored under a hash, or None if there are none."""
        ...


class Catalog(Protocol):
    """One workspace's versions, their citations, and documents. Safe to use from any thread.

    Every read and write is of the workspace the catalog was opened on. Evidence is insert-only: versions and
    citations are never updated or deleted. A document's title is the only thing that changes in
    place. Lists are oldest first.
    """

    workspace: Workspace  # the one workspace this catalog reads and writes

    # --- evidence ---

    def get_version(self, v: VersionId) -> Version | None: ...

    def put_version(self, new: NewVersion) -> None:
        """Store a version's rows without filing it in a document, unless it is stored already.

        For evidence brought in with what it rests on (an import's supports): versions that are cited, not filed.
        """
        ...

    def outside_versions(self) -> list[VersionId]:
        """Versions that supports here point at but that aren't in this workspace: its frontier.

        Only an import makes one; a write can only cite versions that are here.
        """
        ...

    def all_versions(self) -> list[Version]: ...

    def head_versions(self) -> list[Version]:
        """Versions that are some document's head."""
        ...

    def citations_of(self, v: VersionId) -> list[StoredCitation]:
        """The citations version v was written with; supports inside v are stored under v's id."""
        ...

    def citations_supported_by(self, v: VersionId) -> list[StoredCitation]:
        """Citations, from any version, with at least one support in version v."""
        ...

    def citation_counts(self) -> dict[VersionId, CitationCounts]:
        """Per version with citations: how many, how many rest on itself, and whether a whole-version SOURCE
        covers it. For the viewer."""
        ...

    def citation_edges(self) -> list[CitationEdge]:
        """Supports between distinct versions, counted by citing version, cited version and kind. For the viewer."""
        ...

    # --- filing ---

    def file(
        self, v: VersionId, document: str, base: VersionId | None, new: NewVersion | None = None,
        imported: Imported | None = None, origin: str | None = None,
    ) -> Filed | Conflict:
        """File v in document, first storing new (v's rows) unless it is stored already; all or nothing.

        Follows filing_problem, decided atomically with the append: if the document's head isn't base when the
        append happens, nothing is stored and the conflict is returned. Otherwise, the document after filing,
        and whether a history entry was added. imported and origin, if given, are recorded on the entry: where
        an import brought v from, and where an ingested file came from. Neither is part of v; filing v where it
        already is adds no entry, so records neither.
        """
        ...

    def get_document(self, name: str) -> DocumentRef | None: ...

    def all_documents(self) -> list[DocumentRef]: ...

    def documents_holding(self, v: VersionId) -> list[DocumentRef]:
        """Documents whose history holds version v."""
        ...

    def history(self, doc: DocumentId) -> list[HistoryEntry]: ...

    def retitle(self, name: str, title: str | None) -> DocumentRef:
        """Set or (with None) clear a document's title; raises UnknownDocument."""
        ...

    def close(self) -> None:
        """Release what the catalog opened itself; a connection pool its host passed in stays the host's."""
        ...


def filing_problem(doc: DocumentRef | None, document: str, base: VersionId | None, v: VersionId) -> Conflict | None:
    """Why v can't be filed in document (currently doc, None if there is none) from base; None if it can.

    Filing v where it already is the head is a no-op, whatever the base, so a
    retried write succeeds. Otherwise a new document needs no base, and an
    existing one needs its current head as base: a writer who started from an
    older head would overwrite someone else's version.
    """
    if doc is None:
        if base is None:
            return None
        return Conflict(IssueCode.UNKNOWN_DOCUMENT, f"no document is named {document!r}, but a base was given",
                        "leave base out to create the document")
    if doc.head == v:
        return None
    if base is None:
        return Conflict(IssueCode.DOCUMENT_EXISTS, f"document {document!r} already exists (head {doc.head[:12]}…)",
                        "pass its head as base to write its next version, or choose a new name")
    if base != doc.head:
        return stale(document, base, doc.head)
    return None


def stale(document: str, base: VersionId, head: VersionId | None) -> Conflict:
    """The STALE_BASE conflict: document moved past base (to head, if known) before this write was filed."""
    to = f" to {head[:12]}…" if head else ""
    return Conflict(IssueCode.STALE_BASE, f"document {document!r} has moved past base {base[:12]}…{to}",
                    "read its head, reconcile your changes with it, and write again with it as base")
