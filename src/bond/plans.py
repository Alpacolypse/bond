"""Write plans: a write decided, but not yet done.

Bond.plan_write (and plan_ingest) checks a write against the workspace as it is
and returns a WritePlan, or raises WriteError with every issue, as the write itself would. Planning stores nothing.
Bond.commit(plan) does the write: the version's blob, then its record, then the history entry that files it,
then its title. A host that wants writes to go through its own steps (an agent orchestrator whose tools decide what
to write, and whose loop writes it) plans in one place and commits in another.

A plan stays good: what it checked can't stop being true, since versions are never removed and checks read only
the plan's own content and versions already stored (unless the deployment stops linking to a workspace it cites in
between, which only a check made again would see). The one thing that can change is the document it files in, so
commit checks the base again, atomically, and refuses with stale_base if another write moved the document first.
Committing a plan again is harmless: its version is stored already and filing the head again is a no-op.

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
from .models import SELF, BondError, Citation, CitationKind, Format, Issue, Ref, SelfRef, Support, VersionId
from .parsers import load_json
from .policies import STRICT, Policy
from .storage import Blobs, NewVersion

PLAN_FORMAT = 2


class _Checked:
    """A mark that a plan was made by Bond's checks in this process, not loaded from outside."""


# Marks of plans Bond's checks made: such a plan isn't checked again when committed. Weak, so a plan that is
# dropped takes its mark with it.
CHECKED: weakref.WeakSet[_Checked] = weakref.WeakSet()


@dataclass(frozen=True)
class FilingChange:
    """Where a plan files a version: in document, from base (None to create it), with what its history entry
    records."""

    document: str
    version: VersionId
    base: VersionId | None
    origin: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class WritePlan:
    """A checked write of one workspace: the version to store, if it isn't stored already, and where to file it."""

    workspace: str  # the workspace's id: a plan is committed only where it was made
    new: NewVersion | None  # None when the version is stored already and only filing it is left
    filing: FilingChange
    policy: Policy = STRICT  # what the new version was checked against, and is again if the plan is loaded
    warnings: tuple[Issue, ...] = ()  # what planning found that doesn't stop the write; not kept by to_json
    _mark: _Checked | None = field(default=None, compare=False, repr=False)

    @property
    def version(self) -> VersionId:
        """The version the plan files."""
        return self.filing.version

    @property
    def checked(self) -> bool:
        """Whether Bond's checks made this plan in this process, so committing it needn't check it again."""
        return self._mark is not None and self._mark in CHECKED

    def blobs(self) -> dict[str, bytes]:
        """The bytes the plan stores, by content hash."""
        return {} if self.new is None else {self.new.content_hash: self.new.data}

    def to_json(self) -> str:
        """The plan without its bytes, which it names by hash (put them first: Bond.stage)."""
        value: JsonObject = {
            "bond_plan": PLAN_FORMAT,
            "workspace": self.workspace,
            "policy": {"gaps": self.policy.gaps, "kinds": sorted(k.value for k in self.policy.kinds),
                       "producers": sorted(self.policy.producers),
                       "refuse_superseded": self.policy.refuse_superseded},
            "new": None if self.new is None else {
                "id": self.new.id, "content_hash": self.new.content_hash, "format": self.new.format.value,
                "citations": [_citation(c) for c in self.new.citations],
            },
            "filing": {
                "document": self.filing.document, "version": self.filing.version, "base": self.filing.base,
                "origin": self.filing.origin, "title": self.filing.title,
            },
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
                        producers=frozenset(_s(x) for x in _seq(p.get("producers"))),
                        refuse_superseded=p.get("refuse_superseded") is True)
        new = None
        if loaded.get("new") is not None:
            v = _obj(loaded.get("new"))
            content_hash = _s(v.get("content_hash"))
            data = blobs.get(content_hash)
            if data is None:
                raise BondError(f"the plan names blob {content_hash[:12]}…, which isn't stored; stage the plan first")
            citations = [_citation_from(_obj(c)) for c in _seq(v.get("citations"))]
            new = NewVersion(VersionId(_s(v.get("id"))), content_hash, Format(_s(v.get("format"))), data, citations)
        f = _obj(loaded.get("filing"))
        filing = FilingChange(_s(f.get("document")), VersionId(_s(f.get("version"))),
                              None if f.get("base") is None else VersionId(_s(f.get("base"))),
                              None if f.get("origin") is None else _s(f.get("origin")),
                              None if f.get("title") is None else _s(f.get("title")))
        return WritePlan(_s(loaded.get("workspace")), new, filing, policy)


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
