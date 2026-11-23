import pytest

from bond import Format, JsonLocator, Locator, PdfLocator, TextLocator, VersionLocator
from bond.formats import FORMATS, from_suffix, parse, sniff, spec
from bond.locators import LocatorError
from bond.parsers import MAX_JSON_DEPTH, JsonDoc, LineContext, Malformed, MarkdownDoc, ParsedDoc, load_json
from conftest import FIXTURES, json_doc


def parsed(fmt: Format, data: bytes) -> ParsedDoc:
    """Bytes the test knows are well-formed in their format, parsed."""
    doc = parse(fmt, data)
    assert not isinstance(doc, Malformed), doc
    return doc


def malformed(fmt: Format, data: bytes) -> str:
    """Why bytes the test knows are malformed in their format aren't well-formed."""
    doc = parse(fmt, data)
    assert isinstance(doc, Malformed), doc
    return doc.reason

MD = """\
---
title: Front matter is a claim
---
# Heading is a claim

First paragraph
spans two lines.

- item one
- [x] item two
  - nested item
1. numbered

> quoted claim

```text
code is a claim
```

| Col | Val |
|-----|-----|
| a   | 1   |
| b   | 2   |
"""


def unit_texts(doc):
    return [doc.text(u) for u in doc.claims()]


def test_markdown_claim_units():
    # All text is claim text; only syntax (markers, fences, delimiter rows) is left out.
    assert unit_texts(MarkdownDoc(MD)) == [
        "title: Front matter is a claim",
        "Heading is a claim",
        "First paragraph\nspans two lines.",
        "item one",
        "item two",
        "nested item",
        "numbered",
        "quoted claim",
        "code is a claim",
        "| Col | Val |",
        "| a   | 1   |",
        "| b   | 2   |",
    ]


def test_markdown_crlf_offsets_stay_raw():
    doc = MarkdownDoc(MD.replace("\n", "\r\n"))
    assert unit_texts(doc)[2] == "First paragraph\r\nspans two lines."
    assert unit_texts(doc)[-1] == "| b   | 2   |"


def _problem(doc: ParsedDoc, locator: Locator) -> str:
    problem = doc.check(locator)
    assert problem is not None
    return problem


def test_markdown_locators():
    doc = MarkdownDoc("hello")
    assert doc.check(TextLocator(0, 5)) is None
    assert doc.check(VersionLocator()) is None
    assert "past the document" in _problem(doc, TextLocator(0, 6))
    assert "not valid in a markdown" in _problem(doc, JsonLocator((0,)))


def test_markdown_search_context():
    [hit] = MarkdownDoc("# A\n\n## B\n\nfind me\n\n# C\n").search("FIND ME")
    assert hit.locator == TextLocator(11, 18)
    assert hit.context == LineContext(5, ("A", "B"))
    assert hit.context.to_dict() == {"line": 5, "headings": ["A", "B"]}  # as the viewer's API shows it


def test_json_rejects_duplicate_keys_and_nan():
    assert JsonDoc.parse('{"a": 1, "a": 2}') == Malformed(
        "invalid JSON: it has the key 'a' twice in one object, so JSON pointers into it would be ambiguous")
    assert JsonDoc.parse('{"a": NaN}') == Malformed("invalid JSON: NaN is not a JSON value")
    assert JsonDoc.parse('{"a": ') == Malformed("invalid JSON: Expecting value at line 1, column 7")


