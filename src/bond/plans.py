"""Write plans: a write decided, but not yet done.

Bond.plan_write (and plan_ingest) checks a write against the workspace as it is and returns a WritePlan, or raises
WriteError with every issue, as the write itself would. Planning stores nothing. Bond.commit(plan) does the write:
the version's blob, then its record, then the history entry that files it, then its title. So a caller can check
a write without doing it (an agent's dry run), or decide it in one step and do it in the next.

A plan lives in the process that made it, and only the Bond of its workspace commits it: commit refuses a plan
Bond's checks didn't make, since it doesn't check it again. A host that keeps writes as steps of its own (an agent
orchestrator that logs each tool call) keeps what it would write, the document, content, citations and base, and
plans again when it commits: a plan is a function of those and the workspace, and its version's id is the same.

A plan stays good: what it checked can't stop being true, since versions are never removed and checks read only
the plan's own content and versions already stored (unless the deployment stops linking to a workspace it cites
in between). The one thing that can change is the document it files in, so commit checks the base again,
atomically, and refuses with stale_base if another write moved the document first. Committing a plan again is
harmless: its version is stored already and filing the head again is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Issue, VersionId
from .storage import NewVersion


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
    """A checked write of one workspace: the version to store, if it isn't stored already, and where to file it.

    Made only by Bond.plan_write and Bond.plan_ingest; commit refuses one made any other way."""

    workspace: str  # the workspace's id: a plan is committed only where it was made
    new: NewVersion | None  # None when the version is stored already and only filing it is left
    filing: FilingChange
    warnings: tuple[Issue, ...] = ()  # what planning found that doesn't stop the write
    _seal: object = field(default=None, compare=False, repr=False)  # set by Bond's checks; see Bond.commit

    @property
    def version(self) -> VersionId:
        """The version the plan files."""
        return self.filing.version
