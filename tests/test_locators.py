import pytest

from bond import JsonLocator, PdfLocator, TextLocator, VersionLocator
from bond.locators import LocatorError, contains, format_pointer, locator_from_dict, overlaps, parse_pointer


@pytest.mark.parametrize("d", [
    {"type": "text", "start": 5, "end": 5},
    {"type": "text", "start": -1, "end": 3},
    {"type": "text", "start": 4, "end": 2},
    {"type": "text", "start": 1.0, "end": 2},
    {"type": "text", "start": True, "end": 2},
    {"type": "text", "start": 1},
    {"type": "text", "start": 1, "end": 2, "line": 3},
    {"type": "json", "pointer": "/a"},  # stored json locators are positional; pointers are translated
    {"type": "json", "path": "0/1"},
    {"type": "json", "path": [0, -1]},
    {"type": "json", "path": [0, 1.0]},
    {"type": "json", "path": [True]},
    {"type": "json", "path": [0], "start": 0},
    {"type": "json", "path": [0], "start": 3, "end": 3},
    {"type": "pdf", "page": -1, "bbox": [0, 0, 1, 1]},
    {"type": "pdf", "page": 0, "bbox": [0, 0, 1]},
    {"type": "pdf", "page": 0, "bbox": [0.5, 0, 0.4, 1]},
    {"type": "pdf", "page": 0, "bbox": [0, 0, 1.2, 1]},
    {"type": "pdf", "page": 0, "bbox": [0, 0.3, 1, 0.3]},
    {"type": "pdf", "page": 1.0, "bbox": [0, 0, 1, 1]},
    {"type": "version", "start": 0},
    {"type": "line", "n": 3},
    {"start": 1, "end": 2},
])
def test_invalid_locators_are_rejected(d):
    with pytest.raises(LocatorError):
        locator_from_dict(d)


@pytest.mark.parametrize("d", [
    {"type": "text", "start": 412, "end": 465},
    {"type": "json", "path": [0, 12, 0], "start": 0, "end": 31},
    {"type": "json", "path": []},
    {"type": "pdf", "page": 36, "bbox": [0.12, 0.41, 0.64, 0.43]},
    {"type": "version"},
])
def test_locators_round_trip(d):
    assert locator_from_dict(d).to_dict() == {**d, **({"bbox": [float(v) for v in d["bbox"]]} if "bbox" in d else {})}


def test_pointer_escapes_round_trip():
    tokens = parse_pointer("/a~1b/c~0d/0")
    assert tokens == ("a/b", "c~d", "0")
    assert format_pointer(tokens) == "/a~1b/c~0d/0"


def test_text_containment_and_overlap():
    outer, inner = TextLocator(10, 50), TextLocator(20, 30)
    assert contains(outer, inner) and not contains(inner, outer)
    assert contains(outer, outer)
    assert overlaps(outer, inner) and overlaps(inner, outer)
    # Half-open ranges: touching is not overlapping.
    assert not overlaps(TextLocator(0, 10), TextLocator(10, 20))
    assert overlaps(TextLocator(0, 11), TextLocator(10, 20))


def test_json_containment_by_prefix():
    # In {"blocks": [..., {"text": ...}]}: /blocks/12 is [0, 12], and /blocks/12/text is [0, 12, 0].
    block, text = JsonLocator((0, 12)), JsonLocator((0, 12, 0))
    ranged = JsonLocator((0, 12, 0), (0, 31))
    assert contains(block, text) and contains(block, ranged) and contains(text, ranged)
    assert not contains(text, block) and not contains(ranged, text)
    assert not contains(JsonLocator((0, 1)), JsonLocator((0, 12, 0)))  # steps compare as numbers, not text
    assert contains(JsonLocator(()), block)


def test_json_overlap():
    assert overlaps(JsonLocator((0, 12)), JsonLocator((0, 12, 0), (5, 9)))
    assert overlaps(JsonLocator((0,), (0, 5)), JsonLocator((0,), (4, 8)))
    assert not overlaps(JsonLocator((0,), (0, 4)), JsonLocator((0,), (4, 8)))
    assert not overlaps(JsonLocator((0, 0)), JsonLocator((0, 1)))


def test_pdf_containment_is_geometric():
    page = PdfLocator(3, (0.1, 0.1, 0.9, 0.9))
    box = PdfLocator(3, (0.2, 0.2, 0.3, 0.3))
    assert contains(page, box) and not contains(box, page)
    assert not contains(page, PdfLocator(4, (0.2, 0.2, 0.3, 0.3)))
    assert overlaps(box, PdfLocator(3, (0.25, 0.25, 0.5, 0.5)))
    assert not overlaps(box, PdfLocator(3, (0.3, 0.2, 0.4, 0.3)))  # edges touch only
    assert not overlaps(box, PdfLocator(2, (0.2, 0.2, 0.3, 0.3)))


def test_version_locator_contains_everything():
    v = VersionLocator()
    for loc in [TextLocator(0, 1), JsonLocator((0,)), PdfLocator(0, (0, 0, 1, 1)), v]:
        assert contains(v, loc) and overlaps(v, loc) and overlaps(loc, v)
    assert not contains(TextLocator(0, 1), v)


def test_mixed_types_never_compare():
    assert not contains(TextLocator(0, 10), JsonLocator((0,)))
    assert not overlaps(TextLocator(0, 10), PdfLocator(0, (0, 0, 1, 1)))


def test_there_is_no_citation_locator():
    # Every locator addresses a span of a version; nothing names a citation itself.
    with pytest.raises(LocatorError, match="unknown locator type 'citation'"):
        locator_from_dict({"type": "citation", "id": "a" * 64})