def test_json_nested_too_deeply_is_malformed_however_deep():
    # Deeper than any document needs: refused as content, whether or not Python's own stack would have held it.
    for depth in (MAX_JSON_DEPTH + 1, 100_000):
        text = "[" * depth + "]" * depth
        assert load_json(text) == Malformed(f"it nests more than {MAX_JSON_DEPTH} deep")
        assert sniff(text.encode()) is Format.MARKDOWN  # unlabelled, it is text: not JSON this deep
    assert not isinstance(load_json("[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH), Malformed)  # as deep as allowed


def test_json_claims_default_to_leaves():
    doc = json_doc('{"decision": "approved", "score": 3, "flag": false, "note": null, "empty": "", "list": ["x", {"y": "z"}]}')
    assert doc.claims() == [doc.locate(p) for p in ["/decision", "/score", "/flag", "/list/0", "/list/1/y"]]
    assert doc.claims() == [JsonLocator(p) for p in [(0,), (1,), (2,), (5, 0), (5, 1, 0)]]


def test_json_locators():
    doc = json_doc('{"a": "hello", "b": 2}')
    assert doc.check(JsonLocator((0,), (1, 3))) is None
    assert doc.text(JsonLocator((0,), (1, 3))) == "el"
    assert doc.text(JsonLocator((1,))) == "2"
    assert "does not exist" in _problem(doc, JsonLocator((2,)))
    assert "needs a string" in _problem(doc, JsonLocator((1,), (0, 1)))
    assert "past the string at '/a'" in _problem(doc, JsonLocator((0,), (0, 9)))


def test_json_locators_are_positions_and_translate_to_and_from_pointers():
    # Positions are the order keys are written in, whatever the keys look like; they hold none of them.
    doc = json_doc('{"10": "ten", "2": {"Jordan Avery": ["x", "y"]}, "": 1, "a/b~c": true}')
    assert doc.locate("/10") == JsonLocator((0,))
    assert doc.locate("/2/Jordan Avery/1") == JsonLocator((1, 0, 1))
    assert doc.locate("/") == JsonLocator((2,)) and doc.locate("/a~1b~0c") == JsonLocator((3,))
    assert doc.locate("") == JsonLocator(()) and doc.locate("/10", (0, 2)) == JsonLocator((0,), (0, 2))
    for path, pointer in [((), ""), ((1, 0, 1), "/2/Jordan Avery/1"), ((2,), "/"), ((3,), "/a~1b~0c")]:
        assert doc.pointer(JsonLocator(path)) == pointer and doc.locate(pointer) == JsonLocator(path)
    assert doc.locate("/missing") is None
    assert doc.locate("/2/Jordan Avery/01") is None  # an array index has one spelling
    with pytest.raises(LocatorError, match="does not exist"):
        doc.pointer(JsonLocator((1, 0, 2)))
    with pytest.raises(LocatorError, match="escape"):  # a malformed pointer isn't a lookup that misses
        doc.locate("/a~2")


def test_json_search_ignores_escapes():
    [m] = json_doc('{"q": "say \\"hi\\" now"}').search('"hi"')
    assert m.locator == JsonLocator((0,), (4, 8))


def test_pdf_page_validation():
    doc = parsed(Format.PDF, (FIXTURES / "fax_case_001.pdf").read_bytes())
    assert doc.check(PdfLocator(1, (0, 0, 1, 1))) is None
    assert "does not exist" in _problem(doc, PdfLocator(2, (0, 0, 1, 1)))
    assert "not valid in a pdf" in _problem(doc, TextLocator(0, 1))
    assert doc.claims() == [] and doc.search("fax") == []


def test_invalid_content():
    assert malformed(Format.MARKDOWN, b"\xff\xfe bad").startswith("content is not valid UTF-8")
    assert malformed(Format.PDF, b"not a pdf") == "not a PDF: missing %PDF- header"
    assert malformed(Format.PDF, b"%PDF-1.4 truncated").startswith("unreadable PDF")
    assert malformed(Format.CSV, b'a,"b\n').startswith("invalid CSV")
    assert malformed(Format.TSV, b'a\t"b\n').startswith("invalid TSV")


def test_sniff_format():
    assert sniff(b"%PDF-1.4 ...") is Format.PDF
    assert sniff(b'{"a": 1}') is Format.JSON and sniff(b"[1, 2]") is Format.JSON
    assert sniff(b"# hi") is Format.MARKDOWN
    # A bare JSON scalar is more likely a note than a JSON document.
    assert sniff(b"42") is Format.MARKDOWN and sniff(b'"ok"') is Format.MARKDOWN


def test_every_format_has_one_registry_entry():
    assert set(FORMATS) == set(Format)
    assert from_suffix(".MD") is Format.MARKDOWN and from_suffix(".txt") is Format.MARKDOWN
    assert from_suffix(".json") is Format.JSON and from_suffix(".pdf") is Format.PDF and from_suffix(".xyz") is None
    assert [f for f in Format if not spec(f).has_text] == [Format.PDF]
    assert spec(Format.JSON).media_type == "application/json"


def test_a_document_says_where_a_locator_is_and_what_is_around_it():
    md = MarkdownDoc("# Policy\n\n## Criteria\n\nAt least 6 weeks of notice.\n")
    start = md.content.index("6 weeks")
    loc = TextLocator(start, start + 7)
    assert md.where(loc) == "Policy › Criteria · line 5"
    assert md.excerpt(loc, pad=9) == ("At least ", "6 weeks", " of notic")
    js = json_doc('{"blocks": [{"text": "No significant damage to other rooms."}]}')
    assert js.where(JsonLocator((0, 0, 0), (3, 14))) == "/blocks/0/text"
    assert js.excerpt(JsonLocator((0, 0, 0), (3, 14)), pad=3) == ("No ", "significant", " da")
    pdf = parsed(Format.PDF, (FIXTURES / "fax_case_001.pdf").read_bytes())
    assert pdf.where(PdfLocator(1, (0.1, 0.1, 0.9, 0.2))) == "page 2" and pdf.excerpt(VersionLocator()) == ("", "", "")
