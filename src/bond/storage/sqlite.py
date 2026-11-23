"""A Catalog in one SQLite file, for development, tests and single-machine stores."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from ..models import BondError
from .sql import Exec, SqlCatalog, str_value


class _Local(threading.local):
    """One thread's connection, opened the first time that thread uses the catalog."""

    conn: sqlite3.Connection | None = None


class SQLiteCatalog(SqlCatalog):
    """A catalog of one workspace in a SQLite file, created with its tables if it doesn't exist.

    The workspace is opened by name, and created if missing; with no name, the
    only workspace in the file is opened (a new one named "default" if it has
    none). Each thread gets its own connection, so one catalog can be shared by
    every thread of a process; separate processes can open the same file. Writes
    take SQLite's write lock up front (BEGIN IMMEDIATE), waiting up to timeout
    seconds for another writer to finish. With create=False, a file that isn't
    a catalog already, or a workspace that isn't in it, is refused, not made.
    """

    ORDER = "rowid"
    ORDER_DEFINITION = ""

    def __init__(
        self, path: str | os.PathLike[str], workspace: str | None = None, timeout: float = 30.0, create: bool = True,
    ):
        self.path = Path(path)
        if not create and not self.path.is_file():
            raise BondError(f"no Bond catalog at {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._local = _Local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        try:
            self._open(workspace, create)
        except BaseException:
            self.close()
            raise

    def _connection(self) -> sqlite3.Connection:
        if self._closed:
            raise BondError(f"the catalog at {self.path} is closed")
        conn = self._local.conn
        if conn is None:
            with self._lock:
                if self._closed:
                    raise BondError(f"the catalog at {self.path} is closed")
                # Used only by this thread; another may close it, with the catalog.
                conn = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None, check_same_thread=False)
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                self._connections.append(conn)
            self._local.conn = conn
        return conn

    @contextmanager
    def _read(self) -> Generator[Exec]:
        yield Exec(self._connection())

    @contextmanager
    def _write(self) -> Generator[Exec]:
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield Exec(conn)
            conn.execute("COMMIT")  # deferred foreign keys are checked here
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _tables(self, ex: Exec) -> set[str]:
        return {str_value(name) for (name,) in ex.all("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def close(self) -> None:
        """Close every thread's connection; the catalog can't be used after."""
        with self._lock:
            self._closed = True
            for conn in self._connections:
                conn.close()
            self._connections.clear()
