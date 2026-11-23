"""The Bond home: one person's local workspaces, one directory each, and their decisions about them.

A workspace in the home is `<home>/workspaces/<name>/`, a directory `Bond.local` opens, so deleting or exporting
one is deleting or copying its directory. The home sits in the platform's application-data directory, outside
the folders people keep their own files in (and outside synced ones, where a SQLite catalog can be corrupted);
BOND_HOME moves it.

People name workspaces two ways. A value with a path separator in it is a directory (`./cases/001`,
`/srv/bond/smith`); anything else is the name of a workspace in the home (`smith`). So a name never holds a
separator, and a name can't start with a dot, which keeps `.` and `..` meaning directories.

The home also keeps two kinds of decision, rows in its home.db, made only by a person (`bond link`, `bond grant`,
or a click in the app view) and never by an agent:

- a link lets documents be imported from one workspace into another; it gives no access to work in the other.
- a grant lets the agent of a server configuration (`bond mcp --grants NAME`) work in a workspace. It belongs to
  the configuration, not to a process or a chat: the host may restart the server or share one process between
  chats, and the server can tell neither apart. So a grant covers every chat that configuration serves until
  it's revoked.

Each call opens home.db and closes it, so processes that share a home see each other's decisions at once.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..api import Bond
from ..models import BondError

ENV = "BOND_HOME"
DECISIONS = "home.db"
CATALOG = "bond.db"  # the file Bond.local keeps a workspace's catalog in

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS links (workspace TEXT NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL, "
    "PRIMARY KEY (workspace, source))",
    "CREATE TABLE IF NOT EXISTS grants (config TEXT NOT NULL, workspace TEXT NOT NULL, at TEXT NOT NULL, "
    "PRIMARY KEY (config, workspace))",
)


def default_home(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """BOND_HOME if set; otherwise Bond in the platform's application-data directory."""
    if configured := environ.get(ENV):
        return Path(configured).expanduser()
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Bond"
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Bond"
    return Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "bond"


def is_path(value: str) -> bool:
    """Whether a workspace given on the command line is a directory (it has a separator) rather than a name."""
    return "/" in value or os.sep in value or (os.altsep is not None and os.altsep in value)


def check_name(name: str) -> str:
    """name, if it can name a workspace in the home; raises BondError saying why not."""
    if not name.strip():
        raise BondError("a workspace's name can't be blank")
    if is_path(name):
        raise BondError(f"{name!r} is a path, not a workspace name")
    if name.startswith("."):
        raise BondError(f"a workspace's name can't start with '.'; for a directory here, write ./{name.lstrip('./')}")
    if "\0" in name or len(name) > 200:
        raise BondError(f"{name[:40]!r}… can't name a workspace")
    return name


def check_config(name: str) -> str:
    """name, if it can name a server configuration's grants; raises BondError saying why not."""
    if not name.strip() or len(name) > 100 or "\0" in name:
        raise BondError(f"{name[:40]!r} can't name a configuration's grants")
    return name


@dataclass(frozen=True)
class Home:
    """A Bond home at root: its workspaces, by name, and the links and grants a person made between them."""

    root: Path

    @classmethod
    def default(cls) -> Home:
        return cls(default_home())

    # --- workspaces ------------------------------------------------------------

    def path(self, name: str) -> Path:
        """Where the workspace called name lives, whether or not it exists yet."""
        return self.root / "workspaces" / check_name(name)

    def exists(self, name: str) -> bool:
        return (self.path(name) / CATALOG).is_file()

    def names(self) -> list[str]:
        """The workspaces in the home, by name, sorted."""
        workspaces = self.root / "workspaces"
        if not workspaces.is_dir():
            return []
        return sorted(p.name for p in workspaces.iterdir() if (p / CATALOG).is_file())

    def resolve(self, value: str) -> Path:
        """The directory a workspace given on the command line is in: a path as given, or a name in the home."""
        return Path(value).expanduser() if is_path(value) else self.path(value)

    def require(self, name: str) -> Path:
        """The directory of an existing workspace in the home; raises BondError, saying how to create it, if none."""
        if not self.exists(name):
            raise BondError(f"no workspace named {name!r} in {self.root}; create it with: bond new {name}")
        return self.path(name)

    def create(self, name: str) -> Path:
        """A new, empty workspace called name; raises BondError if there already is one."""
        if self.exists(name):
            raise BondError(f"there's already a workspace named {name!r} in {self.root}")
        Bond.local(self.path(name)).close()
        return self.path(name)

    # --- links -----------------------------------------------------------------

    def links(self, name: str) -> list[str]:
        """The workspaces documents may be imported from into name, in the order they were linked."""
        return self._column("SELECT source FROM links WHERE workspace = ? ORDER BY at, rowid", check_name(name))

    def link(self, name: str, source: str) -> bool:
        """Let documents be imported from source into name; False if they already could. Both must exist."""
        self.require(name)
        self.require(source)
        if name == source:
            raise BondError("a workspace can't be linked to itself")
        return self._change("INSERT INTO links VALUES (?, ?, ?) ON CONFLICT DO NOTHING", name, source, _now())

    def unlink(self, name: str, source: str) -> bool:
        """Stop documents being imported from source into name; False if they couldn't be. What was imported stays."""
        return self._change("DELETE FROM links WHERE workspace = ? AND source = ?", check_name(name), source)

    # --- grants ----------------------------------------------------------------

    def granted(self, config: str) -> list[str]:
        """The workspaces granted to config, in the order they were granted."""
        return self._column("SELECT workspace FROM grants WHERE config = ? ORDER BY at, rowid", check_config(config))

    def grantees(self, name: str) -> list[str]:
        """The configurations granted name, by name."""
        return self._column("SELECT config FROM grants WHERE workspace = ? ORDER BY config", check_name(name))

    def grant(self, config: str, name: str) -> bool:
        """Let config's agent work in name, which must exist; False if it already could."""
        self.require(name)
        return self._change("INSERT INTO grants VALUES (?, ?, ?) ON CONFLICT DO NOTHING", check_config(config), name,
                            _now())

    def revoke(self, config: str, name: str) -> bool:
        """Stop config's agent working in name; False if it couldn't. What it wrote there stays."""
        return self._change("DELETE FROM grants WHERE config = ? AND workspace = ?", check_config(config),
                            check_name(name))

    # --- home.db ---------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.root / DECISIONS, timeout=30.0, isolation_level=None)
        for statement in _SCHEMA:
            conn.execute(statement)
        return conn

    def _column(self, query: str, *params: str) -> list[str]:
        if not (self.root / DECISIONS).is_file():
            return []
        with closing(self._connect()) as conn:
            return [value for (value,) in conn.execute(query, params).fetchall()]

    def _change(self, statement: str, *params: str) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute(statement, params).rowcount == 1


def _now() -> str:
    return datetime.now(UTC).isoformat()
