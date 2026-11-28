import json

import pytest

from bond import (
    ALLOW_GAPS,
    Citation,
    CitationKind,
    Format,
    JsonLocator,
    PdfLocator,
    Ref,
    SelfRef,
    TextLocator,
    VersionLocator,
    details,
)
from bond.importers.reducto import import_reducto
from bond.models import StoredCitation, SupportStatus
from case_001 import CITATIONS, SUMMARY, build_citations, claim_locator, leaves
from conftest import FIXTURES


def supported(bond, phrase, **scope):
    """The matches of phrase whose support reaches declared sources (an OCR parse also holds unsourced copies)."""
    return [h for h in bond.search(phrase, **scope) if bond.check_support(h.ref).status is SupportStatus.SUPPORTED]


def kinds_at(trace, node):
    return sorted(e.citation.kind.value for e in trace.edges_at(node))


def test_every_summary_claim_traces_to_a_pdf_region_or_source(bond, case):
    result = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN)
    for spec in CITATIONS:
        assert isinstance(spec.claim, str)
        trace = bond.trace(Ref(result.version, claim_locator(SUMMARY, spec.claim)))
        assert trace.edges_at(trace.root), spec.claim
        if spec.kind is CitationKind.UNSOURCED:  # a heading the writer made up: a root, declared as such
            assert kinds_at(trace, trace.root) == ["unsourced"] and leaves(trace) == [trace.root]
            continue
        for leaf in leaves(trace):
            assert kinds_at(trace, leaf) == ["source"]
            if leaf.version == case["pdf"]:
                assert isinstance(leaf.locator, PdfLocator)
            else:
                assert leaf.version == case["policy"]
        # Claims citing the fax resolve to a bounding box on a page.
        if any(s.document == "fax" for s in spec.supports):
            assert any(isinstance(leaf.locator, PdfLocator) for leaf in leaves(trace))


def test_trace_follows_overlap(bond, case):
    result = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN)
    item = claim_locator(
        SUMMARY,
        'The tenant reports a "progressive ceiling leak in the bathroom" with mold spreading over the wall behind the toilet.',
    )
    trace = bond.trace(Ref(result.version, item))
    # The list item is claimed by a paraphrase and, inside it, by a quote.
    assert kinds_at(trace, trace.root) == ["paraphrase", "quote"]
    hops = [s for e in trace.edges_at(trace.root) for s in e.citation.supports]
    assert all(n.version == case["fax"] and kinds_at(trace, n) == ["transcription"] for n in hops)

    # A whole-document reference picks up every citation.
    whole = bond.trace(Ref(result.version, VersionLocator()))
    assert len(whole.edges_at(whole.root)) == len(CITATIONS)


def test_uncited_regions_and_sources_are_roots(bond, case):
    title = "Intake summary: Jordan Avery"
    specs = [c for c in CITATIONS if c.claim != title]
    result = bond.write_document(
        "summary",
        SUMMARY.encode(),
        build_citations(bond, case, specs=specs),
        format=Format.MARKDOWN,
        policy=ALLOW_GAPS,
    )
    gap = Ref(result.version, claim_locator(SUMMARY, title))
    assert bond.declarations(Ref(result.version, VersionLocator())).undeclared == (gap,)
    trace = bond.trace(gap)
    assert trace.nodes == [gap] and trace.edges == []

    # An ingested file ends the chain with its whole-version source edge.
    trace = bond.trace(Ref(case["policy"], TextLocator(0, 10)))
    [edge] = trace.edges
    assert edge.node == trace.root and edge.citation.kind is CitationKind.SOURCE and edge.citation.supports == []


def test_max_depth(bond, case):
    result = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN)
    ref = Ref(result.version, claim_locator(SUMMARY, "No significant damage to other rooms."))
    full = bond.trace(ref)
    assert [e.citation.kind for e in full.edges] == [CitationKind.QUOTE, CitationKind.TRANSCRIPTION, CitationKind.SOURCE]
    one = bond.trace(ref, max_depth=1)
    assert len(one.nodes) == 2 and len(one.edges) == 1  # the fax node is reached, but not followed
    zero = bond.trace(ref, max_depth=0)
    assert zero.nodes == [ref] and zero.edges == []
    with pytest.raises(ValueError, match="ends past the document"):
        bond.trace(Ref(result.version, TextLocator(0, 10_000)))


