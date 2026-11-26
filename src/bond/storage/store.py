"""Store: write-once keys, the one thing Bond needs a host's storage to do.

Everything Bond keeps besides bytes is a key written once: a workspace's name, a version's record, a document's
name, each entry of its history, each of its titles. A Store holds them. It never updates or deletes a key, so
two writers can only ever disagree about whether a key exists; create says which of them made it. That is the
only coordination Bond needs: a document advances by creating its next history entry, and of two writers who
read the same head, the second finds that entry there already.

A store doesn't interpret keys or values. Bond decides what each key means and encodes each value (see
bond.storage.records, .filing and .index), so a backend can't weaken a rule by getting it wrong: it stores, reads and lists.

A Key is a tuple of strings and non-negative integers. Keys under one prefix list in key order, element by
element: strings by code point, integers numerically. key_path encodes a key as one string whose order keeps
integers' order, for stores that sort strings (an object store, a string column).
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from urllib.parse import quote, unquote

Key = tuple[str | int, ...]

_INT_DIGITS = 20  # enough for any non-negative 64-bit integer


class Store(Protocol):
    """Write-once keys. Safe to use from any thread, process or machine."""

    def create(self, key: Key, value: bytes) -> bool:
        """Store value under key if nothing is stored there, durably before returning. True if this call created
        it; False if the key already existed, in which case nothing is changed. Never overwrites."""
        ...

    def get(self, key: Key) -> bytes | None:
        """The value under key, or None. Strongly consistent: sees every create that returned before it began."""
        ...

    def list(self, prefix: Key, after: Key | None = None) -> Iterator[Key]:
        """The keys under prefix (longer than it, starting with it), in key order; only those after `after`, if
        given."""
        ...


@runtime_checkable
class LastKey(Protocol):
    """A store that can find the greatest key under a prefix without listing them all."""

    def last(self, prefix: Key) -> Key | None:
        """The greatest key under prefix, or None if there is none."""
        ...


def key_path(key: Key) -> str:
    """A key as one string, "/" between elements, whose string order is the key's order wherever order matters.

    Strings are percent-encoded (with "~" too), so no element contains "/" or starts with "~"; integers are written
    as "~" and twenty digits, so they sort numerically. key_from_path reads one back. A string element and an integer element never share a position under one prefix in Bond's
    keys, so their relative order is never relied on.
    """
    parts = []
    for element in key:
        if isinstance(element, bool) or not isinstance(element, str | int):
            raise TypeError(f"a key element is a string or an integer, not {element!r}")
        if isinstance(element, int):
            if element < 0:
                raise ValueError(f"a key's integers are non-negative, not {element}")
            parts.append("~" + str(element).zfill(_INT_DIGITS))
        else:
            parts.append(quote(element, safe="").replace("~", "%7E"))
    return "/".join(parts)


def key_from_path(path: str) -> Key:
    """The key key_path wrote as path."""
    return tuple(int(part[1:]) if part.startswith("~") else unquote(part) for part in path.split("/"))


class MemoryStore:
    """A Store in this process's memory: for tests, and for a workspace that lives only as long as its process."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[Key, bytes]] = {}
        self._paths: list[str] = []  # sorted
        self._lock = threading.Lock()

    def create(self, key: Key, value: bytes) -> bool:
        path = key_path(key)
        with self._lock:
            if path in self._values:
                return False
            self._values[path] = (key, bytes(value))
            bisect.insort(self._paths, path)
            return True

    def get(self, key: Key) -> bytes | None:
        with self._lock:
            found = self._values.get(key_path(key))
        return None if found is None else found[1]

    def list(self, prefix: Key, after: Key | None = None) -> Iterator[Key]:
        start = _start(prefix)
        with self._lock:
            i = bisect.bisect_right(self._paths, key_path(after)) if after is not None else 0
            i = max(i, bisect.bisect_left(self._paths, start))
            keys = []
            for path in self._paths[i:]:
                if not path.startswith(start):
                    break
                keys.append(self._values[path][0])
        return iter(keys)

    def last(self, prefix: Key) -> Key | None:
        start = _start(prefix)
        with self._lock:
            # Every path under prefix starts with start, which ends in "/"; "0" is the character after "/", so the
            # paths under prefix all sort before start[:-1] + "0", and every path between start and it is under prefix.
            i = bisect.bisect_left(self._paths, start[:-1] + "0") if start else len(self._paths)
            if i == 0 or not self._paths[i - 1].startswith(start):
                return None
            return self._values[self._paths[i - 1]][0]


def _start(prefix: Key) -> str:
    """What every key_path under prefix starts with."""
    return key_path(prefix) + "/" if prefix else ""


_last_time = datetime.min.replace(tzinfo=UTC)
_clock_lock = threading.Lock()


def now() -> str:
    """The time, as Bond records it (ISO 8601, UTC, microseconds), strictly later than the last call in this
    process, so things made in order by one process sort in that order."""
    global _last_time
    with _clock_lock:
        t = max(datetime.now(UTC), _last_time + timedelta(microseconds=1))
        _last_time = t
    return t.isoformat(timespec="microseconds")
