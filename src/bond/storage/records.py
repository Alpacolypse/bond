"""Records: one write-once key per version, holding its row and the citations it was written with.

A record holds no content: hashes, enums, numbers, positional locators and producer ids (see docs/storage.md,
"What Bond stores"). It is content-addressed by the version's id, so two writers of one version write the same
record, and the second create finds it there.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from ..ids import canonical, supports
from ..jsonvalue import JsonObject, JsonValue
from ..locators import locator_from_dict
from ..models import SELF, BondError, CitationKind, Format, Ref, StoredCitation, Version, VersionId
from . import NewVersion
from .store import Key, Store, now
from .values import decode, encode, items, mapping, text


@dataclass(frozen=True)
class Record:
    """One version as stored: its row and its citations (supports inside it stored under its own id)."""

    version: Version  # its id is the key it was read under
    citations: list[StoredCitation]
    stored_id: VersionId  # the id the record itself names; another than version.id only in a corrupt store


class Records:
    """A workspace's versions: (workspace id, "records", version id) -> its record."""

    def __init__(self, store: Store, workspace: str):
        self.store = store
        self.prefix: Key = (workspace, "records")

    def put(self, new: NewVersion) -> bool:
        """Store a version's record unless it is stored already; True if this call stored it."""
        citations: list[JsonValue] = [
            {"id": cid, "kind": c.kind.value, "claim": c.claim.to_dict(), "attrs": dict(c.attrs),
             "supports": [[new.id if version == SELF else version, locator] for version, locator in supports(c)]}
            for cid, c in canonical(new.citations)
        ]
        value: JsonObject = {"id": new.id, "content_hash": new.content_hash, "format": new.format.value,
                             "created_at": now(), "citations": citations}
        return self.store.create((*self.prefix, new.id), encode(value))

    def get(self, v: VersionId) -> Record | None:
        """The record stored under v. Raises BondError if it names another version: a record is read by its key,
        so one moved or copied under the wrong key must not pass for the version asked for."""
        record = self.as_stored(v)
        if record is not None and record.stored_id != v:
            raise BondError(f"the record under version {v[:12]}… is version {record.stored_id[:12]}…'s; "
                            "the store is corrupt")
        return record

    def as_stored(self, v: VersionId) -> Record | None:
        """The record stored under v, as the version v whatever id it names (in stored_id), for verify to check
        against v itself."""
        data = self.store.get((*self.prefix, v))
        return None if data is None else _record(v, decode(data, "a version's record"))

    def version(self, v: VersionId) -> Version | None:
        """A stored version, or None if it isn't stored here."""
        record = self.get(v)
        return None if record is None else record.version

    def citations(self, v: VersionId) -> list[StoredCitation]:
        """The citations a version was written with, in id order; none if it isn't stored here."""
        record = self.get(v)
        return [] if record is None else list(record.citations)

    def ids(self) -> Iterator[VersionId]:
        """Every version stored, in no particular order."""
        for key in self.store.list(self.prefix):
            yield VersionId(str(key[-1]))


def _record(key: VersionId, obj: Mapping[str, JsonValue]) -> Record:
    version = Version(key, text(obj, "content_hash"), Format(text(obj, "format")), text(obj, "created_at"))
    citations = []
    for raw in items(obj.get("citations"), "citations"):
        c = mapping(raw, "a citation")
        refs = []
        for pair in items(c.get("supports"), "supports"):
            match pair:
                case [str(v), Mapping() as locator]:
                    refs.append(Ref(VersionId(v), locator_from_dict(locator)))
                case _:
                    raise BondError(f"the store holds {pair!r} where a support belongs; it is corrupt")
        attrs = {k: v for k, v in mapping(c.get("attrs"), "attrs").items() if isinstance(v, str)}
        citations.append(StoredCitation(
            id=text(c, "id"), owner_version=version.id, kind=CitationKind(text(c, "kind")),
            claim=locator_from_dict(mapping(c.get("claim"), "a locator")), supports=refs, attrs=attrs,
        ))
    return Record(version, citations, VersionId(text(obj, "id")))
