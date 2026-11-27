"""The storage contract: what Bond's records, filing and index do on every Store, and what every Blobs backend must do.

A store is supported when Bond's layers pass these on it (and the store passes test_store.py). Most of the
contract is concurrency: one workspace shared by many threads, writers racing on one document's head, and the same
version stored by several writers at once.
"""

from __future__ import annotations

import base64
import os
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from types_boto3_s3 import S3Client

import case_001
from bond import (
    Bond,
    BondError,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    Ref,
    SelfRef,
    TextLocator,
    UnknownDocument,
    VersionId,
    VersionLocator,
    details,
)
from bond.ids import citation_id, hash_bytes, version_id
from bond.models import DocumentRef, Imported, Version
from bond.storage import Blobs, CitationEdge, Conflict, Filed, NewVersion
from bond.storage.local import LocalBlobs, MemoryBlobs
from bond.storage.s3 import S3Blobs
from bond.storage.sql import Exec, Row, SqlValue
from bond.storage.sqlite import SQLiteStore
from conftest import POSTGRES, STORES, postgres_pool, store_of

THREADS = 8


def new_version(text: str, citations: list[Citation] | None = None) -> NewVersion:
    data = text.encode()
    cited = citations if citations is not None else [Citation(CitationKind.SOURCE, VersionLocator(), [])]
    return NewVersion(version_id(hash_bytes(data), Format.MARKDOWN, cited), hash_bytes(data), Format.MARKDOWN, data,
                      cited)


def filed(result: Filed | Conflict) -> Filed:
    """A filing that went through."""
    assert isinstance(result, Filed), result
    return result


def file_in(bond: Bond, new: NewVersion, document: str, base: VersionId | None, **entry: Any) -> Filed | Conflict:
    """File new in document from base, storing its record first, as a commit does."""
    return bond.filing.file(new.id, document, base, before=lambda: bond.records.put(new), **entry)


def store(bond: Bond, new: NewVersion, document: str, base: VersionId | None = None) -> VersionId:
    filed(file_in(bond, new, document, base))
    return new.id


def heads(bond: Bond) -> list[Version]:
    """The versions that are some document's head, oldest first."""
    heads = {d.head for d in bond.filing.documents()}
    return [v for v in bond.index.versions() if v.id in heads]


def run_threads(n: int, work: Callable[[int], None]) -> None:
    """Run work(0..n-1) on n threads at once, and raise what any of them raised."""
    start = threading.Barrier(n)

    def run(i: int) -> None:
        start.wait()
        work(i)

    with ThreadPoolExecutor(n) as pool:
        for future in [pool.submit(run, i) for i in range(n)]:
            future.result()


# --- one workspace on each store --------------------------------------------------------


@pytest.fixture(params=STORES)
def ws(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Bond]:
    """A new workspace's records, filing and index, on each kind of store."""
    with store_of(request.param, tmp_path) as s, Bond(s, MemoryBlobs()) as b:
        yield b


def test_a_new_workspace_is_empty(ws):
    assert ws.index.versions() == [] and ws.filing.documents() == [] and heads(ws) == []
    assert ws.records.version(VersionId("0" * 64)) is None and ws.filing.document("note") is None


def test_filing_creates_and_advances_a_document(ws):
    v1 = new_version("One.")
    first = filed(file_in(ws, v1, "note", None))
    doc = first.document
    assert first.advanced and doc.name == "note" and doc.head == v1.id and ws.filing.document("note") == doc
    v2 = new_version("Two.")
    second = filed(file_in(ws, v2, "note", v1.id))
    assert second.advanced and second.document.id == doc.id and second.document.head == v2.id
    assert [(e.seq, e.version, e.base) for e in ws.filing.history(doc.id)] == [(1, v1.id, None), (2, v2.id, v1.id)]
    assert [v.id for v in ws.index.versions()] == [v1.id, v2.id]
    assert [v.id for v in heads(ws)] == [v2.id]


def test_filing_the_head_again_is_a_no_op(ws):
    v1 = new_version("One.")
    store(ws, v1, "note")
    again = filed(file_in(ws, v1, "note", None))
    assert not again.advanced and again.document.head == v1.id and len(ws.filing.history(again.document.id)) == 1


