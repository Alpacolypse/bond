"""JSON locators are positions, and translate to and from JSON Pointers losslessly within their version."""

from __future__ import annotations

import json
import random
import unicodedata
from collections.abc import Iterator

import pytest

from bond import JsonLocator
from bond.jsonvalue import JsonValue
from bond.locators import LocatorError, format_pointer, parse_pointer
from conftest import json_doc

# Keys a naive translation gets wrong: escapes, the empty key, keys that look like indexes, the same letter
# composed and decomposed (two keys, never normalized), and keys that are data.
KEYS = ["", "/", "~", "~0", "~1", "a/b~c", "0", "01", "2", "10", "-", " ", "id",
        unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é"), "😀", "MRN-48213", "Jordan Avery"]


def value(rng: random.Random, depth: int = 0) -> JsonValue:
    r = rng.random()
    if depth > 4 or r < 0.3:
        return rng.choice([1, 2.5, True, False, None, "x", "Jordan", ""])
    if r < 0.65:
        return {k: value(rng, depth + 1) for k in rng.sample(KEYS, rng.randint(0, 6))}
    return [value(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def nodes(
    v: JsonValue, tokens: tuple[str, ...] = (), path: tuple[int, ...] = (),
) -> Iterator[tuple[tuple[str, ...], tuple[int, ...], JsonValue]]:
    """Every value with its pointer tokens and its position: the spec, written independently of JsonDoc."""
    yield tokens, path, v
    if isinstance(v, dict):
        for i, (k, c) in enumerate(v.items()):
            yield from nodes(c, tokens + (k,), path + (i,))
    elif isinstance(v, list):
        for i, c in enumerate(v):
            yield from nodes(c, tokens + (str(i),), path + (i,))


@pytest.mark.parametrize("seed", range(4))
def test_every_value_translates_both_ways_to_itself(seed):
    rng = random.Random(seed)
    for _ in range(150):
        doc = value(rng)
        if not isinstance(doc, (dict, list)):
            continue
        parsed = json_doc(json.dumps(doc, ensure_ascii=rng.random() < 0.5))  # \u escapes or raw UTF-8
        for tokens, path, v in nodes(parsed.value):
            pointer = format_pointer(tokens)
            assert parse_pointer(pointer) == tokens  # one spelling per value
            assert parsed.locate(pointer) == JsonLocator(path)
            assert parsed.pointer(JsonLocator(path)) == pointer
            assert parsed.check(JsonLocator(path)) is None
            if isinstance(v, str):
                assert parsed.text(JsonLocator(path)) == v
            # A step past the end names nothing, by position or by pointer.
            beyond = path + (len(v) if isinstance(v, (dict, list)) else 0,)
            assert parsed.check(JsonLocator(beyond)) is not None
            with pytest.raises(LocatorError):
                parsed.pointer(JsonLocator(beyond))
            assert parsed.locate(format_pointer(tokens + ("no such key",))) is None


def test_positions_follow_the_bytes_not_the_keys():
    # The same keys in another order are another version, where the same position names another value.
    a, b = json_doc('{"x": "first", "y": "second"}'), json_doc('{"y": "second", "x": "first"}')
    assert a.text(JsonLocator((0,))) == "first" and b.text(JsonLocator((0,))) == "second"
    assert a.locate("/x") == b.locate("/y") == JsonLocator((0,))
    # Keys that look like indexes keep the order they were written in (a JavaScript object would reorder them).
    doc = json_doc('{"10": "ten", "2": "two"}')
    assert doc.pointer(JsonLocator((0,))) == "/10" and doc.pointer(JsonLocator((1,))) == "/2"
