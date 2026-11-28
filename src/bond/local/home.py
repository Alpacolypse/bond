"""The Bond home: one person's local workspaces, and their decisions about them.

A home is one store and one blob directory, `<home>/bond.db` and `<home>/blobs/`, holding every workspace by name,
as a hosted deployment's database does: the same policy PDF cited by several cases is stored once. The home sits
in the platform's application-data directory, outside the folders people keep their own files in (and outside
synced ones, where a SQLite store can be corrupted); BOND_HOME moves it.

The home also keeps two kinds of decision, rows in its home.db, made only by a person (`bond link`, `bond grant`,
or a click in the app view) and never by an agent:

- a link lets one workspace's documents cite another's (a case citing a policy library): an agent working in the
  first can read, search and cite the second, and what the second links to, but never write there. Links never
  loop, so a shared workspace can't come to rest on the cases that cite it.
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
import threading
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ..api import Bond, Links
from ..models import BondError
from ..storage.local import LocalBlobs
from ..storage.sqlite import SQLiteStore
from ..storage.workspace import find_workspace, open_workspace, workspaces

ENV = "BOND_HOME"
DECISIONS = "home.db"
STORE = "bond.db"  # the store every workspace in the home is kept in
BLOBS = "blobs"  # and their bytes

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


def check_name(name: str) -> str:
    """name, if it can name a workspace in the home; raises BondError saying why not."""
    if not name.strip():
        raise BondError("a workspace's name can't be blank")
    if "\0" in name or len(name) > 200:
        raise BondError(f"{name[:40]!r}… can't name a workspace")
    return name


def check_config(name: str) -> str:
    """name, if it can name a server configuration's grants; raises BondError saying why not."""
    if not name.strip() or len(name) > 100 or "\0" in name:
        raise BondError(f"{name[:40]!r} can't name a configuration's grants")
    return name


class Home:
    """A Bond home at root: its workspaces, by name, and the links and grants a person made between them.

    Its store is opened on first use and kept open until close (a Home is a context manager); workspaces opened from
    it share that store and its blobs, and closing one leaves them open.
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self._store: SQLiteStore | None = None
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> Home:
        return cls(default_home())

    def __enter__(self) -> Home:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None

    def _opened(self, create: bool) -> SQLiteStore | None:
        """The home's store; None if it has none yet and create is False."""
        with self._lock:
            if self._store is None:
                if not create and not (self.root / STORE).is_file():
                    return None
                self.root.mkdir(parents=True, exist_ok=True)
                self._store = SQLiteStore(self.root / STORE)
            return self._store

    # --- workspaces ------------------------------------------------------------

    def names(self) -> list[str]:
        """The workspaces in the home, by name, sorted."""
        store = self._opened(create=False)
        return [] if store is None else sorted(w.name for w in workspaces(store))

    def exists(self, name: str) -> bool:
        store = self._opened(create=False)
        return store is not None and find_workspace(store, check_name(name)) is not None

    def require(self, name: str) -> None:
        """Raises BondError, saying how to create it, unless there is a workspace called name."""
        if not self.exists(name):
            raise BondError(f"no workspace named {name!r} in {self.root}; create it with: bond new {name}")

    def create(self, name: str) -> None:
        """A new, empty workspace called name; raises BondError if there already is one."""
        if self.exists(name):
            raise BondError(f"there's already a workspace named {name!r} in {self.root}")
        store = self._opened(create=True)
        assert store is not None
        open_workspace(store, name, create=True)

    def open(self, name: str, links: Links = ()) -> Bond:
        """The workspace called name, linked to links (see Bond.set_links); raises BondError if there is none.
        Closing it leaves the home's store open."""
        self.require(name)
        store = self._opened(create=False)
        assert store is not None
        return Bond(store, LocalBlobs(self.root / BLOBS), name, create=False, links=links)

    # --- links -----------------------------------------------------------------

    def links(self, name: str) -> list[str]:
        """The workspaces name links to (whose documents its own may cite), in the order they were linked."""
        return self._column("SELECT source FROM links WHERE workspace = ? ORDER BY at, rowid", check_name(name))

    def reaches(self, name: str, other: str) -> bool:
        """Whether name links to other, directly or through workspaces it links to."""
        seen, level = {name}, [name]
        while level:
            level = [n for w in level for n in self.links(w) if n not in seen]
            if other in level:
                return True
            seen.update(level)
        return False

    def link(self, name: str, source: str) -> bool:
        """Let name's documents cite source's; False if they already could. Both must exist, and source must not
        already reach name: links never loop."""
        self.require(name)
        self.require(source)
        if name == source:
            raise BondError("a workspace can't be linked to itself")
        if self.reaches(source, name):
            raise BondError(f"{source!r} already links to {name!r}, directly or through others; links can't loop, "
                            "so a workspace never rests on one that rests on it")
        return self._change("INSERT INTO links VALUES (?, ?, ?) ON CONFLICT DO NOTHING", name, source, _now())

    def unlink(self, name: str, source: str) -> bool:
        """Stop name's documents citing source's; False if they couldn't. What already cites source stays, and is
        unreachable from name until it is linked again."""
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
