"""Grant mode: `bond mcp --grants NAME` serves the workspaces a person granted that configuration in the home.

The agent starts with what was granted and can't grant itself anything. `workspaces()` lists what it can work in
and, with the app view, shows the person a picker: their click grants, revokes or creates a workspace
(view_workspaces, grant_workspace and new_workspace, app-only and acting as the person), and from the next call
the agent works there. Grants live in the home (bond.local.home), so they outlast the process.
"""

from __future__ import annotations

import threading
from contextlib import ExitStack
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer

from ..api import Bond
from ..mcp.server import Served, ServedTool, reported
from ..mcp.server import create_server as mcp_server
from ..mcp.view import ACTING, READING, ViewTools
from .home import Home, check_config

GRANTING = """
A person grants you workspaces: workspaces() lists the ones you can work in, and shows them a picker to grant \
another. Ask them to pick one there when the workspace you need isn't listed; you can't grant one yourself.
"""


@dataclass(frozen=True)
class Granted:
    """The workspaces the agent can work in now."""

    workspaces: list[str]


@dataclass(frozen=True)
class WorkspaceRow:
    name: str
    granted: bool
    links: list[str]


@dataclass(frozen=True)
class Library:
    workspaces: list[WorkspaceRow]


@dataclass(frozen=True)
class Opened:
    workspace: str


class GrantedWorkspaces:
    """The workspaces granted to one configuration, read from the home on each call (bond.workspaces.Workspaces).

    This is what the server serves in grant mode: a grant made after the server started is served from the next
    call, and a revoked one is refused from the next call. Each workspace is opened on first use and kept open until
    close, and reads the workspaces it links to from the home as they are at each call, so a link made or removed
    while the server runs counts from then on; a workspace deleted from the home is simply no longer offered.
    """

    def __init__(self, home: Home, config: str):
        self.home = home
        self.config = check_config(config)
        self._stack = ExitStack()
        self._open: dict[str, Bond] = {}
        self._lock = threading.Lock()

    def names(self) -> list[str]:
        return [n for n in self.home.granted(self.config) if self.home.exists(n)]

    def open(self, name: str) -> Bond:
        if name not in self.names():
            raise KeyError(name)
        return self._bond(name)

    def _links(self, name: str) -> list[Bond]:
        return [self._bond(n) for n in self.home.links(name) if self.home.exists(n)]

    def _bond(self, name: str) -> Bond:
        with self._lock:
            if name not in self._open:
                self._open[name] = self._stack.enter_context(self.home.open(name, links=lambda: self._links(name)))
            return self._open[name]

    def close(self) -> None:
        self._stack.close()


def create_server(grants: GrantedWorkspaces | Served, *, view: str | None) -> MCPServer:
    """The authoring tools over what grants holds (bound to AGENT_POLICY unless given as Served), with workspaces()
    and, given the view, the picker."""
    served = grants if isinstance(grants, Served) else Served(grants)
    if not isinstance(served.workspaces, GrantedWorkspaces):
        raise TypeError("grant mode serves the workspaces granted in a home (GrantedWorkspaces)")
    grants = served.workspaces

    async def workspaces() -> Granted:
        """The workspaces you can work in now, and a picker where the person can grant you another (only they
        can); after they do, you're told its name."""
        return Granted(grants.names())

    def picker(view_tools: ViewTools) -> None:
        home, config = grants.home, grants.config

        @view_tools.tool("view_workspaces", READING)
        async def view_workspaces() -> Library:
            """For Bond's view: every workspace in the home, for the picker."""
            granted = set(grants.names())
            return Library([WorkspaceRow(n, n in granted, home.links(n)) for n in home.names()])

        @view_tools.tool("grant_workspace", ACTING)
        async def grant_workspace(name: str, granted: bool, person: str) -> Opened:
            """For Bond's view: the person grants the agent this workspace, or stops it working there."""
            view_tools.as_person(person)
            if granted:
                home.grant(config, name)
            else:
                home.revoke(config, name)
            return Opened(name)

        @view_tools.tool("new_workspace", ACTING)
        async def new_workspace(name: str, person: str) -> Opened:
            """For Bond's view: the person creates a workspace, granted to the agent."""
            view_tools.as_person(person)
            home.create(name)
            home.grant(config, name)
            return Opened(name)

    return mcp_server(served, view=view, tools=[ServedTool(reported(workspaces), "workspaces", READING)],
                      more=GRANTING, extend=picker)