def test_shared_supports_are_one_node(bond, case):
    content = "One. Two."
    one = supported(bond, "Active drip from the bathroom ceiling light fixture.",
                    within=Ref(case["fax"], VersionLocator()))[0].ref
    assert isinstance(one.locator, JsonLocator)
    whole = Ref(one.version, JsonLocator(one.locator.path))
    v = bond.write_document(
        "twice",
        content.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, 4), [whole]),
        Citation(CitationKind.PARAPHRASE, TextLocator(5, 9), [whole]),
    ],
        format=Format.MARKDOWN,
    ).version
    trace = bond.trace(Ref(v, VersionLocator()))
    assert len(trace.nodes) == len(set(trace.nodes)) and trace.nodes[0] == trace.root
    # Both citations rest on the same fax value; it is one node, followed once.
    [a, b] = trace.edges_at(trace.root)
    assert a.citation.supports == b.citation.supports == [whole]
    assert kinds_at(trace, whole) == ["transcription"]
    assert sum(e.node == whole for e in trace.edges) == 1


def test_self_supports(bond, case):
    content = "Finding. Conclusion."
    [hit] = supported(bond, "No significant damage to other rooms.", within=Ref(case["fax"], VersionLocator()))
    v = bond.write_document(
        "self",
        content.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, 8), [hit.ref]),
        Citation(CitationKind.PARAPHRASE, TextLocator(9, 20), [SelfRef(TextLocator(0, 8))]),
    ],
        format=Format.MARKDOWN,
        policy=ALLOW_GAPS,
    ).version
    trace = bond.trace(Ref(v, TextLocator(9, 20)))
    [first] = trace.edges_at(trace.root)
    [finding] = first.citation.supports
    assert finding == Ref(v, TextLocator(0, 8)) and kinds_at(trace, finding) == ["paraphrase"]


def test_summary_of_a_summary_resolves_to_the_page(bond, case):
    bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN)
    second = "Inspection: active drip from the bathroom ceiling."
    [hit] = bond.search("Active drip from the bathroom ceiling light fixture.", documents=["summary"])
    result = bond.write_document(
        "brief",
        second.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, len(second)), [hit.ref])
    ],
        format=Format.MARKDOWN,
    )
    [leaf] = leaves(bond.trace(Ref(result.version, TextLocator(0, len(second)))))
    assert leaf.version == case["pdf"]
    assert isinstance(leaf.locator, PdfLocator) and leaf.locator.page == 1


# --- importer -------------------------------------------------------------------


def test_every_text_block_of_the_ocr_output_cites_a_region(bond, case):
    text = bond.text(Ref(case["fax"], VersionLocator()))
    assert text is not None
    ocr = json.loads(text)
    blocks = {
        f"/result/chunks/{ci}/blocks/{bi}/content": block
        for ci, chunk in enumerate(ocr["result"]["chunks"])
        for bi, block in enumerate(chunk["blocks"])
    }
    citations = bond.citations(case["fax"])
    cited = {details.pointer(bond, Ref(case["fax"], c.claim)): c for c in citations if c.kind is CitationKind.TRANSCRIPTION}
    assert set(cited) == set(blocks)
    for pointer, c in cited.items():
        assert c.producer == "reducto"
        [support] = c.supports
        assert support.version == case["pdf"] and isinstance(support.locator, PdfLocator)
        bbox = blocks[pointer]["bbox"]
        assert support.locator.page == bbox["page"] - 1
        assert support.locator.bbox[0] == bbox["left"]


def test_each_vendor_field_of_the_ocr_output_is_cited_as_what_it_is(bond, case):
    by_kind: dict[CitationKind, dict[str, StoredCitation]] = {}
    for c in bond.citations(case["fax"]):
        by_kind.setdefault(c.kind, {})[details.pointer(bond, Ref(case["fax"], c.claim))] = c
    block = "/result/chunks/0/blocks/0"
    # type labels the same region the content was read from.
    label = by_kind[CitationKind.CLASSIFICATION][f"{block}/type"]
    assert label.supports == by_kind[CitationKind.TRANSCRIPTION][f"{block}/content"].supports
    assert label.producer == "reducto"
    unsourced = by_kind[CitationKind.UNSOURCED]
    assert {"/job_id", "/usage", "/result/type", "/result/chunks/0/content", f"{block}/bbox"} <= set(unsourced)
    assert not any("/blocks/" in p and p.endswith(("/content", "/type", "/confidence")) for p in unsourced)
    assert all(c.supports == [] and c.producer is None for c in unsourced.values())
    assert bond.declarations(Ref(case["fax"], VersionLocator())).undeclared == ()
    # An unsourced leaf traces to an edge with no supports: it rests on nothing.
    [edge] = bond.trace(details.locate_pointer(bond, case["fax"], "/result/chunks/0/blocks/0/bbox/top")).edges
    assert edge.citation.kind is CitationKind.UNSOURCED and edge.citation.supports == []


