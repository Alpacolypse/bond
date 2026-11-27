"""The Store contract, and Bond's layers on it.

A Store only creates, reads and lists write-once keys (bond.storage.store); every store a host brings must pass the
first half of this file. The second half checks what Bond builds on a store (bond.storage.layers) that the shared
storage contract in test_storage.py can't see: what is in the keys, and how a crash between writes leaves things.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from test_storage import THREADS, file_in, new_version, run_threads

from bond import Bond, IssueCode, VersionId
from bond.models import HistoryEntry
from bond.storage import Conflict, Filed
from bond.storage.local import MemoryBlobs
from bond.storage.store import Key, LastKey, MemoryStore, Store, key_from_path, key_path, now
from conftest import STORES, store_of


@pytest.fixture(params=STORES)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    with store_of(request.param, tmp_path) as s:
        yield s


# --- the Store contract ----------------------------------------------------------------------------


def test_a_key_is_created_once_and_never_overwritten(store):
    assert store.create(("a", "b"), b"one")
    assert not store.create(("a", "b"), b"one")
    assert not store.create(("a", "b"), b"two")
    assert store.get(("a", "b")) == b"one" and store.get(("a", "c")) is None


def test_racing_creates_of_one_key_have_one_winner(store):
    wins: list[int] = []

    def work(t: int) -> None:
        if store.create(("race",), str(t).encode()):
            wins.append(t)

    run_threads(THREADS, work)
    [winner] = wins
    assert store.get(("race",)) == str(winner).encode()


def test_listing_is_in_key_order_with_integers_in_numeric_order(store):
    for seq in (10, 2, 1, 33):
        store.create(("ws", "entries", "doc", seq), b"")
    store.create(("ws", "entries", "doc2", 1), b"")  # a sibling whose name extends doc's
    store.create(("ws", "entriesX", "doc", 1), b"")
    assert list(store.list(("ws", "entries", "doc"))) == [("ws", "entries", "doc", n) for n in (1, 2, 10, 33)]
    assert list(store.list(("ws", "entries", "doc"), after=("ws", "entries", "doc", 2))) == [
        ("ws", "entries", "doc", 10), ("ws", "entries", "doc", 33)]
    assert list(store.list(("ws", "nothing"))) == []


def test_strings_with_slashes_and_percent_signs_stay_one_element(store):
    store.create(("a/b", "c%2Fd"), b"x")
    store.create(("a", "b"), b"y")
    assert list(store.list(("a/b",))) == [("a/b", "c%2Fd")]
    assert list(store.list(("a",))) == [("a", "b")]


def test_the_last_key_under_a_prefix(store):
    for seq in (3, 12, 7):
        store.create(("ws", "entries", "doc", seq), b"")
    store.create(("ws", "entries", "doc2", 99), b"")
    if not isinstance(store, LastKey):
        pytest.skip("this store has no last()")
    assert store.last(("ws", "entries", "doc")) == ("ws", "entries", "doc", 12)
    assert store.last(("ws", "entries", "none")) is None


def test_key_paths_order_integers_numerically_and_refuse_other_elements():
    assert key_path(("a", 2)) < key_path(("a", 10))
    for key in [("a", 2), ("~7", "a/b", "%7E", 0), ("ws_1", "entries", "doc_2", 10**19)]:
        assert key_from_path(key_path(key)) == key
    with pytest.raises(ValueError):
        key_path(("a", -1))
    with pytest.raises(TypeError):
        key_path(("a", True))


def test_the_clock_never_repeats():
    times = [now() for _ in range(1000)]
    assert times == sorted(times) and len(set(times)) == len(times)


# --- Bond's layers -----------------------------------------------------------------------------------


def test_a_claimed_name_with_no_entries_is_not_a_document_and_is_reused():
    # A writer that crashed after claiming a name, before writing the document's first entry.
    store = MemoryStore()
    ws = Bond(store, MemoryBlobs())
    claim = ws.filing.claim("note")
    assert ws.filing.document("note") is None and ws.filing.documents() == []
    v = new_version("One.")
    result = file_in(ws, v, "note", None)
    assert isinstance(result, Filed) and result.document.id == claim.id


def test_a_crash_before_the_entry_leaves_an_unfiled_version_that_a_retry_files():
    store = MemoryStore()
    ws = Bond(store, MemoryBlobs())
    v = new_version("One.")
    ws.records.put(v)  # the record went in, then the writer stopped
    assert ws.filing.document("note") is None and ws.index.holding(v.id) == []
    assert isinstance(file_in(ws, v, "note", None), Filed)  # the retry
    assert [d.name for d in ws.index.holding(v.id)] == ["note"]


def test_an_entry_taken_by_the_same_version_is_a_write_that_already_happened():
    store = MemoryStore()
    ws = Bond(store, MemoryBlobs())
    v1, v2 = new_version("One."), new_version("Two.")
    doc = file_in(ws, v1, "note", None)
    assert isinstance(doc, Filed)
    # Another writer filed v2 from v1 between this writer's read and its append.
    assert ws.filing.append(doc.document.id, HistoryEntry(2, v2.id, v1.id, now()))
    ws.records.put(v2)
    again = file_in(ws, v2, "note", v1.id)
    assert isinstance(again, Filed) and not again.advanced
    late = new_version("Late.")
    stale = file_in(ws, late, "note", v1.id)
    assert isinstance(stale, Conflict) and stale.code is IssueCode.STALE_BASE


def test_titles_are_a_log_and_the_latest_wins():
    store = MemoryStore()
    ws = Bond(store, MemoryBlobs())
    v = new_version("One.")
    doc = file_in(ws, v, "note", None)
    assert isinstance(doc, Filed)
    for title in ("First", None, "Third"):
        ws.filing.retitle("note", title)
    found = ws.filing.document("note")
    assert found is not None and found.title == "Third"
    titles: list[Key] = list(store.list((ws.workspace.id, "titles", doc.document.id)))
    assert [k[-1] for k in titles] == [1, 2, 3]  # every title is kept


def test_racing_retitles_all_land():
    store = MemoryStore()
    ws = Bond(store, MemoryBlobs())
    v = new_version("One.")
    doc = file_in(ws, v, "note", None)
    assert isinstance(doc, Filed)
    def work(t: int) -> None:
        ws.filing.retitle("note", f"Title {t}")

    run_threads(THREADS, work)
    assert len(list(store.list((ws.workspace.id, "titles", doc.document.id)))) == THREADS


def test_another_bond_on_the_same_store_sees_writes_at_its_next_query():
    store = MemoryStore()
    a, b = Bond(store, MemoryBlobs(), "case"), Bond(store, MemoryBlobs(), "case")
    src = new_version("Evidence.")
    assert isinstance(file_in(a, src, "src", None), Filed)
    assert [v.id for v in b.index.versions()] == [src.id]  # b's index catches up
    v = new_version("More.")
    assert isinstance(file_in(a, v, "src", src.id), Filed)
    assert [d.head for d in b.index.holding(src.id)] == [v.id]
    assert b.workspace == a.workspace


def test_documents_and_versions_list_oldest_first():
    ws = Bond(MemoryStore(), MemoryBlobs())
    names = [f"doc-{i:02}" for i in range(20)][::-1]  # created in an order their hashes don't share
    versions: list[VersionId] = []
    for i, name in enumerate(names):
        v = new_version(f"Version {i}.")
        versions.append(v.id)
        assert isinstance(file_in(ws, v, name, None), Filed)
    assert [d.name for d in ws.filing.documents()] == names
    assert [v.id for v in ws.index.versions()] == versions


class CountingStore(MemoryStore):
    """A MemoryStore that counts the calls made of it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get(self, key):
        self.calls += 1
        return super().get(key)

    def list(self, prefix, after=None):
        self.calls += 1
        return super().list(prefix, after)

    def last(self, prefix):
        self.calls += 1
        return super().last(prefix)


@pytest.mark.parametrize("n", [10, 100])
def test_a_search_reads_the_store_a_number_of_times_linear_in_the_documents(n):
    from bond import Format

    store = CountingStore()
    bond = Bond(store, MemoryBlobs(), "ws")
    for i in range(n):
        bond.ingest(f"doc{i}", f"Note {i}.".encode(), format=Format.MARKDOWN)
    bond.search("absent")  # warm the caches
    store.calls = 0
    assert bond.search("absent") == []
    assert store.calls <= 6 * n + 10, store.calls  # one catch-up, and each head once: linear, not quadratic
    store.calls = 0
    assert len(bond.search("Note 7.")) == 1
    assert store.calls <= 6 * n + 20, store.calls