@pytest.mark.parametrize(("document", "base", "code"), [
    ("note", None, IssueCode.DOCUMENT_EXISTS),
    ("missing", "v1", IssueCode.UNKNOWN_DOCUMENT),
    ("note", "v0", IssueCode.STALE_BASE),
])
def test_a_filing_conflict_stores_nothing(ws, document, base, code):
    v0, v1 = new_version("Zero."), new_version("One.")
    store(ws, v0, "note")
    store(ws, v1, "note", v0.id)
    late = new_version("Late.")
    conflict = file_in(ws, late, document, None if base is None else {"v0": v0.id, "v1": v1.id}[base])
    assert isinstance(conflict, Conflict) and conflict.code is code
    assert ws.records.version(late.id) is None and ws.records.citations(late.id) == []


def test_one_version_in_many_documents_is_stored_once(ws):
    v = new_version("Fax text.")
    store(ws, v, "case_002/fax")
    store(ws, v, "case_007/fax")
    assert len(ws.index.versions()) == 1 and len(ws.records.citations(v.id)) == 1
    assert [d.name for d in ws.index.holding(v.id)] == ["case_002/fax", "case_007/fax"]


def test_citations_round_trip_with_supports_inside_and_outside(ws):
    src = new_version("Six weeks of written notice.")
    store(ws, src, "src")
    text = "Six weeks of PT. Six weeks."
    first, second = TextLocator(0, 16), TextLocator(17, 27)
    cited = [
        Citation(CitationKind.PARAPHRASE, first, [Ref(src.id, TextLocator(0, 30))]),
        Citation(CitationKind.PARAPHRASE, second, [SelfRef(first)]),
    ]
    note = new_version(text, cited)
    store(ws, note, "note")
    by_claim = {c.claim: c for c in ws.records.citations(note.id)}
    assert by_claim[first].supports == [Ref(src.id, TextLocator(0, 30))]
    assert by_claim[second].supports == [Ref(note.id, first)]  # SELF is stored under the version's own id
    # and read back as written
    assert sorted(citation_id(c.as_written()) for c in ws.records.citations(note.id)) == sorted(map(citation_id, cited))
    assert [c.owner_version for c in ws.index.supported_by(src.id)] == [note.id]
    assert ws.index.counts()[note.id].citations == 2
    assert ws.index.counts()[note.id].self_supported == 1
    assert ws.index.counts()[src.id].whole_source
    assert ws.index.edges() == [CitationEdge(note.id, src.id, CitationKind.PARAPHRASE, 1)]


def test_retitle(ws):
    store(ws, new_version("A."), "a")
    a = ws.filing.document("a")
    assert a is not None and a.title is None
    titled = ws.filing.retitle("a", "Note A")
    assert (titled.id, titled.title) == (a.id, "Note A")
    assert ws.filing.retitle("a", None).title is None
    with pytest.raises(UnknownDocument):
        ws.filing.retitle("missing", "anything")


# --- workspaces ------------------------------------------------------------------------


