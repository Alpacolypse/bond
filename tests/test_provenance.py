"""The public guarantee: direct declarations, structural checks, and application trust are distinct."""

import json

import pytest

from bond import (
    ALLOW_GAPS,
    Bond,
    Citation,
    Format,
    JsonLocator,
    Ref,
    SelfRef,
    SupportStatus,
    TextLocator,
    VersionLocator,
    details,
)
from bond import (
    CitationKind as K,
)
from bond.authoring import Shortfall, Tools
from bond.locators import CsvLocator, Locator
from bond.models import SupportReason
from bond.parsers import CsvDoc, Malformed, MarkdownDoc, ParsedDoc
from conftest import json_doc, refused, written


def head(bond, document):
    return bond.document(document).head


def table(text: str) -> CsvDoc:
    doc = CsvDoc.parse(text, ",")
    assert not isinstance(doc, Malformed), doc
    return doc


def test_search_reports_support_for_the_entire_match_and_the_passage(bond):
    bond.ingest("source", b"Known fact.")
    tools = Tools(bond)
    [source] = tools.search("Known fact.")
    written(tools.write("mixed", f'<bond:cite kind="quote" from="{source.handle}">Known fact.</bond:cite> '
                        '<bond:cite kind="unsourced">Unsubstantiated assertion.</bond:cite>'))
    text = "Known fact. Unsubstantiated assertion."
    [hit] = tools.search(text, document="mixed")
    assert hit.support is SupportStatus.PARTIALLY_SUPPORTED
    assert hit.kinds == [K.QUOTE, K.UNSOURCED]
    assert bond.declarations(Ref(head(bond, "mixed"), TextLocator(0, len(text)))).undeclared == ()
    assert tools.trace(hit.handle).status is SupportStatus.PARTIALLY_SUPPORTED
    [safe] = tools.search("Known fact.", document="mixed")
    assert safe.support is SupportStatus.SUPPORTED
    assert tools.trace(safe.handle).status is SupportStatus.SUPPORTED
    assert safe.passage == hit.handle
    assert tools.trace(safe.passage).status is SupportStatus.PARTIALLY_SUPPORTED

    # Quoting that whole span is structurally valid; ancestry still exposes the unsourced assertion.
    written(tools.write("copy", f'<bond:cite kind="quote" from="{hit.handle}">{text}</bond:cite>'))
    assert bond.verify(head(bond, "copy")) == []
    [copy] = tools.search(text, document="copy")
    assert copy.support is SupportStatus.UNSUPPORTED
    why = tools.trace(copy.handle)
    # The one shortfall is the assertion, in the document the quote copied it from.
    unsourced = hit.handle.split("@")[0] + f"@{text.index('Unsubstantiated')}-{len(text)}"
    assert why.status is SupportStatus.UNSUPPORTED and why.issues == [Shortfall(SupportReason.UNSOURCED, unsourced)]
    assert not why.truncated and why.unreachable == []
    assert any(link.kind is K.UNSOURCED for s in why.spans for link in s.rests_on)


def test_several_citations_jointly_cover_a_match(bond):
    source = bond.ingest("source", b"alpha beta").version
    result = bond.write_document(
        "joined",
        b"alpha beta",
        [
        Citation(K.QUOTE, TextLocator(0, 5), [Ref(source, TextLocator(0, 5))]),
        Citation(K.QUOTE, TextLocator(6, 10), [Ref(source, TextLocator(6, 10))]),
    ],
        format=Format.MARKDOWN,
    )
    [hit] = bond.search("alpha beta", documents=["joined"])
    assert bond.check_support(hit.ref).status is SupportStatus.SUPPORTED
    assert hit.declarations == bond.declarations(Ref(result.version, TextLocator(0, 10)))
    assert hit.declarations.undeclared == ()


def test_partial_words_and_overlapping_declarations_remain_visible(bond):
    result = bond.write_document(
        "mixed",
        b"alpha",
        [
        Citation(K.SOURCE, TextLocator(0, 2), []),
        Citation(K.UNSOURCED, TextLocator(0, 5), []),
    ],
        format=Format.MARKDOWN,
    )
    p = bond.declarations(Ref(result.version, TextLocator(0, 5)))
    assert p.undeclared == ()
    assert [bond.text(Ref(result.version, c.claim)) for c in p.citations if c.kind is K.UNSOURCED] == ["alpha"]
    [whole] = bond.search("alpha")
    assert bond.check_support(whole.ref).status is SupportStatus.UNSUPPORTED
    # The overlapping unsourced declaration is retained even where source also covers the span.
    [covered] = bond.search("al")
    assert bond.check_support(covered.ref).status is SupportStatus.UNSUPPORTED
    assert covered.kinds == {K.SOURCE, K.UNSOURCED}


