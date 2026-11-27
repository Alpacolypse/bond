"""Filing: a workspace's documents, as name claims, numbered history entries and title entries, all written once.

    (ws, "names", sha256(name))        -> the document's id, name and creation time, claimed once
    (ws, "entries", document_id, seq)  -> the version it held from then on, the base it was written from, when,
                                          and where an import or an ingested file came from
    (ws, "titles", document_id, seq)   -> a title, or none

A document's head is its latest entry, and its title its latest title entry. A write advances a document by
creating its next entry: of two writers who read the same head, the store lets one create it, and the other finds
it taken. That is the only conflict there is, and the only coordination Bond asks of a store. A claimed name with
no entries yet (a writer stopped between the two) is not a document, and the next write to it reuses the claim.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Container, Mapping
from dataclasses import dataclass, replace

from ..jsonvalue import JsonObject, JsonValue
from ..models import BondError, DocumentId, DocumentRef, HistoryEntry, Imported, IssueCode, UnknownDocument, VersionId
from . import Conflict, Filed, filing_problem, stale
from .store import Key, LastKey, Store, now
from .values import decode, encode, mapping, name_hash, optional_text, text


@dataclass(frozen=True)
class Claim:
    """A document's name, claimed once: the document's id, and when it was claimed."""

    id: DocumentId
    name: str
    created_at: str


@dataclass(frozen=True)
class Head:
    """A document as read: its ref (with its head and title) and the seq of its head entry."""

    ref: DocumentRef
    seq: int


class Filing:
    """A workspace's documents."""

    def __init__(self, store: Store, workspace: str):
        self.store = store
        self.ws = workspace

    def _name_key(self, name: str) -> Key:
        return (self.ws, "names", name_hash(name))

    def _entries(self, doc: DocumentId) -> Key:
        return (self.ws, "entries", doc)

    def _titles(self, doc: DocumentId) -> Key:
        return (self.ws, "titles", doc)

    # --- reads ---

    def document(self, name: str) -> DocumentRef | None:
        """The document named name, with its head and title; None if there is none."""
        head = self.get(name)
        return None if head is None else head.ref

    def documents(self) -> list[DocumentRef]:
        """Every document, oldest first."""
        return [h.ref for c in self.claims() if (h := self.head(c)) is not None]

    def history(self, doc: DocumentId, after: int = 0) -> list[HistoryEntry]:
        """A document's entries in order, those after seq `after` only if given."""
        start = (*self._entries(doc), after) if after else None
        found = []
        for key in self.store.list(self._entries(doc), after=start):
            seq = key[-1]
            if not isinstance(seq, int):
                raise BondError(f"the store holds history key {key!r}, whose last element must be a seq")
            entry = self.entry(doc, seq)
            if entry is not None:
                found.append(entry)
        return found

    def get(self, name: str) -> Head | None:
        """The document named name, with the seq of its head entry; None if there is none."""
        claim = self.claim_of(name)
        return None if claim is None else self.head(claim)

    def claim_of(self, name: str) -> Claim | None:
        data = self.store.get(self._name_key(name))
        return None if data is None else _claim(decode(data, "a document's name"))

    def claims(self) -> list[Claim]:
        """Every name claimed in the workspace, oldest first; a claim with no entries is not yet a document."""
        found = []
        for key in self.store.list((self.ws, "names")):
            data = self.store.get(key)
            if data is not None:
                found.append(_claim(decode(data, "a document's name")))
        return sorted(found, key=lambda c: (c.created_at, c.id))

    def new_claims(self, known: Container[Key]) -> dict[Key, Claim]:
        """The name claims whose keys aren't in known, by key: what an index hasn't read yet."""
        found = {}
        for key in self.store.list((self.ws, "names")):
            if key not in known and (data := self.store.get(key)) is not None:
                found[key] = _claim(decode(data, "a document's name"))
        return found

    def head(self, claim: Claim) -> Head | None:
        """The document claim names, with its head; None if it has no entries yet."""
        seq = self._last_seq(self._entries(claim.id))
        if seq == 0:
            return None
        entry = self.entry(claim.id, seq)
        if entry is None:
            raise BondError(f"history entry {seq} of {claim.name!r} was listed but can't be read; the store is corrupt")
        return Head(DocumentRef(claim.id, claim.name, entry.version, claim.created_at, self.title(claim.id)), seq)

    def entry(self, doc: DocumentId, seq: int) -> HistoryEntry | None:
        data = self.store.get((*self._entries(doc), seq))
        return None if data is None else _entry(seq, decode(data, "a history entry"))

    def title(self, doc: DocumentId) -> str | None:
        seq = self._last_seq(self._titles(doc))
        if seq == 0:
            return None
        data = self.store.get((*self._titles(doc), seq))
        return None if data is None else optional_text(decode(data, "a title"), "title")

    def _last_seq(self, prefix: Key) -> int:
        if isinstance(self.store, LastKey):
            last = self.store.last(prefix)
        else:
            last = None
            for last in self.store.list(prefix):  # noqa: B007 (the last one listed is the greatest)
                pass
        if last is None:
            return 0
        if not isinstance(last[-1], int):
            raise BondError(f"the store holds key {last!r}, whose last element must be a seq")
        return last[-1]

    # --- writes ---

    def file(
        self, v: VersionId, document: str, base: VersionId | None, *, imported: Imported | None = None,
        origin: str | None = None, before: Callable[[], object] | None = None,
    ) -> Filed | Conflict:
        """File version v in document, from base, following filing_problem: the conflict, or the document after.

        before, if given, runs once the filing is allowed and before the entry that files v is created: what
        must be stored before anything names it (v's record). Then the next entry is created; if another write
        created it first, the head is read again: if it is v, the write already happened (a retry, or two
        writers of the same version), and otherwise it is a conflict. imported and origin are recorded on the
        entry.
        """
        read = self.get(document)
        doc = None if read is None else read.ref
        if problem := filing_problem(doc, document, base, v):
            return problem
        if doc is not None and doc.head == v:
            return Filed(doc, False)
        if before is not None:
            before()
        if read is None:
            claim = self.claim(document)
            entry = HistoryEntry(1, v, None, now(), imported, origin)
            title = None
        else:
            claim = Claim(read.ref.id, document, read.ref.created_at)
            entry = HistoryEntry(read.seq + 1, v, read.ref.head, now(), imported, origin)
            title = read.ref.title
        if not self.append(claim.id, entry):
            again = self.get(document)
            if again is not None and again.ref.head == v:
                return Filed(again.ref, False)
            if read is None:
                return Conflict(IssueCode.DOCUMENT_EXISTS, f"document {document!r} was created by another write",
                                "read its head and pass it as base to write its next version")
            return stale(document, read.ref.head, None if again is None else again.ref.head)
        return Filed(DocumentRef(claim.id, document, v, claim.created_at, title), True)

    def claim(self, name: str) -> Claim:
        """Claim name for a new document, unless it is claimed already; the claim either way."""
        value: JsonObject = {"id": "doc_" + uuid.uuid4().hex, "name": name, "created_at": now()}
        self.store.create(self._name_key(name), encode(value))
        claim = self.claim_of(name)
        if claim is None:
            raise BondError(f"the name {name!r} was claimed, but its claim can't be read back")
        return claim

    def append(self, doc: DocumentId, entry: HistoryEntry) -> bool:
        """Create entry at its seq unless one is there; True if this call created it."""
        value: JsonObject = {"version": entry.version, "base": entry.base, "at": entry.at,
                             "imported": None if entry.imported is None else entry.imported.to_dict(),
                             "origin": entry.origin}
        return self.store.create((*self._entries(doc), entry.seq), encode(value))

    def retitle(self, name: str, title: str | None) -> DocumentRef:
        """Give a document its next title entry; the last writer wins, and no title conflicts with a write. Raises
        UnknownDocument."""
        claim = self.claim_of(name)
        head = None if claim is None else self.head(claim)
        if claim is None or head is None:
            raise UnknownDocument(name)
        while True:
            seq = self._last_seq(self._titles(claim.id)) + 1
            # after: the history entry the document was at, so a replayed write can tell whether a title came since
            value: JsonObject = {"title": title, "at": now(), "after": head.seq}
            if self.store.create((*self._titles(claim.id), seq), encode(value)):
                return replace(head.ref, title=title)

    def titled_after(self, doc: DocumentId) -> int:
        """The history entry the document was at when it was last titled; 0 if it never was."""
        seq = self._last_seq(self._titles(doc))
        data = None if seq == 0 else self.store.get((*self._titles(doc), seq))
        if data is None:
            return 0
        after = decode(data, "a title").get("after")
        if not isinstance(after, int):
            raise BondError(f"the store holds {after!r} where a title's entry seq belongs; it is corrupt")
        return after


def _claim(obj: Mapping[str, JsonValue]) -> Claim:
    return Claim(DocumentId(text(obj, "id")), text(obj, "name"), text(obj, "created_at"))


def _entry(seq: int, obj: Mapping[str, JsonValue]) -> HistoryEntry:
    base = optional_text(obj, "base")
    imported = None
    if obj.get("imported") is not None:
        i = mapping(obj.get("imported"), "an import's source")
        imported = Imported(text(i, "workspace"), text(i, "workspace_name"), VersionId(text(i, "version")))
    return HistoryEntry(seq, VersionId(text(obj, "version")), None if base is None else VersionId(base),
                        text(obj, "at"), imported, optional_text(obj, "origin"))