@pytest.fixture(params=STORES)
def two(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[list[Bond]]:
    """Two workspaces, case-001 and case-002, in one store."""
    with store_of(request.param, tmp_path) as s:
        yield [Bond(s, MemoryBlobs(), "case-001"), Bond(s, MemoryBlobs(), "case-002")]


def test_each_bond_is_opened_on_its_own_workspace(two):
    a, b = two
    assert (a.workspace.name, b.workspace.name) == ("case-001", "case-002")
    assert a.workspace.id != b.workspace.id and a.workspace.id.startswith("ws_")


def test_nothing_in_one_workspace_is_visible_from_another(two):
    """Every read of the protocol, on a database where two workspaces share a policy and a document name."""
    a, b = two
    policy = new_version("Six weeks of written notice.")
    store(a, policy, "policy")
    store(b, policy, "policy")  # the same version, the same name: in each workspace, its own
    ref = Ref(policy.id, TextLocator(0, 9))
    cited = [Citation(CitationKind.PARAPHRASE, TextLocator(0, 13), [ref])]
    secret = new_version("Tenant A met", cited)
    store(a, secret, "determination")
    store(a, new_version("Only in A."), "a-only")

    assert [v.id for v in b.index.versions()] == [policy.id] == [v.id for v in heads(b)]
    assert b.records.version(secret.id) is None and b.records.citations(secret.id) == []
    assert b.index.supported_by(policy.id) == []  # who cites the shared policy is case data
    assert [c.owner_version for c in a.index.supported_by(policy.id)] == [secret.id]
    assert b.index.edges() == [] and set(b.index.counts()) == {policy.id}
    assert [d.name for d in b.filing.documents()] == ["policy"] and b.filing.document("a-only") is None
    a_policy, b_policy = a.filing.document("policy"), b.filing.document("policy")
    assert a_policy is not None and b_policy is not None and a_policy.id != b_policy.id
    assert b.index.holding(secret.id) == [] and b.filing.history(a_policy.id) == []
    assert b.index.outside() == [] == a.index.outside()


def test_titles_change_in_one_workspace_only(two):
    a, b = two
    store(a, new_version("A."), "note")
    store(b, new_version("B."), "note")
    b.filing.retitle("note", "B's note")
    a_note = a.filing.document("note")
    assert a_note is not None and a_note.title is None
    b.filing.retitle("note", None)
    with pytest.raises(UnknownDocument):
        a.filing.retitle("b-only", "anything")


def test_a_workspace_opened_without_a_name_needs_to_be_the_only_one(tmp_path):
    with SQLiteStore(tmp_path / "bond.db") as store:
        Bond(store, MemoryBlobs(), "case-001")
        Bond(store, MemoryBlobs(), "case-002")
        with pytest.raises(BondError, match="holds 2 workspaces.*open one by name"):
            Bond(store, MemoryBlobs())
        with pytest.raises(BondError, match="no workspace named 'case-003'"):
            Bond(store, MemoryBlobs(), "case-003", create=False)
    with SQLiteStore(tmp_path / "other.db") as other:
        assert Bond(other, MemoryBlobs()).workspace.name == "default"


def test_a_version_can_be_stored_without_a_document(ws):
    v = new_version("Evidence a summary rests on.")
    ws.records.put(v)
    ws.records.put(v)  # stored once
    assert [x.id for x in ws.index.versions()] == [v.id] and heads(ws) == []
    assert ws.index.holding(v.id) == [] and len(ws.records.citations(v.id)) == 1


def test_supports_may_name_versions_outside_the_workspace(ws):
    """An import brings a version without what it rests on; its supports name versions that aren't here."""
    elsewhere = new_version("Evidence that stayed behind.")
    cited = [Citation(CitationKind.PARAPHRASE, TextLocator(0, 7), [Ref(elsewhere.id, TextLocator(0, 8))])]
    summary = new_version("Summary", cited)
    ws.records.put(summary)
    assert ws.index.outside() == [elsewhere.id]
    assert [s.version for c in ws.records.citations(summary.id) for s in c.supports] == [elsewhere.id]
    ws.records.put(elsewhere)
    assert ws.index.outside() == []


def test_history_records_where_an_import_came_from(ws):
    v = new_version("A policy.")
    origin = Imported("ws_" + "a" * 32, "policies", v.id)
    doc = filed(file_in(ws, v, "policy", None, imported=origin)).document
    assert [e.imported for e in ws.filing.history(doc.id)] == [origin]
    assert [e.origin for e in ws.filing.history(doc.id)] == [None]


def test_history_records_where_an_ingested_file_came_from(ws):
    v1, v2 = new_version("Faxed page one."), new_version("Faxed page two.")
    doc = filed(file_in(ws, v1, "fax", None, origin="Cases/001/fax.pdf")).document
    filed(file_in(ws, v2, "fax", v1.id))
    assert [(e.origin, e.imported) for e in ws.filing.history(doc.id)] == [("Cases/001/fax.pdf", None), (None, None)]


def test_threads_share_one_workspace(ws):
    per_thread = 25

    def work(t: int) -> None:
        for i in range(per_thread):
            store(ws, new_version(f"Thread {t}, document {i}."), f"t{t}/{i}")

    run_threads(THREADS, work)
    docs = ws.filing.documents()
    assert len(docs) == len(ws.index.versions()) == THREADS * per_thread
    assert all(len(ws.filing.history(d.id)) == 1 for d in docs)


def test_writers_racing_on_one_head_form_one_unbroken_history(ws):
    # Every writer reads the head, writes from it, and on a stale base reads again. The compare-and-swap
    # must let exactly one writer through from each head: the history is one chain, each entry's base the
    # entry before it, with every write in it exactly once.
    first = new_version("Start.")
    store(ws, first, "race")
    per_thread = 10

    def head() -> DocumentRef:
        doc = ws.filing.document("race")
        assert doc is not None
        return doc

    def work(t: int) -> None:
        for i in range(per_thread):
            new = new_version(f"Writer {t}, edit {i}.")
            while isinstance(result := file_in(ws, new, "race", head().head), Conflict):
                assert result.code is IssueCode.STALE_BASE  # another writer moved it: read the head again

    run_threads(THREADS, work)
    history = ws.filing.history(head().id)
    assert [e.seq for e in history] == list(range(1, THREADS * per_thread + 2))
    assert all(e.base == prev.version for prev, e in zip(history, history[1:], strict=False))
    assert len({e.version for e in history}) == len(history)
    assert len(ws.index.versions()) == len(history)  # a refused write stored nothing


def test_writers_storing_the_same_version_at_once_store_it_once(ws):
    src = new_version("Shared evidence.")

    def work(t: int) -> None:
        store(ws, src, f"case_{t}/fax")

    run_threads(THREADS, work)
    assert len(ws.index.versions()) == 1 and len(ws.records.citations(src.id)) == 1
    assert len(ws.index.holding(src.id)) == THREADS


def test_writers_creating_one_document_at_once(ws):
    def work(t: int) -> None:
        draft = new_version(f"Draft {t}.")
        result = file_in(ws, draft, "new", None)
        assert isinstance(result, Filed) or result.code is IssueCode.DOCUMENT_EXISTS

    run_threads(THREADS, work)
    [doc] = ws.filing.documents()
    [entry] = ws.filing.history(doc.id)
    # A writer that lost the race may have stored its version before losing (on a Store, a record goes in before
    # the entry that names it): it is unfiled, held by no document, never the head.
    assert all(ws.index.holding(v.id) == [] for v in ws.index.versions() if v.id != entry.version)


def test_a_store_reopens_with_what_it_stored(tmp_path):
    v = new_version("Persist me.")
    with SQLiteStore(tmp_path / "bond.db") as first:
        store(Bond(first, MemoryBlobs()), v, "p")
    again = SQLiteStore(tmp_path / "bond.db")
    doc = Bond(again, MemoryBlobs()).filing.document("p")
    assert doc is not None and doc.head == v.id
    again.close()
    with pytest.raises(BondError, match="closed"):
        again.get(("anything",))


def test_a_database_from_another_bond_is_refused_not_ignored(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "bond.db")
    db.executescript("CREATE TABLE bond_versions (id TEXT); CREATE TABLE bond_history (id TEXT);")
    db.close()
    with pytest.raises(BondError, match="Bond schema unknown.*re-create it"):
        SQLiteStore(tmp_path / "bond.db")


@pytest.mark.skipif(not POSTGRES, reason="BOND_TEST_POSTGRES unset")
def test_a_postgres_store_lives_alongside_other_tables():
    from bond.storage.postgres import PostgresStore

    with postgres_pool() as pool:
        with pool.connection() as conn:
            conn.execute("CREATE TABLE keys (id TEXT PRIMARY KEY)")  # the host's own table, same name unprefixed
        # Several processes starting at once on an empty database create the tables once.
        stores: list[PostgresStore] = []
        run_threads(4, lambda _: stores.append(PostgresStore(pool)))
        store(Bond(stores[0], MemoryBlobs()), new_version("One."), "note")
        assert Bond(stores[1], MemoryBlobs()).filing.document("note") is not None
        with pool.connection() as conn:
            conn.execute("UPDATE bond_schema SET version = 1")
        with pytest.raises(BondError, match="Bond schema 1, but this Bond needs"):
            PostgresStore(pool)


class Recorder:
    """A connection, as Exec takes one, that records the statements it is given and returns no rows."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rowcount = 0

    def execute(self, sql: str, params: Sequence[SqlValue], /) -> Recorder:
        self.statements.append(sql)
        return self

    def cursor(self) -> Recorder:
        return self

    def executemany(self, sql: str, rows: Sequence[Sequence[SqlValue]], /) -> None:
        self.statements.append(sql)

    def fetchone(self) -> Row | None:
        return None

    def fetchall(self) -> list[Row]:
        return []


def test_statements_take_the_drivers_placeholders():
    conn = Recorder()
    Exec(conn, "%s").all("SELECT id FROM bond_versions WHERE id = ? AND format = ?", ("v", "markdown"))
    Exec(conn).all("SELECT id FROM bond_versions WHERE id = ?", ("v",))
    assert conn.statements == ["SELECT id FROM bond_versions WHERE id = %s AND format = %s",
                               "SELECT id FROM bond_versions WHERE id = ?"]


@pytest.mark.parametrize(("placeholder", "sql", "params", "problem"), [
    ("?", "SELECT id FROM bond_versions WHERE id = ?", (), "1 ? for 0 parameters"),
    ("%s", "SELECT id FROM bond_versions WHERE id = 'why?'", (), "1 ? for 0 parameters"),
    ("%s", "SELECT id FROM bond_citations WHERE attrs::jsonb ? 'reason' AND id = ?", ("c",), "2 ? for 1 parameters"),
    ("%s", "SELECT id FROM bond_documents WHERE name LIKE 'case%'", (), "contains %"),
])
def test_a_statement_that_a_rewrite_would_change_is_refused(placeholder, sql, params, problem):
    conn = Recorder()
    with pytest.raises(RuntimeError, match=re.escape(problem)):
        Exec(conn, placeholder).all(sql, params)
    assert conn.statements == []  # nothing reached the driver


# --- Blobs ---------------------------------------------------------------------------


@pytest.fixture
def s3() -> Iterator[S3Client]:
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="evidence")
        yield client


@pytest.fixture(params=["local", "s3"])
def blobs(request: pytest.FixtureRequest, tmp_path: Path) -> Blobs:
    if request.param == "local":
        return LocalBlobs(tmp_path / "blobs")
    return S3Blobs(request.getfixturevalue("s3"), "evidence", prefix="bond/")


def test_blobs_round_trip(blobs):
    data = b"%PDF-1.4 fax bytes"
    blobs.put(hash_bytes(data), data)
    blobs.put(hash_bytes(data), data)  # idempotent
    assert blobs.get(hash_bytes(data)) == data


def test_a_missing_blob(blobs):
    assert blobs.get(hash_bytes(b"never stored")) is None


def test_threads_share_one_blobs(blobs):
    def work(t: int) -> None:
        for i in range(10):
            data = f"blob {i}".encode()  # every thread writes the same ten blobs
            blobs.put(hash_bytes(data), data)
            assert blobs.get(hash_bytes(data)) == data

    run_threads(THREADS, work)


def test_s3_is_asked_to_check_each_upload_against_its_hash(s3):
    # S3 refuses an upload whose sha256 isn't the one sent (moto doesn't check, so this checks what is sent).
    sent: list[dict[str, object]] = []
    s3.meta.events.register("before-parameter-build.s3.PutObject", lambda params, **_: sent.append(dict(params)))
    data = b"fax bytes"
    S3Blobs(s3, "evidence").put(hash_bytes(data), data)
    [params] = sent
    checksum = params["ChecksumSHA256"]
    assert isinstance(checksum, str) and base64.b64decode(checksum).hex() == hash_bytes(data)


def test_s3_writes_a_blob_once(s3):
    # A blob is named by its bytes, so putting it again is done, not an overwrite: on a versioned bucket,
    # one version. (Overwriting also races readers of the old copy in moto, which closes its buffer.)
    s3.put_bucket_versioning(Bucket="evidence", VersioningConfiguration={"Status": "Enabled"})
    blobs = S3Blobs(s3, "evidence")
    data = b"fax bytes"
    for _ in range(3):
        blobs.put(hash_bytes(data), data)
    versions = s3.list_object_versions(Bucket="evidence")["Versions"]
    assert [v["Key"] for v in versions] == [hash_bytes(data)]
    assert blobs.get(hash_bytes(data)) == data


def test_s3_keys_are_under_the_prefix(s3):
    data = b"fax"
    S3Blobs(s3, "evidence", prefix="tenant-a/").put(hash_bytes(data), data)
    [obj] = s3.list_objects_v2(Bucket="evidence")["Contents"]
    assert obj["Key"] == "tenant-a/" + hash_bytes(data)
    assert S3Blobs(s3, "evidence", prefix="tenant-b/").get(hash_bytes(data)) is None


# --- Bond over the backends ----------------------------------------------------------


def test_threads_share_one_bond(bond):
    # The failure this contract was written for: one Bond, many threads, every write reported truly.
    per_thread = 25

    def work(t: int) -> None:
        for i in range(per_thread):
            bond.ingest(f"t{t}/{i}", f"Thread {t}, source {i}.".encode(), format=Format.MARKDOWN)

    run_threads(THREADS, work)
    assert len(bond.documents()) == len(details.versions(bond)) == THREADS * per_thread
    assert all(details.filed_in(bond, v.id) for v in details.versions(bond))


def test_a_case_over_s3_verifies_down_to_the_page(s3, tmp_path):
    blobs = S3Blobs(s3, "evidence")
    with SQLiteStore(tmp_path / "bond.db") as sqlite, Bond(sqlite, blobs) as bond:
        case = case_001.ingest(bond)
        summary = bond.write_document(
            "summary",
            case_001.SUMMARY.encode(),
            case_001.build_citations(bond, case),
            format=Format.MARKDOWN,
        ).version
        assert bond.verify(summary) == []
        s3.delete_object(Bucket="evidence", Key=details.version(bond, case["policy"]).content_hash)
        assert (IssueCode.BLOB_MISSING, case["policy"]) in {(i.code, i.version) for i in bond.verify(summary)}


def test_a_thread_pool_serves_reads_while_writes_land(bond, case):
    # Reads during writes see a consistent store: every head a reader finds resolves.
    def write(i: int) -> None:
        bond.ingest(f"note/{i}", f"Note {i}.".encode(), format=Format.MARKDOWN)

    def read(i: int) -> None:
        for d in bond.documents():
            bond.citations(d.head)

    with ThreadPoolExecutor(THREADS) as pool:
        futures = [pool.submit(write if i % 2 else read, i) for i in range(100)]
        for f in futures:
            f.result()
    assert len(bond.documents()) == len(case) + 50


def test_a_blob_is_written_whole_without_syncing_its_directory_where_the_os_cant(tmp_path):
    # Windows can't open a directory to fsync it, so there the write stops at the atomic rename.
    from bond.storage.local import _write_durably

    for sync_dir in ((True, False) if os.name != "nt" else (False,)):
        path = tmp_path / str(sync_dir) / "blob"
        path.parent.mkdir()
        _write_durably(path, b"bytes", sync_dir=sync_dir)
        assert path.read_bytes() == b"bytes" and [p.name for p in path.parent.iterdir()] == ["blob"]  # no temp left


def test_a_blob_the_os_refuses_for_a_moment_is_read_again(tmp_path, monkeypatch):
    # Windows can refuse to open a blob just renamed into place while another handle on it closes.
    from bond.storage.local import _read

    path = tmp_path / "blob"
    path.write_bytes(b"bytes")
    real, refusals = Path.read_bytes, []

    def refused_twice(self: Path) -> bytes:
        if len(refusals) < 2:
            refusals.append(self)
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refused_twice)
    assert _read(path, waits=(0, 0, 0)) == b"bytes" and len(refusals) == 2

    refusals.clear()
    with pytest.raises(PermissionError):  # refused past the last wait: the error stands
        _read(path, waits=(0,))