@pytest.mark.parametrize("fmt, data, full, left, right", [
    (Format.MARKDOWN, b"alpha beta", TextLocator(0, 10), TextLocator(0, 5), TextLocator(6, 10)),
    (Format.JSON, b'{"x":"alpha beta"}', JsonLocator((0,)), JsonLocator((0,), (0, 5)), JsonLocator((0,), (6, 10))),
    (Format.CSV, b"alpha beta", CsvLocator(0, 0), CsvLocator(0, 0, (0, 5)), CsvLocator(0, 0, (6, 10))),
    (Format.TSV, b"alpha beta", CsvLocator(0, 0), CsvLocator(0, 0, (0, 5)), CsvLocator(0, 0, (6, 10))),
])
def test_gap_scopes_are_exact_across_text_formats(bond, fmt, data, full, left, right):
    result = bond.write_document("partial", data, [Citation(K.SOURCE, left, [])], format=fmt, policy=ALLOW_GAPS)
    p = bond.declarations(Ref(result.version, full))
    assert [bond.text(r) for r in p.undeclared] == ["beta"]
    assert bond.declarations(Ref(result.version, left)).undeclared == ()
    assert bond.declarations(Ref(result.version, right)).undeclared == (Ref(result.version, right),)


@pytest.mark.parametrize("doc, whole, scope", [
    (MarkdownDoc("! unknown"), TextLocator(0, 9), TextLocator(0, 1)),
    (json_doc('{"x":"! unknown"}'), JsonLocator((0,)), JsonLocator((0,), (0, 1))),
    (table("! unknown"), CsvLocator(0, 0), CsvLocator(0, 0, (0, 1))),
])
def test_scoping_a_gap_to_punctuation_does_not_report_words_outside_it(doc: ParsedDoc, whole: Locator, scope: Locator):
    assert doc.gaps([]) == [whole]  # the enclosing gap contains words
    assert doc.gaps([], within=scope) == []


def test_json_ancestor_coverage_and_scoped_scalar_gaps(bond):
    data = json.dumps({"covered": {"text": "alpha", "number": 42}, "other": ["beta", False, None]}).encode()
    version = bond.write_document(
        "json",
        data,
        [Citation(K.SOURCE, JsonLocator((0,)), [])],
        format=Format.JSON,
        policy=ALLOW_GAPS,
    ).version
    assert bond.declarations(Ref(version, JsonLocator((0,)))).undeclared == ()
    p = bond.declarations(Ref(version, JsonLocator((1,))))
    assert [bond.text(r) for r in p.undeclared] == ["beta", "false"]
    assert bond.declarations(Ref(version, JsonLocator((1, 0), (1, 3)))).undeclared == (
        Ref(version, JsonLocator((1, 0), (1, 3))),)


def test_table_row_column_and_partial_cell_coverage_combine(bond):
    data = b"a,b\nc,delta\n"
    version = bond.write_document(
        "table",
        data,
        [
        Citation(K.SOURCE, CsvLocator(row=0), []),
        Citation(K.SOURCE, CsvLocator(column=0), []),
        Citation(K.SOURCE, CsvLocator(1, 1, (0, 2)), []),
    ],
        format=Format.CSV,
        policy=ALLOW_GAPS,
    ).version
    assert bond.declarations(Ref(version, CsvLocator(row=0))).undeclared == ()
    assert bond.declarations(Ref(version, CsvLocator(column=0))).undeclared == ()
    p = bond.declarations(Ref(version, CsvLocator(column=1)))
    assert p.undeclared == (Ref(version, CsvLocator(1, 1, (2, 5))),)


def test_structural_validation_does_not_establish_semantic_support(bond):
    bond.ingest("contract", b"The contract expires on 30 June.")
    tools = Tools(bond)
    [source] = tools.search("The contract expires on 30 June.")
    claim = "The contract never expires."
    written(tools.write("contradiction", f'<bond:cite kind="paraphrase" from="{source.handle}">{claim}</bond:cite>'))
    assert bond.verify(head(bond, "contradiction")) == []
    [mismatch] = refused(tools.write("bad-quote", f'<bond:cite kind="quote" from="{source.handle}">{claim}</bond:cite>'))
    assert "does not match" in mismatch


def test_strict_requires_declarations_not_external_evidence(bond):
    tools = Tools(bond)
    out = written(tools.write("draft", '<bond:cite kind="unsourced">An unsupported assertion.</bond:cite>'))
    assert bond.verify(head(bond, "draft")) == []
    assert out.support is SupportStatus.UNSUPPORTED
    [hit] = tools.search("unsupported assertion")
    assert hit.support is SupportStatus.UNSUPPORTED and hit.kinds == [K.UNSOURCED]
    assert tools.trace(hit.handle).status is SupportStatus.UNSUPPORTED


def test_citation_changes_change_identity_even_when_bytes_match(bond):
    source = bond.ingest("source", b"alpha").version
    support = Ref(source, TextLocator(0, 5))
    a = bond.write_document("a", b"alpha", [Citation(K.QUOTE, TextLocator(0, 5), [support])], format=Format.MARKDOWN)
    b = bond.write_document("b", b"alpha", [Citation(K.PARAPHRASE, TextLocator(0, 5), [support])], format=Format.MARKDOWN)
    assert a.version != b.version
    assert details.version(bond, a.version).content_hash == details.version(bond, b.version).content_hash


