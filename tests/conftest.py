from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

import case_001
from bond import Bond, Citation, Format, VersionId, WriteError
from bond.authoring import Outcome, Refused, Written
from bond.ids import dumps, hash_bytes, version_id
from bond.models import Issue
from bond.parsers import JsonDoc, Malformed
from bond.storage import NewVersion
from bond.storage.index import MemoryIndex
from bond.storage.local import LocalBlobs
from bond.storage.sql import SqlStore
from bond.storage.sqlite import SQLiteStore
from bond.storage.store import Key, MemoryStore, Store, key_path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

# A Postgres DSN to also run the Bond suite and the store contract against, e.g.
# BOND_TEST_POSTGRES=postgresql://bond@localhost:55432/bond. Each test gets its own schema.
POSTGRES = os.environ.get("BOND_TEST_POSTGRES")
STORES = ["sqlite", "memory",
          pytest.param("postgres", marks=pytest.mark.skipif(not POSTGRES, reason="BOND_TEST_POSTGRES unset"))]


@contextmanager
def postgres_pool() -> Generator[ConnectionPool]:
    """A connection pool whose connections use a new, empty schema, dropped afterwards."""
    import psycopg
    from psycopg import sql

    assert POSTGRES
    schema = "bond_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(POSTGRES, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with ConnectionPool(POSTGRES, kwargs={"options": f"-c search_path={schema}"}, min_size=1, max_size=8,
                                open=True) as pool:
                yield pool
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@contextmanager
def store_of(kind: str, tmp_path: Path) -> Generator[Store]:
    """A new, empty store of a kind, closed after."""
    if kind == "memory":
        yield MemoryStore()
    elif kind == "sqlite":
        with SQLiteStore(tmp_path / "bond.db") as store:
            yield store
    else:
        from bond.storage.postgres import PostgresStore

        with postgres_pool() as pool:
            yield PostgresStore(pool)


@pytest.fixture(params=STORES)
def bond(request: pytest.FixtureRequest, tmp_path: Path):
    with store_of(request.param, tmp_path / "store") as store, Bond(store, LocalBlobs(tmp_path / "store" / "blobs")) as b:
        yield b


@pytest.fixture
def case(bond: Bond) -> dict[str, VersionId]:
    """Case 001 sources ingested and the fax's OCR output imported: document -> version."""
    return case_001.ingest(bond)


def overwrite(store: Store, key: Key, value: bytes) -> None:
    """Replace a key's value behind Bond's back, as a corrupted store or another code path might: no Store can."""
    if isinstance(store, MemoryStore):
        store._values[key_path(key)] = (key, value)
    elif isinstance(store, SqlStore):
        with store._read() as ex:
            assert ex.run("UPDATE bond_keys SET value = ? WHERE key = ?", (value, key_path(key))) == 1
    else:
        pytest.skip(f"can't overwrite keys in {type(store).__name__}")


def rewrite_record(bond: Bond, version: VersionId, change: Callable[[dict[str, Any]], None]) -> None:
    """Change a version's stored record (its JSON, as bond.storage.records writes it) behind Bond's back."""
    key = (bond.workspace.id, "records", version)
    data = bond.store.get(key)
    assert data is not None, version
    record = json.loads(data)
    change(record)
    overwrite(bond.store, key, dumps(record).encode())
    bond.index = MemoryIndex(bond.records, bond.filing)  # read what was rewritten


def store_unchecked(bond: Bond, content: str, citations: list[Citation], document: str) -> VersionId:
    """Store a version the way another code path might: with a valid id, but none of Bond's checks."""
    data = content.encode()
    version = version_id(hash_bytes(data), Format.MARKDOWN, citations)
    bond.blobs.put(hash_bytes(data), data)
    new = NewVersion(version, hash_bytes(data), Format.MARKDOWN, data, citations)
    bond.filing.file(version, document, None, before=lambda: bond.records.put(new))
    return version


def planned_issues(bond: Bond, document: str, content: str | bytes, citations: list[Citation],
                   **kwargs: Any) -> list[Issue]:
    """Every issue a write would be refused for, filing included, found by planning it: nothing is stored."""
    try:
        bond.plan_write(document, content, citations, **kwargs)
    except WriteError as e:
        return e.errors
    return []


def written(out: Outcome) -> Written:
    """A write or edit's outcome, which must be that it went through."""
    assert isinstance(out, Written), out
    return out


def refused(out: Outcome) -> list[str]:
    """The issues a write or edit was refused for; it must have been refused."""
    assert isinstance(out, Refused), out
    return out.issues


def json_doc(text: str) -> JsonDoc:
    """A JSON document the test knows is well-formed."""
    doc = JsonDoc.parse(text)
    assert not isinstance(doc, Malformed), doc
    return doc
