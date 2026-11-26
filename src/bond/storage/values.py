"""Stored values: JSON, written canonically and checked as it is read back.

Every value Bond keeps in a store (bond.storage.store) is a JSON object. A value that isn't what Bond wrote
(a store corrupted, or changed behind Bond's back) is raised as a BondError saying so, never read as something else.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ..ids import dumps
from ..jsonvalue import JsonValue
from ..models import BondError
from ..parsers import load_json


def encode(value: JsonValue) -> bytes:
    """A value as stored: canonical JSON, UTF-8."""
    return dumps(value).encode()


def decode(data: bytes, what: str) -> Mapping[str, JsonValue]:
    """A stored object; what names it for the error if it isn't one."""
    loaded = load_json(data.decode("utf-8", errors="replace"))
    if not isinstance(loaded, Mapping):
        raise BondError(f"the store holds {data[:60]!r} where {what} belongs; it is corrupt")
    return loaded


def text(obj: Mapping[str, JsonValue], field: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str):
        raise BondError(f"the store holds {value!r} where text ({field}) belongs; it is corrupt")
    return value


def optional_text(obj: Mapping[str, JsonValue], field: str) -> str | None:
    return None if obj.get(field) is None else text(obj, field)


def mapping(value: JsonValue, what: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise BondError(f"the store holds {value!r} where {what} belongs; it is corrupt")
    return value


def items(value: JsonValue, what: str) -> Sequence[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise BondError(f"the store holds {value!r} where a list of {what} belongs; it is corrupt")
    return value


def name_hash(name: str) -> str:
    """A name as it appears in a key: its sha256, so no free text reaches a key (keys reach logs values don't)."""
    return hashlib.sha256(name.encode()).hexdigest()
