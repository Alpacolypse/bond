"""Opening a workspace in a store, by name.

A workspace's name is claimed once, under the hash of the name: ("workspaces", sha256(name)) holds its id, name
and creation time. Every other key of the workspace starts with its id, so workspaces sharing a store see nothing
of each other.
"""

from __future__ import annotations

import uuid

from ..models import BondError, Workspace
from .store import Key, Store, now
from .values import decode, encode, name_hash, text

DEFAULT_WORKSPACE = "default"  # what a store with no workspace gets when one is opened without a name


def open_workspace(store: Store, name: str | None, create: bool) -> Workspace:
    """The workspace named name in store, created if create; with no name, the only one there is (or a new
    default)."""
    if name is not None and not name.strip():
        raise ValueError("a workspace's name must be a non-empty string")
    if name is None:
        found = workspaces(store)
        if len(found) > 1:
            names = ", ".join(repr(w.name) for w in found)
            raise BondError(f"this store holds {len(found)} workspaces ({names}); open one by name")
        if found:
            return found[0]
        name = DEFAULT_WORKSPACE
    key: Key = ("workspaces", name_hash(name))
    if store.get(key) is None:
        if not create:
            raise BondError(f"this store holds no workspace named {name!r}")
        # Another writer creating the same workspace at once creates nothing here; both then read its key.
        store.create(key, encode({"id": "ws_" + uuid.uuid4().hex, "name": name, "created_at": now()}))
    return _workspace(store, key)


def workspaces(store: Store) -> list[Workspace]:
    """Every workspace in store, oldest first."""
    return sorted((_workspace(store, key) for key in store.list(("workspaces",))), key=lambda w: (w.created_at, w.id))


def find_workspace(store: Store, name: str) -> Workspace | None:
    """The workspace named name in store; None if there is none."""
    key: Key = ("workspaces", name_hash(name))
    return None if store.get(key) is None else _workspace(store, key)


def _workspace(store: Store, key: Key) -> Workspace:
    data = store.get(key)
    if data is None:
        raise BondError(f"the workspace at {key!r} can't be read back")
    obj = decode(data, "a workspace")
    return Workspace(text(obj, "id"), text(obj, "name"), text(obj, "created_at"))
