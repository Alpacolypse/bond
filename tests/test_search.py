import pytest

from bond import (
    ALLOW_GAPS,
    Citation,
    CitationKind,
    Format,
    JsonLocator,
    Ref,
    SupportStatus,
    TextLocator,
    VersionLocator,
    details,
)
from bond.normalize import normalize
from bond.parsers import KeysContext, LineContext


def test_fixture_phrases_are_found_with_raw_locators(bond, case):
    for phrase in [
        "progressive ceiling leak in the bathroom",  # hyphenated across lines
        "No significant damage to other rooms.",  # ligature in the OCR text
        '"no response."',  # curly quotes in the OCR text
        "no exposed wiring",  # line break in the OCR text
        "6 weeks of written notice",  # in the policy
    ]:
        hits = bond.search(phrase)
        assert hits, phrase
        for h in hits:
            text = bond.text(h.ref)
            assert normalize(text).text == normalize(phrase).text


def test_hit_details(bond, case):
    [hit] = [h for h in bond.search("Habitability defect, water intrusion") if h.kinds == {CitationKind.TRANSCRIPTION}]
    assert hit.ref.version == case["fax"]
    assert hit.ref == details.locate_pointer(bond, case["fax"], "/result/chunks/1/blocks/6/content", (12, 48))
    assert hit.documents == ["fax_case_001.reducto"]
    assert hit.context == KeysContext(("result", "chunks", "1", "blocks", "6"))
    assert hit.kinds == {CitationKind.TRANSCRIPTION}

    [hit] = bond.search("Worsening conditions or a serious threat to health or safety")
    assert hit.kinds == {CitationKind.SOURCE}
    assert isinstance(hit.context, LineContext) and hit.context.headings == (
        "Northwind Legal Aid: Intake Policy IP-112", "Repair and Deduct Cases", "Criteria",
    )


def test_ambiguous_phrases_return_every_match(bond, case):
    hits = bond.search("written notice", within=Ref(case["policy"], VersionLocator()))
    assert len(hits) == 2
    assert len({h.ref.locator for h in hits}) == 2


def test_hits_carry_the_kinds_covering_them(bond, case):
    # Nothing is hidden: the block's text is transcribed, its chunk-level copies are unsourced.
    hits = bond.search("Water leaking through")
    by_kinds = {h.kinds: h for h in hits}
    assert set(by_kinds) == {frozenset({CitationKind.TRANSCRIPTION}), frozenset({CitationKind.UNSOURCED})}
    assert "/blocks/" in details.pointer(bond, by_kinds[frozenset({CitationKind.TRANSCRIPTION})].ref)
    assert all("/blocks/" not in details.pointer(bond, h.ref) for h in hits if h.kinds == {CitationKind.UNSOURCED})
    [job] = bond.search("synthetic-0001")
    assert job.kinds == {CitationKind.UNSOURCED}
    # Vendor scores are cited by an assessment resting on the transcribed text.
    hits = bond.search("high", within=Ref(case["fax"], VersionLocator()))
    assert hits and all(details.pointer(bond, h.ref).endswith("/confidence") and h.kinds == {CitationKind.ASSESSMENT}
                        for h in hits)


def test_check_support_separates_supported_hits(bond, case):
    # Search returns every match; check_support says which rest on declared sources.
    hits = bond.search("Water leaking through")
    supported = [h for h in hits if bond.check_support(h.ref).status is SupportStatus.SUPPORTED]
    assert [h.kinds for h in supported] == [{CitationKind.TRANSCRIPTION}]
    [job] = bond.search("synthetic-0001")
    assert bond.check_support(job.ref).status is SupportStatus.UNSUPPORTED  # job metadata: unsourced
    assert len(bond.search("Water leaking through", limit=1)) == 1


def test_uncited_text_is_found_and_marked(bond, case):
    first = "Water leaking through the bathroom ceiling for 8 weeks."
    content = f"# Draft heading\n\n{first}\n\nLandlord ignored every notice.\n"
    start = content.index(first)
    [support] = [h.ref for h in bond.search(first) if h.kinds == {CitationKind.TRANSCRIPTION}]
    result = bond.write_document(
        "draft",
        content.encode(),
        [
        Citation(CitationKind.QUOTE, TextLocator(start, start + len(first)), [support]),
    ],
        format=Format.MARKDOWN,
        policy=ALLOW_GAPS,
    )
    [gap] = bond.search("ignored every notice", within=Ref(result.version, VersionLocator()))
    assert gap.kinds == frozenset()
    [heading] = bond.search("Draft heading", within=Ref(result.version, VersionLocator()))
    assert heading.kinds == frozenset()


def test_within_pins_a_phrase_inside_a_value(bond, case):
    block = details.locate_pointer(bond, case["fax"], "/result/chunks/1/blocks/3/content")
    [hit] = bond.search("written notice", within=block)
    assert isinstance(hit.ref.locator, JsonLocator)
    assert details.pointer(bond, hit.ref) == "/result/chunks/1/blocks/3/content"
    assert bond.search("written notice", within=details.locate_pointer(bond, case["fax"], "/result/chunks/1/blocks/1/content")) == []

    whole = bond.search("written notice", within=Ref(case["policy"], VersionLocator()))
    first = whole[0].ref.locator
    assert isinstance(first, TextLocator)
    within = Ref(case["policy"], TextLocator(first.start - 5, first.end + 5))
    assert [h.ref for h in bond.search("written notice", within=within)] == [whole[0].ref]


def test_limit(bond, case):
    assert len(bond.search("leak", limit=2)) == 2
    with pytest.raises(ValueError, match="limit must be at least 1"):
        bond.search("leak", limit=0)


def test_search_takes_documents_or_a_reference_not_both(bond, case):
    with pytest.raises(ValueError, match="documents or within, not both"):
        bond.search("pain", documents=["policy_ip112"], within=Ref(case["fax"], VersionLocator()))
