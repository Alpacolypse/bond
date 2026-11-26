"""Storage: where a workspace's keys and bytes live, behind two protocols.

A Store holds keys, each written once: every workspace's records, names, history entries and titles (see
bond.storage.store; what Bond keeps in them is bond.storage.records, bond.storage.filing and
bond.storage.workspace, with reverse lookups derived in bond.storage.index). Blobs holds the bytes, named by their hash.
They are separate because a host pairs them independently: SQLite with a local directory on one machine, Postgres
or DynamoDB with an object store in production.

One store holds any number of workspaces, each a closed world: a Bond is opened on one, and its keys all start with
that workspace's id. Versions are named by their content everywhere, so the same version in two workspaces has the
same id, and a host with access to several can relate them (a policy every case cites) without any mapping. Blobs
are named by hash and may be shared between workspaces, or kept apart, as the host chooses.

Backends store; they never judge. Hashing, checks, kinds, policies and the rules of filing stay in Bond, so a
backend can't weaken provenance, and verify recomputes every id from what a backend returns.

Evidence never conflicts: blobs and records are content-addressed, so two writers of the same name write the same
thing, and a create that finds it there already is done. The one point of coordination is filing: a document
advances by creating its next history entry, and of two writers who started from the same head, the store lets one
create it.

Bond writes a version's blob before its record, and its record before the entry that files it, so a crash can leave
an orphan blob or an unfiled version but never a head without its record or a record without bytes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from ..models import (
    BondError,
    Citation,
    CitationKind,
    DocumentRef,
    Format,
    IssueCode,
    VersionId,
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
