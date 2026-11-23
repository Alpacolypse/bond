"""A Catalog over SQL tables, shared by the SQLite and Postgres backends.

The tables are prefixed bond_, so a catalog can live in a database that holds
other things. One database holds any number of workspaces: every row carries
its workspace's id, in its primary key, and a catalog is opened on one
workspace and puts that id in every statement, so nothing it reads or writes
belongs to another. The same version in two workspaces is two sets of rows
with the same id (its bytes are one blob, by hash).

Filing is a compare-and-swap on the history's primary key (document_id, seq):
a writer appends seq + 1 after the head it read, and if another writer
appended it first, the insert does nothing and the write is refused. Creating
a document is the same, on the unique (workspace, name). The filing row is
inserted first, and a version's rows only once it is in, so a refused write
stores nothing; the history's foreign keys to versions are checked at commit.
Neither needs a lock or a particular isolation level, so the same code is
correct on any SQL database with unique constraints, deferrable foreign keys
and INSERT … ON CONFLICT DO NOTHING.

A support may name a version the workspace doesn't hold: one an import left
outside (see Bond.import_document). So a support's version has no foreign key;
a write checks every support it is given before anything is stored.

A backend supplies connections (_read, _write) and names the column that
orders rows by insertion.

Every statement is a LiteralString: SQL text is built only from literals in
this package, and every value goes in as a parameter, so nothing a caller
passes can become SQL. A type checker refuses a statement built from a runtime
string.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import LiteralString, Protocol

from ..ids import canonical, dumps, supports
from ..jsonvalue import JsonValue
from ..locators import Locator, locator_from_dict
from ..models import (
    SELF,
    BondError,
    Citation,
    CitationKind,
    DocumentId,
    DocumentRef,
    Format,
    HistoryEntry,
    Imported,
    IssueCode,
    Ref,
    StoredCitation,
    UnknownDocument,
    Version,
    VersionId,
    Workspace,
)
from ..parsers import Malformed, load_json
from . import CitationCounts, CitationEdge, Conflict, Filed, NewVersion, filing_problem, stale

SCHEMA_VERSION = 10
TABLES = frozenset({"bond_schema", "bond_workspaces", "bond_versions", "bond_documents", "bond_history",
                    "bond_citations", "bond_supports"})

# The workspace a catalog opened without a name creates, when it holds none.
DEFAULT_WORKSPACE = "default"

# The tables of a store from before the bond_ prefix: refused, rather than ignored beside new ones.
_LEGACY = frozenset({"versions", "document_history", "citation_supports"})

# {n} is the backend's insertion-order column definition, if it needs one.
SCHEMA: list[LiteralString] = [
    "CREATE TABLE bond_schema (version INTEGER NOT NULL)",
    """CREATE TABLE bond_workspaces (
        {n}
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE bond_versions (
        {n}
        workspace    TEXT NOT NULL REFERENCES bond_workspaces(id),
        id           TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        format       TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        PRIMARY KEY (workspace, id)
    )""",
    """CREATE TABLE bond_documents (
        {n}
        id         TEXT PRIMARY KEY,
        workspace  TEXT NOT NULL REFERENCES bond_workspaces(id),
        name       TEXT NOT NULL,
        title      TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (workspace, name)
    )""",
    # A filing row goes in before the version rows it names, so these are checked when the write commits.
    """CREATE TABLE bond_history (
        workspace           TEXT NOT NULL,
        document_id         TEXT NOT NULL REFERENCES bond_documents(id),
        seq                 INTEGER NOT NULL,
        version             TEXT NOT NULL,
        base                TEXT,
        at                  TEXT NOT NULL,
        from_workspace      TEXT,
        from_workspace_name TEXT,
        from_version        TEXT,
        origin              TEXT,
        PRIMARY KEY (document_id, seq),
        FOREIGN KEY (workspace, version) REFERENCES bond_versions(workspace, id) DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (workspace, base) REFERENCES bond_versions(workspace, id) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE bond_citations (
        {n}
        workspace     TEXT NOT NULL,
        owner_version TEXT NOT NULL,
        id            TEXT NOT NULL,
        kind          TEXT NOT NULL,
        claim_locator TEXT NOT NULL,
        attrs         TEXT NOT NULL,
        PRIMARY KEY (workspace, owner_version, id),
        FOREIGN KEY (workspace, owner_version) REFERENCES bond_versions(workspace, id)
    )""",
    """CREATE TABLE bond_supports (
        workspace     TEXT NOT NULL,
        owner_version TEXT NOT NULL,
        citation_id   TEXT NOT NULL,
        position      INTEGER NOT NULL,
        version       TEXT NOT NULL,
        locator       TEXT NOT NULL,
        PRIMARY KEY (workspace, owner_version, citation_id, position),
        FOREIGN KEY (workspace, owner_version, citation_id) REFERENCES bond_citations(workspace, owner_version, id)
    )""",
    "CREATE INDEX bond_history_version ON bond_history(workspace, version)",
    "CREATE INDEX bond_supports_version ON bond_supports(workspace, version)",
]

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

    def many(self, sql: LiteralString, rows: Sequence[Sequence[SqlValue]]) -> None:
        if rows:
            statement = self._sql(sql, len(rows[0]))
            if any(len(r) != len(rows[0]) for r in rows):
                raise RuntimeError(f"rows of different lengths for one statement: {sql!r}")
            self.conn.cursor().executemany(statement, rows)


class SqlCatalog:
    """The Catalog protocol in SQL. Subclasses supply connections and the dialect's few differences."""

    ORDER: LiteralString  # the column that orders a table's rows by insertion
    ORDER_DEFINITION: LiteralString  # its definition in CREATE TABLE, or "" if every table has it implicitly

    workspace: Workspace

    # --- what a backend supplies -------------------------------------------------

    def _read(self) -> AbstractContextManager[Exec]:
        """A connection for reads."""
        raise NotImplementedError

    def _write(self) -> AbstractContextManager[Exec]:
        """A connection inside one transaction: committed if the block returns, rolled back if it raises."""
        raise NotImplementedError

    def _tables(self, ex: Exec) -> set[str]:
        """The names of the tables in the catalog's database (or schema)."""
        raise NotImplementedError

    def _lock_schema(self, ex: Exec) -> None:
        """Keep two catalogs opening a new database at once from both creating its tables."""

    def close(self) -> None:
        pass

    # --- schema and workspace ----------------------------------------------------

    def _open(self, workspace: str | None, create: bool) -> None:
        """Make or check the tables, then open the workspace this catalog reads and writes."""
        self._init_schema(create)
        self.workspace = self._open_workspace(workspace, create)
        self._ws = self.workspace.id

    def _init_schema(self, create: bool) -> None:
        """Create the tables in a database that has none of them, if create; refuse one made with another schema."""
        with self._write() as ex:
            self._lock_schema(ex)
            tables = self._tables(ex)
            ours = tables & TABLES
            if not ours and _LEGACY <= tables:
                raise BondError("this is a store from an older Bond, from before its tables were prefixed bond_; "
                                "re-create it")
            if not ours:
                if not create:
                    raise BondError("this database holds no Bond catalog")
                for statement in SCHEMA:
                    ex.run(statement.format(n=self.ORDER_DEFINITION))
                ex.run("INSERT INTO bond_schema (version) VALUES (?)", (SCHEMA_VERSION,))
                return
            found = ex.one("SELECT version FROM bond_schema") if "bond_schema" in ours else None
            if found is None or found[0] != SCHEMA_VERSION or ours != TABLES:
                schema = "unknown" if found is None else repr(found[0])
                raise BondError(f"this catalog has Bond schema {schema}, but this Bond needs {SCHEMA_VERSION}; "
                                "re-create it")

    def _open_workspace(self, name: str | None, create: bool) -> Workspace:
        """The workspace named name, created if create; with no name, the only one there is (or a new default)."""
        if name is not None and not name.strip():
            raise ValueError("a workspace's name must be a non-empty string")
        with self._write() as ex:
            if name is None:
                rows = ex.all(f"SELECT id, name, created_at FROM bond_workspaces ORDER BY {self.ORDER}")
                if len(rows) > 1:
                    names = ", ".join(repr(r[1]) for r in rows)
                    raise BondError(f"this catalog holds {len(rows)} workspaces ({names}); open one by name")
                if rows:
                    return _workspace(rows[0])
                name = DEFAULT_WORKSPACE
            query: LiteralString = "SELECT id, name, created_at FROM bond_workspaces WHERE name = ?"
            if (r := ex.one(query, (name,))) is not None:
                return _workspace(r)
            if not create:
                raise BondError(f"this catalog holds no workspace named {name!r}")
            # Another catalog creating the same workspace at once inserts nothing here; both then read its row.
            ex.run("INSERT INTO bond_workspaces (id, name, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                   ("ws_" + uuid.uuid4().hex, name, _now()))
            r = ex.one(query, (name,))
        if r is None:
            raise BondError(f"the workspace {name!r} was created, but can't be read back")
        return _workspace(r)

    # --- versions ----------------------------------------------------------------

    _COLS: LiteralString = "id, content_hash, format, created_at"

    def get_version(self, v: VersionId) -> Version | None:
        with self._read() as ex:
            r = ex.one(f"SELECT {self._COLS} FROM bond_versions WHERE workspace = ? AND id = ?", (self._ws, v))
        return _version(r) if r else None

    def all_versions(self) -> list[Version]:
        with self._read() as ex:
            return [_version(r) for r in ex.all(
                f"SELECT {self._COLS} FROM bond_versions WHERE workspace = ? ORDER BY {self.ORDER}", (self._ws,)
            )]

    def head_versions(self) -> list[Version]:
        with self._read() as ex:
            return [_version(r) for r in ex.all(
                f"SELECT {self._COLS} FROM bond_versions WHERE workspace = ? AND id IN (SELECT h.version "
                "FROM bond_history h WHERE h.workspace = ? "
                "AND h.seq = (SELECT MAX(seq) FROM bond_history WHERE document_id = h.document_id)) "
                f"ORDER BY {self.ORDER}",
                (self._ws, self._ws),
            )]

    def put_version(self, new: NewVersion) -> None:
        with self._write() as ex:
            self._insert_version(ex, new, _now())

    def outside_versions(self) -> list[VersionId]:
        with self._read() as ex:
            rows = ex.all(
                "SELECT s.version FROM bond_supports s JOIN bond_citations c "
                "ON c.workspace = s.workspace AND c.owner_version = s.owner_version AND c.id = s.citation_id "
                "WHERE s.workspace = ? AND NOT EXISTS "
                "(SELECT 1 FROM bond_versions v WHERE v.workspace = s.workspace AND v.id = s.version) "
                f"GROUP BY s.version ORDER BY MIN(c.{self.ORDER})",
                (self._ws,),
            )
        return [VersionId(str_value(v)) for (v,) in rows]

    # --- filing ------------------------------------------------------------------

    def file(
        self, v: VersionId, document: str, base: VersionId | None, new: NewVersion | None = None,
        imported: Imported | None = None, origin: str | None = None,
    ) -> Filed | Conflict:
        now = _now()
        with self._write() as ex:
            # The head and its seq in one read: the append at seq + 1 then collides with any write since.
            r = ex.one(self._doc_query(", h.seq") + "AND d.name = ?", (self._ws, document))
            doc, seq = (_document_ref(r[:5]), int_value(r[5])) if r else (None, 0)
            if problem := filing_problem(doc, document, base, v):
                return problem
            if doc is not None and doc.head == v:
                return Filed(doc, False)
            filed = self._create(ex, document, v, now, imported, origin) if doc is None else self._advance(
                ex, doc, seq, v, now, imported, origin)
            # Only once the document is filed: a refused write stores nothing.
            if isinstance(filed, Filed) and new is not None:
                self._insert_version(ex, new, now)
            return filed

    def _create(
        self, ex: Exec, document: str, v: VersionId, now: str, imported: Imported | None, origin: str | None,
    ) -> Filed | Conflict:
        """A new document with v as its first entry; another write creating it first is a conflict."""
        doc_id = DocumentId("doc_" + uuid.uuid4().hex)
        if ex.run("INSERT INTO bond_documents (id, workspace, name, created_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT DO NOTHING", (doc_id, self._ws, document, now)) == 0:
            return Conflict(IssueCode.DOCUMENT_EXISTS, f"document {document!r} was created by another write",
                            "read its head and pass it as base to write its next version")
        self._append(ex, doc_id, 1, v, None, now, imported, origin)
        return Filed(DocumentRef(doc_id, document, v, now), True)

    def _advance(
        self, ex: Exec, doc: DocumentRef, seq: int, v: VersionId, now: str, imported: Imported | None,
        origin: str | None,
    ) -> Filed | Conflict:
        """Append v after the head read, at seq, which the writer started from; another write appending first is the
        compare-and-swap failing."""
        if not self._append(ex, doc.id, seq + 1, v, doc.head, now, imported, origin):
            return stale(doc.name, doc.head, None)
        return Filed(replace(doc, head=v), True)

    def _append(
        self, ex: Exec, doc_id: DocumentId, seq: int, v: VersionId, base: VersionId | None, now: str,
        imported: Imported | None, origin: str | None,
    ) -> bool:
        """Append a history entry at seq; False if there is one there already."""
        source = (imported.workspace, imported.workspace_name, imported.version) if imported else (None, None, None)
        return ex.run(
            "INSERT INTO bond_history (workspace, document_id, seq, version, base, at, from_workspace, "
            "from_workspace_name, from_version, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (self._ws, doc_id, seq, v, base, now, *source, origin),
        ) == 1

    def _insert_version(self, ex: Exec, new: NewVersion, now: str) -> None:
        """Insert a version's row and its citations, unless it is stored already (by this or a concurrent write)."""
        inserted = ex.run(
            "INSERT INTO bond_versions (workspace, id, content_hash, format, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (self._ws, new.id, new.content_hash, new.format.value, now),
        )
        if inserted == 1:
            self._insert_citations(ex, new.id, new.citations)

    def _insert_citations(self, ex: Exec, v: VersionId, citations: Sequence[Citation]) -> None:
        """Insert a version's citations, each once and in id order; supports inside it (SELF) are stored under its id."""
        for cid, c in canonical(citations):
            ex.run(
                "INSERT INTO bond_citations (workspace, owner_version, id, kind, claim_locator, attrs) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._ws, v, cid, c.kind.value, dumps(c.claim.to_dict()), dumps(c.attrs)),
            )
            ex.many(
                "INSERT INTO bond_supports (workspace, owner_version, citation_id, position, version, locator) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(self._ws, v, cid, j, v if sv == SELF else sv, dumps(loc))
                 for j, (sv, loc) in enumerate(supports(c))],
            )

    # --- documents ---------------------------------------------------------------

    def _doc_query(self, extra: LiteralString = "") -> LiteralString:
        """Documents with their heads, in this workspace: one parameter, the workspace; callers add AND …"""
        return (
            f"SELECT d.id, d.name, h.version, d.created_at, d.title{extra} FROM bond_documents d "
            "JOIN bond_history h "
            "ON h.document_id = d.id AND h.seq = (SELECT MAX(seq) FROM bond_history WHERE document_id = d.id) "
            "WHERE d.workspace = ? "
        )

    def _document(self, ex: Exec, where: LiteralString, params: Sequence[SqlValue]) -> DocumentRef | None:
        r = ex.one(self._doc_query() + f"AND {where}", (self._ws, *params))
        return _document_ref(r) if r else None

    def get_document(self, name: str) -> DocumentRef | None:
        with self._read() as ex:
            return self._document(ex, "d.name = ?", (name,))

    def all_documents(self) -> list[DocumentRef]:
        with self._read() as ex:
            return [_document_ref(r) for r in ex.all(self._doc_query() + f"ORDER BY d.{self.ORDER}", (self._ws,))]

    def documents_holding(self, v: VersionId) -> list[DocumentRef]:
        with self._read() as ex:
            return [_document_ref(r) for r in ex.all(
                self._doc_query() + "AND d.id IN (SELECT document_id FROM bond_history WHERE workspace = ? "
                f"AND version = ?) ORDER BY d.{self.ORDER}",
                (self._ws, self._ws, v),
            )]

    def history(self, doc: DocumentId) -> list[HistoryEntry]:
        with self._read() as ex:
            rows = ex.all(
                "SELECT seq, version, base, at, from_workspace, from_workspace_name, from_version, origin "
                "FROM bond_history WHERE workspace = ? AND document_id = ? ORDER BY seq",
                (self._ws, doc),
            )
        return [
            HistoryEntry(int_value(seq), VersionId(str_value(v)), None if base is None else VersionId(str_value(base)), str_value(at),
                         None if ws is None else Imported(str_value(ws), str_value(ws_name), VersionId(str_value(from_v))),
                         None if origin is None else str_value(origin))
            for seq, v, base, at, ws, ws_name, from_v, origin in rows
        ]

    def retitle(self, name: str, title: str | None) -> DocumentRef:
        with self._write() as ex:
            doc = self._document(ex, "d.name = ?", (name,))
            if doc is None:
                raise UnknownDocument(name)
            ex.run("UPDATE bond_documents SET title = ? WHERE workspace = ? AND id = ?", (title, self._ws, doc.id))
        return replace(doc, title=title)

    # --- citations ---------------------------------------------------------------

    def citations_of(self, v: VersionId) -> list[StoredCitation]:
        return self._citations("c.owner_version = ?", (v,))

    def citations_supported_by(self, v: VersionId) -> list[StoredCitation]:
        return self._citations(
            "(c.owner_version, c.id) IN (SELECT owner_version, citation_id FROM bond_supports "
            "WHERE workspace = ? AND version = ?)",
            (self._ws, v),
        )

    def citation_counts(self) -> dict[VersionId, CitationCounts]:
        with self._read() as ex:
            rows = ex.all(
                "SELECT c.owner_version, COUNT(*), "
                "SUM(CASE WHEN EXISTS (SELECT 1 FROM bond_supports s "
                "     WHERE s.workspace = c.workspace AND s.owner_version = c.owner_version AND s.citation_id = c.id "
                "     AND s.version = c.owner_version) THEN 1 ELSE 0 END), "
                "MAX(CASE WHEN c.kind = ? AND c.claim_locator = ? THEN 1 ELSE 0 END) "
                "FROM bond_citations c WHERE c.workspace = ? GROUP BY c.owner_version",
                (CitationKind.SOURCE.value, dumps({"type": "version"}), self._ws),
            )
        return {VersionId(str_value(v)): CitationCounts(int_value(n), int_value(n_self), bool(whole_source))
                for v, n, n_self, whole_source in rows}

    def citation_edges(self) -> list[CitationEdge]:
        with self._read() as ex:
            rows = ex.all(
                "SELECT c.owner_version, s.version, c.kind, COUNT(DISTINCT c.id) FROM bond_citations c "
                "JOIN bond_supports s ON s.workspace = c.workspace AND s.owner_version = c.owner_version "
                "AND s.citation_id = c.id "
                "WHERE c.workspace = ? AND s.version != c.owner_version "
                f"GROUP BY c.owner_version, s.version, c.kind ORDER BY MIN(c.{self.ORDER})",
                (self._ws,),
            )
        return [CitationEdge(VersionId(str_value(a)), VersionId(str_value(b)), CitationKind(str_value(kind)), int_value(n))
                for a, b, kind, n in rows]

    def _citations(self, where: LiteralString, params: Sequence[SqlValue]) -> list[StoredCitation]:
        with self._read() as ex:
            rows = ex.all(
                "SELECT c.id, c.owner_version, c.kind, c.claim_locator, c.attrs "
                f"FROM bond_citations c WHERE c.workspace = ? AND {where} ORDER BY c.{self.ORDER}",
                (self._ws, *params),
            )
            support_rows = ex.all(
                "SELECT s.owner_version, s.citation_id, s.version, s.locator FROM bond_supports s "
                "JOIN bond_citations c ON c.workspace = s.workspace AND c.owner_version = s.owner_version "
                f"AND c.id = s.citation_id WHERE c.workspace = ? AND {where} ORDER BY s.position",
                (self._ws, *params),
            )
        found: dict[tuple[str, str], list[Ref]] = {(str_value(r[1]), str_value(r[0])): [] for r in rows}
        for owner, cid, v, loc in support_rows:
            found[(str_value(owner), str_value(cid))].append(Ref(VersionId(str_value(v)), _locator(loc)))
        return [
            StoredCitation(
                id=str_value(cid), owner_version=VersionId(str_value(owner)), kind=CitationKind(str_value(kind)),
                claim=_locator(claim), supports=found[(str_value(owner), str_value(cid))], attrs=_attrs(attrs),
            )
            for cid, owner, kind, claim, attrs in rows
        ]


# --- reading rows: values the schema puts there, checked as they come out --------------------------


def str_value(value: SqlValue) -> str:
    if not isinstance(value, str):
        raise BondError(f"the catalog holds {value!r} where text belongs; it is corrupt")
    return value


def int_value(value: SqlValue) -> int:
    if not isinstance(value, int):
        raise BondError(f"the catalog holds {value!r} where an integer belongs; it is corrupt")
    return value


def _object(value: SqlValue) -> Mapping[str, JsonValue]:
    loaded = load_json(str_value(value))
    if not isinstance(loaded, dict):
        reason = loaded.reason if isinstance(loaded, Malformed) else "not an object"
        raise BondError(f"the catalog holds {value!r} where a JSON object belongs ({reason}); it is corrupt")
    return loaded


def _locator(value: SqlValue) -> Locator:
    return locator_from_dict(_object(value))


def _attrs(value: SqlValue) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, v in _object(value).items():
        if not isinstance(v, str):
            raise BondError(f"the catalog holds attrs {value!r}, whose values must be producer ids; it is corrupt")
        attrs[key] = v
    return attrs


def _workspace(r: Row) -> Workspace:
    ws_id, name, created_at = r
    return Workspace(str_value(ws_id), str_value(name), str_value(created_at))


def _version(r: Row) -> Version:
    v, content_hash, fmt, created_at = r
    return Version(VersionId(str_value(v)), str_value(content_hash), Format(str_value(fmt)), str_value(created_at))


def _document_ref(r: Row) -> DocumentRef:
    doc_id, name, head, created_at, title = r
    return DocumentRef(DocumentId(str_value(doc_id)), str_value(name), VersionId(str_value(head)), str_value(created_at),
                       None if title is None else str_value(title))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
