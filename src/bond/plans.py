"""Write plans: a write decided, but not yet done.

Bond.plan_write (and plan_ingest, plan_import, plan_import_version) checks a write against the workspace as it is
and returns a WritePlan, or raises WriteError with every issue, as the write itself would. Planning stores nothing.
Bond.commit(plan) does the write: the versions' blobs, then their records, then the history entry that files one,
then its title. A host that wants writes to go through its own steps (an agent orchestrator whose tools decide what
to write, and whose loop writes it) plans in one place and commits in another.

A plan stays good: what it checked can't stop being true, since versions are never removed and checks read only
the plan's own content and versions already stored. The one thing that can change is the document it files in, so
commit checks the base again, atomically, and refuses with stale_base if another write moved the document first.
Committing a plan again is harmless: its versions are stored already and filing the head again is a no-op.

A plan serializes (to_json) without its bytes, which it names by hash: put them first (Bond.stage), then keep the
plan where the host keeps its steps, and load it back (Bond.load_plan) to commit, from the same or another process.
A loaded plan is checked again before it is committed, so a plan from outside can't store what a write couldn't.
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from .ids import dumps, supports
from .jsonvalue import JsonObject, JsonValue
from .locators import locator_from_dict
from .models import SELF, BondError, Citation, CitationKind, Format, Imported, Ref, SelfRef, Support, VersionId
from .parsers import load_json
from .policies import STRICT, Policy
from .storage import Blobs, NewVersion

PLAN_FORMAT = 1


class _Checked:
    """A mark that a plan was made by Bond's checks in this process, not loaded from outside."""


# Marks of plans Bond's checks made: such a plan isn't checked again when committed. Weak, so a plan that is
# dropped takes its mark with it.
CHECKED: weakref.WeakSet[_Checked] = weakref.WeakSet()


@dataclass(frozen=True)
class PlannedVersion:
    """A version a plan stores: its bytes and citations, and whether it was brought from another workspace (its
    identity is checked, not its content against a policy, since what it rests on may be outside)."""

    new: NewVersion
    imported: bool = False


