"""A Store in one SQLite file, for development, tests and single-machine stores."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from ..models import BondError
from .sql import Exec, SqlStore, _text


class _Local(threading.local):
    """One thread's connection, opened the first time that thread uses the store."""

    conn: sqlite3.Connection | None = None


class SQLiteStore(SqlStore):
    """A store in a SQLite file, created with its table if it doesn't exist (unless create is False).

    Each thread gets its own connection, so one store can be shared by every thread of a process; separate
    processes can open the same file, waiting up to timeout seconds for another's write to finish. One file holds
    any number of workspaces. Close it when done: its connections are its own.
    """

    KEY_TYPE = "TEXT"  # SQLite compares text by its bytes (the BINARY collation) unless told otherwise
    VALUE_TYPE = "BLOB"

    def __init__(self, path: str | os.PathLike[str], timeout: float = 30.0, create: bool = True):
        self.path = Path(path)
        if not create and not self.path.is_file():
            raise BondError(f"no Bond store at {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._local = _Local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        try:
            self._init_schema(create)
        except BaseException:
            self.close()
            raise

    def _connection(self) -> sqlite3.Connection:
        if self._closed:
            raise BondError(f"the store at {self.path} is closed")
        conn = self._local.conn
        if conn is None:
            with self._lock:
                if self._closed:
                    raise BondError(f"the store at {self.path} is closed")
                # Used only by this thread; another may close it, with the store.
                conn = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None, check_same_thread=False)
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
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _tables(self, ex: Exec) -> set[str]:
        return {_text(name) for (name,) in ex.all("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def close(self) -> None:
        """Close every thread's connection; the store can't be used after."""
        with self._lock:
            self._closed = True
            for conn in self._connections:
                conn.close()
            self._connections.clear()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
