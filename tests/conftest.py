from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import LiteralString

import pytest
from psycopg_pool import ConnectionPool

import case_001
from bond import Bond, Citation, Format, VersionId
from bond.authoring import Outcome, Refused, Written
from bond.ids import hash_bytes, version_id
from bond.parsers import JsonDoc, Malformed
from bond.storage import NewVersion
from bond.storage.local import LocalBlobs
from bond.storage.sql import SqlCatalog, SqlValue
from bond.storage.sqlite import SQLiteCatalog

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

# A Postgres DSN to also run the Bond suite and the catalog contract against, e.g.
# BOND_TEST_POSTGRES=postgresql://bond@localhost:55432/bond. Each test gets its own schema.
POSTGRES = os.environ.get("BOND_TEST_POSTGRES")
CATALOGS = ["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not POSTGRES, reason="BOND_TEST_POSTGRES unset"))]


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
def catalog_of(kind: str, tmp_path: Path) -> Generator[SqlCatalog]:
    if kind == "sqlite":
        catalog = SQLiteCatalog(tmp_path / "bond.db")
        try:
            yield catalog
        finally:
            catalog.close()
    else:
        from bond.storage.postgres import PostgresCatalog

        with postgres_pool() as pool:
            yield PostgresCatalog(pool)


@contextmanager
def workspaces_of(kind: str, tmp_path: Path, *names: str) -> Generator[list[SqlCatalog]]:
    """Catalogs on one database, each opened on its own workspace."""
    if kind == "sqlite":
        catalogs: list[SqlCatalog] = [SQLiteCatalog(tmp_path / "bond.db", workspace=name) for name in names]
        try:
            yield catalogs
        finally:
            for catalog in catalogs:
                catalog.close()
    else:
        from bond.storage.postgres import PostgresCatalog

        with postgres_pool() as pool:
            yield [PostgresCatalog(pool, workspace=name) for name in names]


@pytest.fixture(params=CATALOGS)
def bond(request: pytest.FixtureRequest, tmp_path: Path):
    with catalog_of(request.param, tmp_path / "store") as catalog, Bond(catalog, LocalBlobs(tmp_path / "store" / "blobs")) as b:
        yield b


@pytest.fixture
def case(bond: Bond) -> dict[str, VersionId]:
    """Case 001 sources ingested and the fax's OCR output imported: document -> version."""
    return case_001.ingest(bond)


def raw_sql(bond: Bond, sql: LiteralString, params: tuple[SqlValue, ...] = ()) -> None:
    """Run a statement on a store's catalog behind Bond's back, as another code path might (? placeholders)."""
    assert isinstance(bond.catalog, SqlCatalog)
    with bond.catalog._write() as ex:
        ex.run(sql, params)


def store_unchecked(bond: Bond, content: str, citations: list[Citation], document: str) -> VersionId:
    """Store a version the way another code path might: with a valid id, but none of Bond's checks."""
    data = content.encode()
    version = version_id(hash_bytes(data), Format.MARKDOWN, citations)
    bond.blobs.put(hash_bytes(data), data)
    bond.catalog.file(version, document, None, NewVersion(version, hash_bytes(data), Format.MARKDOWN, data, citations))
    return version


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