def test_trace_reports_gaps_inside_partly_cited_ancestry_and_honest_limits(bond):
    partial = bond.write_document(
        "partial",
        b"Known. Unknown.",
        [
        Citation(K.SOURCE, TextLocator(0, 6), []),
    ],
        format=Format.MARKDOWN,
        policy=ALLOW_GAPS,
    )
    tools = Tools(bond)
    [hit] = tools.search("Known. Unknown.")
    written(tools.write("summary", f'<bond:cite kind="paraphrase" from="{hit.handle}">A conclusion.</bond:cite>'))
    [summary] = tools.search("A conclusion.")
    why = tools.trace(summary.handle)
    assert not why.truncated and why.unreachable == []
    partial_node = next(s for s in why.spans if s.documents == ["partial"])
    assert partial_node.undeclared == [partial_node.handle.split("@")[0] + "@7-15"]  # "Unknown."
    bounded = tools.trace(summary.handle, limit=1)
    assert bounded.truncated and bounded.unreachable == []
    assert not bond.trace(Ref(head(bond, "summary"), VersionLocator()), max_depth=0).complete
    assert bond.trace(Ref(partial.version, TextLocator(7, 15)), max_depth=0).complete


def test_linked_unsourced_material_stays_unsourced(bond, tmp_path):
    with Bond.local(tmp_path / "external") as source:
        written(Tools(source).write("draft", '<bond:cite kind="unsourced">Unsupported assertion.</bond:cite>'))
        bond.set_links([source])
        tools = Tools(bond)
        [hit] = tools.search("Unsupported assertion.")
        assert (hit.workspace, hit.documents, hit.support) == ("external", ["draft"], SupportStatus.UNSUPPORTED)
        assert bond.citations(head(source, "draft"))[0].kind is K.UNSOURCED
        assert bond.documents() == []  # nothing was copied


def test_trace_distinguishes_unreachable_from_truncated(bond, tmp_path):
    with Bond.local(tmp_path / "external") as source, Bond.local(tmp_path / "library") as library:
        root = source.ingest("root", b"Root fact.").version
        library.set_links([source])
        library.write_document("derived", b"Root fact.", [Citation(K.QUOTE, TextLocator(0, 10), [Ref(root, TextLocator(0, 10))])],
                               format=Format.MARKDOWN)
        library.set_links([])  # the library stops linking to what derived rests on
        bond.set_links([library])
        tools = Tools(bond)
        [hit] = tools.search("Root fact.")
        assert hit.support is SupportStatus.UNKNOWN and hit.workspace == "library"
        why = tools.trace(hit.handle)
        assert why.status is SupportStatus.UNKNOWN and not why.truncated and why.unreachable == [root]
        assert why.spans[-1].handle == root and why.spans[-1].rests_on == [] and why.spans[-1].unreachable
        library.set_links([source])
        again = tools.trace(hit.handle)
        assert again.status is SupportStatus.SUPPORTED and again.unreachable == []
        assert [g.workspace for g in again.spans] == ["library", "external"]


def test_trace_detects_unsourced_self_support(bond):
    version = bond.write_document(
        "self",
        b"guess result",
        [
        Citation(K.UNSOURCED, TextLocator(0, 5), []),
        Citation(K.PARAPHRASE, TextLocator(6, 12), [SelfRef(TextLocator(0, 5))]),
    ],
        format=Format.MARKDOWN,
    ).version
    tools = Tools(bond)
    [hit] = tools.search("result")
    assert hit.support is SupportStatus.UNSUPPORTED
    why = tools.trace(hit.handle)
    assert not why.truncated and why.status is SupportStatus.UNSUPPORTED
    assert why.spans[-1].undeclared == []
    assert any(link.kind is K.UNSOURCED for link in why.spans[-1].rests_on)
    assert bond.verify(version) == []


def test_search_annotates_every_match_rather_than_filtering(bond):
    tools = Tools(bond)
    written(tools.write("first", '<bond:cite kind="unsourced">Repeated phrase.</bond:cite>'))
    bond.ingest("second", b"Repeated phrase.")
    hits = tools.search("Repeated phrase.")
    assert [(h.documents, h.support) for h in hits] == [
        (["first"], SupportStatus.UNSUPPORTED), (["second"], SupportStatus.SUPPORTED)]
    assert len(tools.search("Repeated phrase.", limit=1)) == 1


def test_invalid_agent_query_limits_are_repairable_errors(bond):
    from bond.authoring import ToolError

    bond.ingest("source", b"Source.")
    tools = Tools(bond)
    [hit] = tools.search("Source.")
    with pytest.raises(ToolError, match="limit must be at least 1"):
        tools.trace(hit.handle, limit=0)
    with pytest.raises(ToolError, match="limit must be at least 1"):
        tools.search("Source.", limit=0)