def test_confidence_traces_through_the_transcribed_text_to_the_page(bond, case):
    block = "/result/chunks/0/blocks/0"
    trace = bond.trace(details.locate_pointer(bond, case["fax"], f"{block}/confidence"))
    [edge] = trace.edges_at(trace.root)
    assert edge.citation.kind is CitationKind.ASSESSMENT
    # The assessment rests on the text the transcriber read, which the transcription cites on the page.
    [judged] = edge.citation.supports
    assert judged == details.locate_pointer(bond, case["fax"], f"{block}/content")
    assert bond.text(judged) == "FAX COVER SHEET"  # the fax's first block, as OCR read it
    [transcription] = trace.edges_at(judged)
    assert transcription.citation.kind is CitationKind.TRANSCRIPTION
    assert Ref(case["fax"], transcription.citation.claim) == details.locate_pointer(bond, case["fax"], f"{block}/content")
    [page] = transcription.citation.supports
    assert page.version == case["pdf"] and kinds_at(trace, page) == ["source"]


def test_ocr_output_is_stored_byte_for_byte(bond, case):
    assert bond.text(Ref(case["fax"], VersionLocator())) == (FIXTURES / "fax_case_001.reducto.json").read_text(encoding="utf-8")
    # Re-importing is idempotent.
    pdf = (FIXTURES / "fax_case_001.pdf").read_bytes()
    again = import_reducto(bond, pdf, (FIXTURES / "fax_case_001.reducto.json").read_bytes(), "fax_case_001")
    assert again.parse.version == case["fax"]


def test_reimporting_new_ocr_output_advances_it_and_keeps_the_old_one(bond, case):
    ocr = json.loads((FIXTURES / "fax_case_001.reducto.json").read_text(encoding="utf-8"))
    ocr["job_id"] = "synthetic-0002"
    pdf = (FIXTURES / "fax_case_001.pdf").read_bytes()
    other = import_reducto(bond, pdf, json.dumps(ocr, indent=2, ensure_ascii=False).encode(), "fax_case_001")
    assert other.pdf.version == case["pdf"] and not other.pdf.advanced
    assert other.parse.version != case["fax"] and other.parse.advanced
    assert [h.version for h in bond.history("fax_case_001.reducto")] == [case["fax"], other.parse.version]
    # Search reads current versions; the old OCR output is still there, searchable within it, and traceable.
    assert len(supported(bond, "Active drip")) == 1
    assert len(supported(bond, "Active drip", within=Ref(case["fax"], VersionLocator()))) == 1
    assert isinstance(bond.citations(other.parse.version)[0].claim, JsonLocator)


def test_transcriptions_of_one_pdf_under_different_names_coexist(bond, case):
    ocr = json.loads((FIXTURES / "fax_case_001.reducto.json").read_text(encoding="utf-8"))
    ocr["job_id"] = "synthetic-0002"
    pdf = (FIXTURES / "fax_case_001.pdf").read_bytes()
    second = import_reducto(bond, pdf, json.dumps(ocr, indent=2, ensure_ascii=False).encode(), "fax_case_001_rerun")
    assert second.pdf.version == case["pdf"]  # one PDF version, filed under both names
    assert [d.name for d in details.filed_in(bond, case["pdf"])] == ["fax_case_001", "fax_case_001_rerun"]
    assert len(supported(bond, "Active drip")) == 2


def test_a_trace_limit_keeps_the_nearest_and_says_so(bond, case):
    trace = bond.trace(Ref(case["fax"], VersionLocator()), limit=2)
    assert not trace.complete and len(trace.nodes) == 2
    everything = bond.trace(Ref(case["fax"], VersionLocator()))
    assert everything.complete and trace.nodes == everything.nodes[:2]
