"""A Store in one SQL table, shared by the SQLite and Postgres backends.

Every key Bond stores is a row of bond_keys: its key_path, primary key, and its value. Creating a key is an
INSERT … ON CONFLICT DO NOTHING, so of two writers creating one key, the database lets exactly one through, with no
lock and at any isolation level. Listing is a range scan on the primary key: key paths keep Bond's key order as
byte order (see bond.storage.store.key_path), so the key column compares bytes (SQLite's default, Postgres's "C"
collation).

The table is prefixed bond_, so a store can live in a database that holds other things. One database holds any
number of workspaces: their keys start with their ids.

A backend supplies connections (_read, _write) and the dialect's column types.

Every statement is a LiteralString: SQL text is built only from literals in this package, and every value goes in
as a parameter, so nothing a caller passes can become SQL. A type checker refuses a statement built from a runtime
string.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from typing import LiteralString, Protocol

from ..models import BondError
from .store import Key, key_from_path, key_path

SCHEMA_VERSION = 11
TABLES = frozenset({"bond_schema", "bond_keys"})

# A value as it goes into a statement or comes out of a row.
SqlValue = str | int | float | bytes | None
Row = tuple[SqlValue, ...]


class Cursor(Protocol):
    """What Bond needs of a DB-API cursor: sqlite3's, or psycopg's."""

    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Row | None: ...

    def fetchall(self) -> Sequence[Row]: ...

    def executemany(self, sql: LiteralString, rows: Sequence[Sequence[SqlValue]], /) -> object: ...


class Connection(Protocol):
    """What Bond needs of a DB-API connection: sqlite3's, or psycopg's."""

    def execute(self, sql: LiteralString, params: Sequence[SqlValue], /) -> Cursor: ...

    def cursor(self) -> Cursor: ...


class Exec:
    """Runs statements on one connection, written with ? placeholders whatever the driver's style.

    The ? are rewritten to the driver's placeholder, so a statement's ? must be
    exactly its parameters, one each: a ? that is anything else (inside a string
    literal, or Postgres's JSON operator) is refused. A driver with another
    placeholder (psycopg's %s) gives % a meaning too, so a statement for it may
    not contain one (as in LIKE 'a%'). Either is a bug in the statement, raised
    as a RuntimeError.
    """

    def __init__(self, conn: Connection, placeholder: LiteralString = "?"):
        self.conn = conn
        self.placeholder = placeholder

    def _sql(self, sql: LiteralString, params: int) -> LiteralString:
        if (marks := sql.count("?")) != params:
            raise RuntimeError(f"statement has {marks} ? for {params} parameters: {sql!r}")
        if self.placeholder == "?":
            return sql
        if "%" in sql:
            raise RuntimeError(f"statement contains %, which {self.placeholder!r} placeholders reserve: {sql!r}")
        return sql.replace("?", self.placeholder)

    def all(self, sql: LiteralString, params: Sequence[SqlValue] = ()) -> list[Row]:
        return list(self.conn.execute(self._sql(sql, len(params)), params).fetchall())

    def one(self, sql: LiteralString, params: Sequence[SqlValue] = ()) -> Row | None:
        return self.conn.execute(self._sql(sql, len(params)), params).fetchone()

    def run(self, sql: LiteralString, params: Sequence[SqlValue] = ()) -> int:
        """Run a statement; the number of rows it changed."""
        return self.conn.execute(self._sql(sql, len(params)), params).rowcount


class SqlStore:
    """The Store protocol (and LastKey) in SQL. Subclasses supply connections and the dialect's column types."""

    KEY_TYPE: LiteralString  # a text type that compares bytes
    VALUE_TYPE: LiteralString  # a binary type

    # --- what a backend supplies -------------------------------------------------

    def _read(self) -> AbstractContextManager[Exec]:
        """A connection for reads, and for single-statement writes."""
        raise NotImplementedError

    def _write(self) -> AbstractContextManager[Exec]:
        """A connection inside one transaction: committed if the block returns, rolled back if it raises."""
        raise NotImplementedError

    def _tables(self, ex: Exec) -> set[str]:
        """The names of the tables in the store's database (or schema)."""
        raise NotImplementedError

    def _lock_schema(self, ex: Exec) -> None:
        """Keep two stores opening a new database at once from both creating its tables."""

    # --- the schema ----------------------------------------------------------------

    def _init_schema(self, create: bool) -> None:
        """Create the tables in a database that has none of Bond's, if create; refuse one made by another Bond."""
        with self._write() as ex:
            self._lock_schema(ex)
            ours = {t for t in self._tables(ex) if t.startswith("bond_")}
            if not ours:
                if not create:
                    raise BondError("this database holds no Bond store")
                ex.run("CREATE TABLE bond_schema (version INTEGER NOT NULL)")
                ex.run(f"CREATE TABLE bond_keys (key {self.KEY_TYPE} PRIMARY KEY, value {self.VALUE_TYPE} NOT NULL)")
                ex.run("INSERT INTO bond_schema (version) VALUES (?)", (SCHEMA_VERSION,))
                return
            found = ex.one("SELECT version FROM bond_schema") if "bond_schema" in ours else None
            if found is None or found[0] != SCHEMA_VERSION or ours != TABLES:
                schema = "unknown" if found is None else repr(found[0])
                raise BondError(f"this database has Bond schema {schema}, but this Bond needs {SCHEMA_VERSION}; "
                                "re-create it")

    # --- Store ---------------------------------------------------------------------

    def create(self, key: Key, value: bytes) -> bool:
        with self._read() as ex:
            return ex.run("INSERT INTO bond_keys (key, value) VALUES (?, ?) ON CONFLICT DO NOTHING",
                          (key_path(key), bytes(value))) == 1

    def get(self, key: Key) -> bytes | None:
        with self._read() as ex:
            r = ex.one("SELECT value FROM bond_keys WHERE key = ?", (key_path(key),))
        return None if r is None else _bytes(r[0])

    def list(self, prefix: Key, after: Key | None = None) -> Iterator[Key]:
        low, high = _bounds(prefix)
        past = "" if after is None else key_path(after)  # every key is past "", since none is empty
        with self._read() as ex:
            if high is None:
                rows = ex.all("SELECT key FROM bond_keys WHERE key >= ? AND key > ? ORDER BY key", (low, past))
            else:
                rows = ex.all("SELECT key FROM bond_keys WHERE key >= ? AND key < ? AND key > ? ORDER BY key",
                              (low, high, past))
        return iter([key_from_path(_text(k)) for (k,) in rows])

    def last(self, prefix: Key) -> Key | None:
        low, high = _bounds(prefix)
        with self._read() as ex:
            if high is None:
                r = ex.one("SELECT key FROM bond_keys WHERE key >= ? ORDER BY key DESC LIMIT 1", (low,))
            else:
                r = ex.one("SELECT key FROM bond_keys WHERE key >= ? AND key < ? ORDER BY key DESC LIMIT 1",
                           (low, high))
        return None if r is None else key_from_path(_text(r[0]))


def _bounds(prefix: Key) -> tuple[str, str | None]:
    """The key paths under prefix: from the first (inclusive) to the second (exclusive; None for no bound).

    Every path under prefix starts with its path and "/"; "0" is the character after "/", so they all sort below
    the path and "0", and every path between the two is under prefix.
    """
    if not prefix:
        return "", None
    path = key_path(prefix)
    return path + "/", path + "0"


def _bytes(value: SqlValue | memoryview) -> bytes:
    if isinstance(value, bytes | memoryview):
        return bytes(value)
    raise BondError(f"the store holds {value!r} where bytes belong; it is corrupt")


def _text(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise BondError(f"the store holds {value!r} where a key belongs; it is corrupt")
    return value