@dataclass(frozen=True)
class FilingChange:
    """Where a plan files a version: in document, from base (None to create it), with what its history entry
    records."""

    document: str
    version: VersionId
    base: VersionId | None
    imported: Imported | None = None
    origin: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class WritePlan:
    """A checked write of one workspace: versions to store, and where to file one, if anywhere."""

    workspace: str  # the workspace's id: a plan is committed only where it was made
    versions: tuple[PlannedVersion, ...]  # to store, each before any that rests on it
    filing: FilingChange | None
    policy: Policy = STRICT  # what written versions were checked against, and are again if the plan is loaded
    subject: VersionId | None = None  # for a plan that files nothing (import_version): the version it brings
    _mark: _Checked | None = field(default=None, compare=False, repr=False)

    @property
    def version(self) -> VersionId | None:
        """The version the plan files, or (for an import that files nothing) the version it brings, even when that
        is here already and it stores nothing."""
        if self.filing is not None:
            return self.filing.version
        return self.subject if self.subject is not None else self.versions[-1].new.id if self.versions else None

    @property
    def checked(self) -> bool:
        """Whether Bond's checks made this plan in this process, so committing it needn't check it again."""
        return self._mark is not None and self._mark in CHECKED

    def blobs(self) -> dict[str, bytes]:
        """The bytes the plan stores, by content hash."""
        return {pv.new.content_hash: pv.new.data for pv in self.versions}

    def to_json(self) -> str:
        """The plan without its bytes, which it names by hash (put them first: Bond.stage)."""
        value: JsonObject = {
            "bond_plan": PLAN_FORMAT,
            "workspace": self.workspace,
            "policy": {"gaps": self.policy.gaps, "kinds": sorted(k.value for k in self.policy.kinds),
                       "producers": sorted(self.policy.producers)},
            "versions": [
                {"id": pv.new.id, "content_hash": pv.new.content_hash, "format": pv.new.format.value,
                 "imported": pv.imported, "citations": [_citation(c) for c in pv.new.citations]}
                for pv in self.versions
            ],
            "filing": None if self.filing is None else {
                "document": self.filing.document, "version": self.filing.version, "base": self.filing.base,
                "imported": None if self.filing.imported is None else self.filing.imported.to_dict(),
                "origin": self.filing.origin, "title": self.filing.title,
            },
            "subject": self.subject,
        }
        return dumps(value)

    @staticmethod
    def from_json(text: str, blobs: Blobs) -> WritePlan:
        """A plan to_json wrote, with its bytes read from blobs; unchecked, so commit checks it again. Raises
        BondError if it isn't a plan or a blob it names isn't there."""
        loaded = load_json(text)
        if not isinstance(loaded, Mapping) or loaded.get("bond_plan") != PLAN_FORMAT:
            raise BondError(f"not a Bond write plan (format {PLAN_FORMAT})")
        p = _obj(loaded.get("policy"))
        policy = Policy(gaps=p.get("gaps") is True,
                        kinds=frozenset(CitationKind(_s(k)) for k in _seq(p.get("kinds"))),
                        producers=frozenset(_s(x) for x in _seq(p.get("producers"))))
        versions = []
        for raw in _seq(loaded.get("versions")):
            v = _obj(raw)
            content_hash = _s(v.get("content_hash"))
            data = blobs.get(content_hash)
            if data is None:
                raise BondError(f"the plan names blob {content_hash[:12]}…, which isn't stored; stage the plan first")
            citations = [_citation_from(_obj(c)) for c in _seq(v.get("citations"))]
            new = NewVersion(VersionId(_s(v.get("id"))), content_hash, Format(_s(v.get("format"))), data, citations)
            versions.append(PlannedVersion(new, v.get("imported") is True))
        filing = None
        if loaded.get("filing") is not None:
            f = _obj(loaded.get("filing"))
            imported = None
            if f.get("imported") is not None:
                i = _obj(f.get("imported"))
                imported = Imported(_s(i.get("workspace")), _s(i.get("workspace_name")), VersionId(_s(i.get("version"))))
            filing = FilingChange(_s(f.get("document")), VersionId(_s(f.get("version"))),
                                  None if f.get("base") is None else VersionId(_s(f.get("base"))), imported,
                                  None if f.get("origin") is None else _s(f.get("origin")),
                                  None if f.get("title") is None else _s(f.get("title")))
        subject = None if loaded.get("subject") is None else VersionId(_s(loaded.get("subject")))
        return WritePlan(_s(loaded.get("workspace")), tuple(versions), filing, policy, subject)


def checked(plan: WritePlan) -> WritePlan:
    """plan, marked as made by Bond's checks."""
    mark = _Checked()
    CHECKED.add(mark)
    return replace(plan, _mark=mark)


def _citation(c: Citation) -> JsonObject:
    """A citation as written, in the form its id hashes (supports inside its version as "self")."""
    pairs: list[JsonValue] = [[v, loc] for v, loc in supports(c)]
    return {"kind": c.kind.value, "claim": c.claim.to_dict(), "attrs": dict(c.attrs), "supports": pairs}


def _citation_from(c: Mapping[str, JsonValue]) -> Citation:
    refs: list[Support] = []
    for pair in _seq(c.get("supports")):
        match pair:
            case [str(v), Mapping() as loc]:
                locator = locator_from_dict(loc)
                refs.append(SelfRef(locator) if v == SELF else Ref(VersionId(v), locator))
            case _:
                raise BondError(f"the plan holds {pair!r} where a support belongs")
    attrs = {k: _s(v) for k, v in _obj(c.get("attrs")).items()}
    return Citation(CitationKind(_s(c.get("kind"))), locator_from_dict(_obj(c.get("claim"))), refs, attrs)


def _obj(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise BondError(f"the plan holds {value!r} where an object belongs")
    return value


def _seq(value: JsonValue) -> Sequence[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise BondError(f"the plan holds {value!r} where a list belongs")
    return value


def _s(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise BondError(f"the plan holds {value!r} where text belongs")
    return value
