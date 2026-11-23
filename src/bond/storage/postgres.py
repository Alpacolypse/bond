"""A Catalog in Postgres, for production stores on the host's own database.

Bond doesn't depend on a Postgres driver: the host passes a psycopg 3
connection pool it configured itself (credentials, TLS, schema, sizing).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from ..models import BondError
from .sql import Connection, Exec, SqlCatalog, str_value

_SCHEMA_LOCK = 0x626F6E64  # "bond": an advisory lock key, held while a new database gets its tables


class PoolConnection(Connection, Protocol):
    """What Bond needs of a pooled connection: psycopg's, with its transactions."""

    def transaction(self) -> AbstractContextManager[object]: ...


class Pool(Protocol):
    """What Bond needs of a connection pool: psycopg_pool.ConnectionPool, or anything shaped like it."""

    def connection(self) -> AbstractContextManager[PoolConnection]: ...


class PostgresCatalog(SqlCatalog):
    """A catalog of one workspace in the Postgres database (and schema, by the connections' search_path) that pool
    connects to.

    The bond_ tables are created if none exist (unless create is False, as for
    a viewer, which refuses such a database), under an advisory lock so several
    processes can start at once; a database with another Bond schema is refused.
    One database holds many workspaces, a case each: the workspace is opened by
    name and created if missing (unless create is False); with no name, the
    only one there is. Each call takes its own connection from the pool, so one
    catalog can be shared by every thread. The pool is the host's: close() leaves it open.
    """

    ORDER = "n"
    ORDER_DEFINITION = "n BIGINT GENERATED ALWAYS AS IDENTITY,"

    def __init__(self, pool: Pool, workspace: str | None = None, create: bool = True):
        self.pool = pool
        self._open(workspace, create)

    @contextmanager
    def _read(self) -> Generator[Exec]:
        with self.pool.connection() as conn:
            yield Exec(conn, "%s")

    @contextmanager
    def _write(self) -> Generator[Exec]:
        with self.pool.connection() as conn, conn.transaction():
            yield Exec(conn, "%s")

    def _tables(self, ex: Exec) -> set[str]:
        return {str_value(name) for (name,) in ex.all("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")}

    def _lock_schema(self, ex: Exec) -> None:
        # Names, attrs and locators are text; a database that can't hold it isn't one to start in.
        encoding = ex.one("SHOW server_encoding")
        if encoding is None or encoding[0] != "UTF8":
            raise BondError(f"a Bond catalog needs a UTF8 database, but this one is {encoding and encoding[0]!r}")
        ex.one("SELECT pg_advisory_xact_lock(?)", (_SCHEMA_LOCK,))
