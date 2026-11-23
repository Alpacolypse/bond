"""The workspaces a deployment offers: which ones there are to work in, by name, and which each may import from.

A Bond is one workspace and decides nothing about others. Something above it decides which workspaces an API
offers (so who can reach them) and which may import from which (its links). That is a deployment's decision, and
Workspaces is how it hands it to an API: bond.mcp serves the agent whatever workspaces it is given. bond.local's
GrantedWorkspaces is one deployment's answer, read from the Bond home; Fixed is the plain one, for workspaces a
program opened itself (local directories, Postgres, anything else).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .api import Bond


class Workspaces(Protocol):
    """The workspaces offered, read on every call: what is offered may change while an API serves them."""

    def names(self) -> list[str]:
        """The workspaces offered now, to work in, by name."""
        ...

    def open(self, name: str) -> Bond:
        """One of names(), opened; raises KeyError for any other name."""
        ...

    def links(self, name: str) -> Mapping[str, Bond]:
        """The workspaces name may import documents from, by name, opened. A link gives no access to work in them."""
        ...


@dataclass(frozen=True)
class Fixed:
    """Workspaces a program opened, by name, each with the workspaces it links to. The program owns every Bond."""

    workspaces: Mapping[str, Bond]
    linked: Mapping[str, Mapping[str, Bond]] = field(default_factory=dict)

    def names(self) -> list[str]:
        return list(self.workspaces)

    def open(self, name: str) -> Bond:
        return self.workspaces[name]

    def links(self, name: str) -> Mapping[str, Bond]:
        return self.linked.get(name, {})
