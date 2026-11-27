"""The workspaces a deployment offers: which ones there are to work in, by name.

A Bond is one workspace. Something above it decides which workspaces an API offers (so who can reach them) and which
each links to, so its citations can rest on theirs (Bond.set_links). That is a deployment's decision, and Workspaces
is how it hands the first part to an API; each Bond it opens carries its own links. bond.local's GrantedWorkspaces is
one deployment's answer, read from the Bond home; Fixed is the plain one, for workspaces a program opened itself
(local directories, Postgres, anything else).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .api import Bond


class Workspaces(Protocol):
    """The workspaces offered, read on every call: what is offered may change while an API serves them."""

    def names(self) -> list[str]:
        """The workspaces offered now, to work in, by name."""
        ...

    def open(self, name: str) -> Bond:
        """One of names(), opened, with its links; raises KeyError for any other name."""
        ...


@dataclass(frozen=True)
class Fixed:
    """Workspaces a program opened (with their links), by name. The program owns every Bond."""

    workspaces: Mapping[str, Bond]

    def names(self) -> list[str]:
        return list(self.workspaces)

    def open(self, name: str) -> Bond:
        return self.workspaces[name]
