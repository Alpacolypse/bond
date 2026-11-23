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
    SupportStatus,
    TextLocator,
    VersionId,
    VersionLocator,
    WriteError,
    details,
)
from bond import (
    IssueCode as E,
)
from bond.ids import citation_id
from bond.policies import PRODUCERS, Policy
from case_001 import SUMMARY, build_citations, claim_locator


def write_summary(bond, case, content=SUMMARY, citations=None, base=None):
    citations = citations if citations is not None else build_citations(bond, case)
    return bond.write_document("summary_case_001", content.encode(), citations, format=Format.MARKDOWN, base=base)


def supported(bond, phrase, **kwargs):
    """Search hits whose support reaches declared sources: an OCR parse also holds unsourced copies of its text."""
    return [h for h in bond.search(phrase, **kwargs) if bond.check_support(h.ref).status is SupportStatus.SUPPORTED]


def whole(version):
    return Ref(version, VersionLocator())


def gaps(bond, version):
    return list(bond.declarations(whole(version)).undeclared)


def quote(bond, claim, version, phrase):
    [hit] = supported(bond, phrase, within=whole(version))
    return Citation(CitationKind.QUOTE, claim, [hit.ref])


# --- the fixture summary -----------------------------------------------------


def test_fixture_summary_is_written_and_read_back(bond, case):
    citations = build_citations(bond, case)
    result = write_summary(bond, case, citations=citations)

    assert bond.text(whole(result.version)) == SUMMARY
    stored = {c.id: c for c in bond.citations(result.version)}
    ids = [citation_id(c) for c in citations]
    assert sorted(stored) == sorted(ids)
    # Supports are a set too: they are stored in a canonical order.
    assert [(stored[i].kind, stored[i].claim, set(stored[i].supports), stored[i].attrs) for i in ids] == [
        (c.kind, c.claim, set(c.supports), c.attrs) for c in citations
    ]
    assert gaps(bond, result.version) == []
    assert [c.kind for c in bond.citations(case["policy"])] == [CitationKind.SOURCE]


def test_identical_rewrite_returns_existing_version(bond, case):
    first = write_summary(bond, case)
    again = write_summary(bond, case)
    assert again.version == first.version
    assert (first.advanced, again.advanced) == (True, False)
    assert len(bond.history("summary_case_001")) == 1


# --- broken copies fail with every error at once ------------------------------


def test_broken_summary_reports_every_error_at_once(bond, case):
    content = SUMMARY.replace(
        "No significant damage to other rooms.",
        "No significant damage to other rooms.\n- Tenant reports no earlier repairs.",  # new, uncited claim
    )
    good = build_citations(bond, case, content)
    broken = list(good)
    # 0: misquote -> QUOTE_MISMATCH
    i = next(i for i, c in enumerate(good) if c.claim == claim_locator(content, "Active drip from the bathroom ceiling light fixture."))
    [hit] = supported(bond, "Black mold over 4 square feet of the wall", within=whole(case["fax"]))
    broken[i] = Citation(CitationKind.QUOTE, good[i].claim, [hit.ref])
    # 1: claim outside the document -> BAD_LOCATOR
    broken.append(Citation(CitationKind.PARAPHRASE, TextLocator(0, len(content) + 10), good[0].supports))
    # 2: support in a version that doesn't exist -> UNKNOWN_VERSION
    broken.append(Citation(CitationKind.PARAPHRASE, good[0].claim, [Ref(VersionId("f" * 64), TextLocator(0, 3))]))
    # 3: quote citing a PDF directly -> INVALID_CITATION
    broken.append(Citation(CitationKind.QUOTE, good[0].claim, [Ref(case["pdf"], PdfLocator(0, (0, 0, 1, 1)))]))
    # 4: support locator outside its version -> BAD_LOCATOR
    broken.append(Citation(CitationKind.PARAPHRASE, good[0].claim, [Ref(case["fax"], JsonLocator((99,)))]))

    with pytest.raises(WriteError) as exc:
        bond.write_document("summary_case_001", content.encode(), broken, format=Format.MARKDOWN)

    errors = exc.value.errors
    assert sorted(e.code.value for e in errors) == sorted(c.value for c in [
        E.QUOTE_MISMATCH, E.BAD_LOCATOR, E.UNKNOWN_VERSION, E.INVALID_CITATION, E.BAD_LOCATOR, E.UNCOVERED_CLAIM,
    ])
    by_code = {e.code: e for e in errors}
    assert by_code[E.QUOTE_MISMATCH].citation_index == i
    assert by_code[E.UNKNOWN_VERSION].citation_index == len(good) + 1
    assert by_code[E.INVALID_CITATION].citation_index == len(good) + 2
    assert "earlier repairs" in by_code[E.UNCOVERED_CLAIM].message
    assert by_code[E.UNCOVERED_CLAIM].locator == claim_locator(content, "Tenant reports no earlier repairs.")
    # Nothing was written.
    assert "summary_case_001" not in [d.name for d in bond.documents()]


def test_invalid_content(bond, case):
    with pytest.raises(WriteError) as exc:
        bond.write_document("dup", b'{"a": 1, "a": 2}', [], format=Format.JSON)
    assert exc.value.codes == [E.INVALID_CONTENT]
    with pytest.raises(WriteError) as exc:
        bond.write_document("pdf", b"not a pdf", [], format=Format.PDF)
    assert exc.value.codes == [E.INVALID_CONTENT]


def test_kind_validators(bond, case):
    content = "Water leaking through the bathroom ceiling for 8 weeks."
    claim = TextLocator(0, len(content))
    [support] = [h.ref for h in supported(bond, content)]
    pdf = Ref(case["pdf"], PdfLocator(1, (0.1, 0.1, 0.9, 0.2)))

    def problems(*citations):
        with pytest.raises(WriteError) as exc:
            bond.write_document("k", content.encode(), list(citations), format=Format.MARKDOWN)
        return [(e.code, e.message) for e in exc.value.errors]

    # transcription: one PDF support and a transcriber
    assert any("requires attrs ['transcriber']" in m for _, m in problems(Citation(CitationKind.TRANSCRIPTION, claim, [pdf])))
    assert any("must be a region of a page (a pdf locator)" in m for _, m in problems(
        Citation(CitationKind.TRANSCRIPTION, claim, [support], {"transcriber": "x"})))
    # quote: exactly one support
    assert any("exactly 1 support" in m for _, m in problems(Citation(CitationKind.QUOTE, claim, [support, support])))
    # paraphrase: at least one support, no PDFs, no unknown attrs
    assert any("at least 1 support" in m for _, m in problems(Citation(CitationKind.PARAPHRASE, claim, [])))
    assert any("does not accept attrs" in m for _, m in problems(
        Citation(CitationKind.PARAPHRASE, claim, [support], {"why": "x"})))
    assert any("text, json or csv locator" in m for _, m in problems(Citation(CitationKind.PARAPHRASE, claim, [pdf])))
    # claims must address text
    assert any("claim must be a text, json or csv locator" in m for _, m in problems(
        Citation(CitationKind.PARAPHRASE, VersionLocator(), [support])))


def test_quote_is_case_sensitive_but_forgives_ocr_noise(bond, case):
    ok = "ceiling leak in the bathroom"
    w = bond.write_document("q", ok.encode(), [quote(bond, TextLocator(0, len(ok)), case["fax"], ok)], format=Format.MARKDOWN)
    assert gaps(bond, w.version) == []

    shouty = "CEILING leak in the bathroom"
    with pytest.raises(WriteError) as exc:
        bond.write_document(
            "q",
            shouty.encode(),
            [quote(bond, TextLocator(0, len(shouty)), case["fax"], shouty)],
            format=Format.MARKDOWN,
            base=w.version,
        )
    assert exc.value.codes == [E.QUOTE_MISMATCH]


def test_coverage_is_always_enforced(bond, case):
    with pytest.raises(WriteError) as exc:
        bond.write_document("u", b"But this paragraph is a claim.\n", [], format=Format.MARKDOWN)
    assert exc.value.codes == [E.UNCOVERED_CLAIM]


def uncovered_texts(bond, content: str, citations=()) -> list[str]:
    """The text of every gap a write of content would report."""
    with pytest.raises(WriteError) as exc:
        bond.write_document("m", content.encode(), list(citations), format=Format.MARKDOWN)
    assert set(exc.value.codes) == {E.UNCOVERED_CLAIM}
    gaps = [e.locator for e in exc.value.errors]
    assert all(isinstance(g, TextLocator) for g in gaps)
    return [content[g.start:g.end] for g in gaps if isinstance(g, TextLocator)]


def test_markup_is_no_escape_from_coverage(bond, case):
    content = (
        "---\nstatus: approved\n---\n\n"
        "# Case accepted\n\n"
        "```python\nTenant meets criteria\n```\n\n"
        "<div>Denied for fraud</div>\n\n"
        "| Decision | Reason |\n|---|---|\n| approved | criteria met |\n\n"
        "[policy]: https://example.org/mp-112\n"
    )
    # All text is claim text: front matter, headings, code, HTML, table headers and link definitions.
    assert uncovered_texts(bond, content) == [
        "status: approved", "Case accepted", "Tenant meets criteria", "<div>Denied for fraud</div>",
        "| Decision | Reason |", "| approved | criteria met |", "[policy]: https://example.org/mp-112",
    ]
    # Syntax is not: the fence's info string, heading markers, rules and delimiter rows need no citation.
    assert bond.write_document("syntax", b"---\n\n# \n\n```text\n```\n", [], format=Format.MARKDOWN).advanced


def test_every_word_of_a_claim_must_be_covered(bond, case):
    content = "Water leaking through the bathroom ceiling for 8 weeks."
    thin = quote(bond, TextLocator(0, 13), case["fax"], "Water leaking")
    # One cited phrase no longer covers the paragraph around it: the rest is reported exactly.
    assert uncovered_texts(bond, content, [thin]) == ["through the bathroom ceiling for 8 weeks."]


def test_punctuation_and_list_numbering_need_no_citation(bond, case):
    body = "Water leaking through the bathroom ceiling for 8 weeks"
    content = f'1. "{body}."\n'
    start = content.index(body)
    result = bond.write_document(
        "p",
        content.encode(),
        [
        quote(bond, TextLocator(start, start + len(body)), case["fax"], body),
    ],
        format=Format.MARKDOWN,
    )
    assert gaps(bond, result.version) == []


def test_json_coverage_defaults_to_every_leaf(bond, case):
    [hit] = supported(bond, "Habitability defect, water intrusion (HC 12.4)")
    content = json.dumps({"id": "det-0001", "finding": "Habitability defect, water intrusion (HC 12.4)"})
    cite = Citation(CitationKind.QUOTE, JsonLocator((1,)), [hit.ref])  # /finding, the second key
    with pytest.raises(WriteError) as exc:
        bond.write_document("det", content.encode(), [cite], format=Format.JSON)
    assert exc.value.codes == [E.UNCOVERED_CLAIM]
    assert exc.value.errors[0].locator == JsonLocator((0,))  # /id
    hint = exc.value.errors[0].hint
    assert hint is not None and "mark it unsourced if no supporting evidence is declared" in hint

    made_up = Citation(CitationKind.UNSOURCED, JsonLocator((0,)), [])
    result = bond.write_document("det", content.encode(), [cite, made_up], format=Format.JSON)
    assert gaps(bond, result.version) == []
    [made_up_hit] = bond.search("det-0001")
    assert made_up_hit.kinds == {CitationKind.UNSOURCED}


def test_json_strings_need_every_word_covered(bond, case):
    [hit] = supported(bond, "Habitability defect")
    content = json.dumps({"finding": "Habitability defect, water intrusion (HC 12.4)"})
    thin = Citation(CitationKind.QUOTE, JsonLocator((0,), (0, 19)), [hit.ref])
    with pytest.raises(WriteError) as exc:
        bond.write_document("det", content.encode(), [thin], format=Format.JSON)
    [gap] = exc.value.errors
    assert gap.locator == JsonLocator((0,), (19, 46))  # ", water intrusion (HC 12.4)"


def test_unsourced_marks_made_up_content_in_any_format(bond, case):
    body = "Water leaking through the bathroom ceiling for 8 weeks."
    content = f"Case ref: CR-2026-0042\n\n{body}\n"
    head = content.index("\n\n")
    citations = [
        Citation(CitationKind.UNSOURCED, TextLocator(0, head), []),
        quote(bond, TextLocator(head + 2, head + 2 + len(body)), case["fax"], body),
    ]
    result = bond.write_document("u", content.encode(), citations, format=Format.MARKDOWN)
    assert gaps(bond, result.version) == []


def test_unsourced_validator(bond, case):
    content = "Case ref: CR-2026-0042"
    claim = TextLocator(0, len(content))
    [support] = [h.ref for h in supported(bond, "Water leaking through the bathroom ceiling for 8 weeks.")]

    def problems(c):
        with pytest.raises(WriteError) as exc:
            bond.write_document("u", content.encode(), [c], format=Format.MARKDOWN)
        return [e.message for e in exc.value.errors]

    assert bond.write_document("u", content.encode(), [Citation(CitationKind.UNSOURCED, claim, [])],
                               format=Format.MARKDOWN).advanced
    # Attrs are stored with the citation, so they hold no free text: an unsourced claim says nothing about why.
    assert any("does not accept attrs ['reason']" in m for m in problems(
        Citation(CitationKind.UNSOURCED, claim, [], {"reason": "the tenant's unit number, from the intake call"})))
    assert any("takes no supports" in m for m in problems(Citation(CitationKind.UNSOURCED, claim, [support])))
    assert any("cannot be the whole version" in m for m in problems(
        Citation(CitationKind.UNSOURCED, VersionLocator(), [])))


def test_allow_gaps_stores_gaps(bond, case):
    first = "Water leaking through the bathroom ceiling for 8 weeks."
    content = f"{first}\n\nLandlord ignored every notice.\n"
    cite = quote(bond, TextLocator(0, len(first)), case["fax"], first)
    with pytest.raises(WriteError) as exc:
        bond.write_document("draft", content.encode(), [cite], format=Format.MARKDOWN)
    assert exc.value.codes == [E.UNCOVERED_CLAIM]

    result = bond.write_document("draft", content.encode(), [cite], format=Format.MARKDOWN, policy=ALLOW_GAPS)
    [gap] = gaps(bond, result.version)
    assert bond.text(gap) == "Landlord ignored every notice."
    # A gap is not an unsourced citation: it has no provenance at all.
    assert [c.kind for c in bond.citations(result.version)] == [CitationKind.QUOTE]
    assert bond.trace(gap).edges == []
    # Sources are not checked for coverage.
    assert gaps(bond, case["policy"]) == []
    # Other errors still fail the write.
    with pytest.raises(WriteError) as exc:
        bond.write_document(
            "draft",
            content.encode(),
            [Citation(CitationKind.QUOTE, TextLocator(0, 999), cite.supports)],
            format=Format.MARKDOWN,
            policy=ALLOW_GAPS,
        )
    assert E.BAD_LOCATOR in exc.value.codes


# --- identity -----------------------------------------------------------------


def test_same_bytes_with_different_citations_is_another_version(bond, case):
    first = write_summary(bond, case)
    citations = build_citations(bond, case)
    citations[1] = Citation(CitationKind.PARAPHRASE, citations[1].claim, citations[1].supports)
    second = write_summary(bond, case, citations=citations, base=first.version)
    assert second.version != first.version
    assert bond.blob(second.version) == bond.blob(first.version)
    def kinds(v):
        return [c.kind for c in bond.citations(v) if c.claim == citations[1].claim]

    assert kinds(first.version) == [CitationKind.QUOTE] and kinds(second.version) == [CitationKind.PARAPHRASE]
    assert [h.version for h in bond.history("summary_case_001")] == [first.version, second.version]
    # Writing the first version again is a revert: the same version, with the same citations, filed as the head.
    again = write_summary(bond, case, base=second.version)
    assert again.version == first.version
    assert [h.version for h in bond.history("summary_case_001")] == [first.version, second.version, first.version]


def test_a_versions_citations_are_a_set(bond, case):
    citations = build_citations(bond, case)
    first = write_summary(bond, case, citations=citations)
    # Another order, supports reordered, and a citation listed twice: the same citations, so the same version.
    shuffled = [Citation(c.kind, c.claim, list(reversed(c.supports)), c.attrs) for c in reversed(citations)]
    again = write_summary(bond, case, citations=shuffled + [citations[0]], base=first.version)
    assert again.version == first.version and not again.advanced
    assert len(bond.citations(first.version)) == len(citations)


def test_ids_are_reproducible_and_names_are_not_part_of_them(bond, case, tmp_path):
    from bond import Bond

    with Bond.local(tmp_path / "other") as other:
        assert other.ingest("renamed", bond.blob(case["policy"]), format=Format.MARKDOWN).version == \
            case["policy"]
    also = bond.ingest("also filed", bond.blob(case["policy"]), format=Format.MARKDOWN)
    assert also.version == case["policy"]
    assert [d.name for d in details.filed_in(bond, case["policy"])] == ["policy_ip112", "also filed"]


def test_written_bytes_can_also_be_ingested_as_a_source(bond, case):
    result = write_summary(bond, case)
    ref = bond.ingest("summary as received", SUMMARY.encode(), format=Format.MARKDOWN)
    assert ref.version != result.version and bond.blob(ref.version) == bond.blob(result.version)
    [source] = bond.citations(ref.version)
    assert source.kind is CitationKind.SOURCE and isinstance(source.claim, VersionLocator)
    # Copying a source's text verbatim is a new version too, resting on the source.
    policy = bond.text(whole(case["policy"]))
    copy = bond.write_document(
        "policy copy",
        policy.encode(),
        [
        Citation(CitationKind.QUOTE, u, [Ref(case["policy"], u)]) for u in _units(policy)
    ],
        format=Format.MARKDOWN,
    )
    assert copy.version != case["policy"]


def test_a_document_cannot_cite_itself_by_id(bond, case):
    # Its id hashes its citations, so no citation can contain it; the content hash is not a version.
    from bond.ids import hash_bytes

    content = "Self-supporting claim."
    with pytest.raises(WriteError) as exc:
        bond.write_document(
            "self",
            content.encode(),
            [
            Citation(CitationKind.QUOTE, TextLocator(0, len(content)),
                     [Ref(VersionId(hash_bytes(content.encode())), TextLocator(0, len(content)))])
        ],
            format=Format.MARKDOWN,
        )
    assert E.UNKNOWN_VERSION in exc.value.codes


def test_self_supports_are_hashed_relative_and_stored_absolute(bond, case):
    from bond.ids import hash_bytes, version_id

    content, p1, p2 = _two_paragraphs()
    result = bond.write_document(
        "self",
        content.encode(),
        [
        quote(bond, p1, case["fax"], content[p1.start:p1.end]),
        Citation(CitationKind.PARAPHRASE, p2, [SelfRef(p1)]),
    ],
        format=Format.MARKDOWN,
    )
    stored = bond.citations(result.version)
    assert stored[1].supports == [Ref(result.version, p1)]
    hashed = [c.as_written() for c in stored]
    assert hashed[1].supports == [SelfRef(p1)] and hashed[0].supports[0].version == case["fax"]
    assert version_id(hash_bytes(content.encode()), Format.MARKDOWN, hashed) == result.version


def test_a_store_with_another_schema_is_refused(tmp_path):
    import sqlite3

    from bond import Bond, BondError

    root = tmp_path / "old"
    root.mkdir()
    db = sqlite3.connect(root / "bond.db")
    db.executescript("CREATE TABLE bond_schema (version INTEGER NOT NULL); INSERT INTO bond_schema VALUES (1);")
    db.close()
    with pytest.raises(BondError, match="Bond schema 1, but this Bond needs .*; re-create it"):
        Bond.local(root)


def _units(markdown):
    from bond.parsers import MarkdownDoc

    return MarkdownDoc(markdown).claims()


# --- citations within a document ----------------------------------------------


def _two_paragraphs():
    first = "Water leaking through the bathroom ceiling for 8 weeks."
    second = "Leg pain for two months."
    content = f"{first}\n\n{second}\n"
    return content, TextLocator(0, len(first)), TextLocator(len(first) + 2, len(first) + 2 + len(second))


def test_a_document_cites_its_own_parts(bond, case):
    content, p1, p2 = _two_paragraphs()
    citations = [
        quote(bond, p1, case["fax"], content[p1.start:p1.end]),
        Citation(CitationKind.PARAPHRASE, p2, [SelfRef(p1)]),  # a support in this document
    ]
    result = bond.write_document("self", content.encode(), citations, format=Format.MARKDOWN)
    stored = bond.citations(result.version)[1]
    assert stored.supports == [Ref(result.version, p1)]
    # Provenance composes inside the document: p2 -> p1 -> the fax's OCR output.
    trace = bond.trace(Ref(result.version, p2))
    [edge] = trace.edges_at(trace.root)
    [p1_node] = edge.citation.supports
    assert p1_node == Ref(result.version, p1)
    assert [e.citation.kind for e in trace.edges_at(p1_node)] == [CitationKind.QUOTE]


def test_circular_support_is_rejected(bond, case):
    content, p1, p2 = _two_paragraphs()
    with pytest.raises(WriteError) as exc:
        bond.write_document(
            "loop",
            content.encode(),
            [
            Citation(CitationKind.PARAPHRASE, p1, [SelfRef(p2)]),
            Citation(CitationKind.PARAPHRASE, p2, [SelfRef(p1)]),
        ],
            format=Format.MARKDOWN,
        )
    [loop] = exc.value.errors
    assert loop.code is E.CIRCULAR_SUPPORT and loop.cycle == (0, 1) and "0 -> 1 -> 0" in loop.message

    with pytest.raises(WriteError) as exc:
        bond.write_document(
            "loop",
            content.encode(),
            [
            Citation(CitationKind.PARAPHRASE, TextLocator(0, len(content) - 1), [SelfRef(p1)]),
        ],
            format=Format.MARKDOWN,
        )
    assert exc.value.codes == [E.CIRCULAR_SUPPORT]


def test_classification_and_assessment_validators(bond, case):
    content = json.dumps({"label": "Title", "text": "FAX COVER SHEET", "score": "high"})
    pdf = Ref(case["pdf"], PdfLocator(0, (0.1, 0.1, 0.4, 0.2)))
    label, text, score = JsonLocator((0,)), JsonLocator((1,)), JsonLocator((2,))
    good = [
        Citation(CitationKind.TRANSCRIPTION, text, [pdf], {"transcriber": "reducto"}),
        Citation(CitationKind.CLASSIFICATION, label, [pdf], {"classifier": "reducto"}),
    ]
    good.append(Citation(CitationKind.ASSESSMENT, score, [SelfRef(text)], {"assessor": "reducto"}))
    assert gaps(bond, bond.write_document("v", content.encode(), good, format=Format.JSON).version) == []

    def problems(i, c):
        citations = list(good)
        citations[i] = c
        with pytest.raises(WriteError) as exc:
            bond.write_document("v2", content.encode(), citations, format=Format.JSON)
        return [(e.code, e.message) for e in exc.value.errors]

    assert any("requires attrs ['classifier']" in m for _, m in problems(
        1, Citation(CitationKind.CLASSIFICATION, label, [pdf])))
    assert any("must be a region of a page (a pdf locator)" in m for _, m in problems(
        1, Citation(CitationKind.CLASSIFICATION, label, [SelfRef(text)], {"classifier": "reducto"})))
    assert any("assessment support 0 must be a text, json or csv locator, got pdf" in m for _, m in problems(
        2, Citation(CitationKind.ASSESSMENT, score, [pdf], {"assessor": "reducto"})))
    assert any(c is E.CIRCULAR_SUPPORT for c, _ in problems(
        2, Citation(CitationKind.ASSESSMENT, score, [SelfRef(score)], {"assessor": "reducto"})))
    # Vendor values are cited where they sit, never copied into attrs.
    assert any("does not accept attrs ['confidence']" in m for _, m in problems(
        0, Citation(CitationKind.TRANSCRIPTION, text, [pdf], {"transcriber": "reducto", "confidence": "0.9"})))
    # A producer is a tool's id, never free text; and one the policy knows.
    for producer in ["Dr. Okoro's OCR", "Acme OCR", "", "-acme", "a" * 64]:
        assert any(c is E.INVALID_CITATION and m.startswith("attrs.transcriber must be a producer id, got ")
                   for c, m in problems(0, Citation(CitationKind.TRANSCRIPTION, text, [pdf], {"transcriber": producer})))
    assert [c for c, _ in problems(0, Citation(CitationKind.TRANSCRIPTION, text, [pdf], {"transcriber": "t"}))] == [
        E.UNKNOWN_PRODUCER]


def test_a_policy_names_the_producers_it_knows(bond, case):
    content = json.dumps({"text": "FAX COVER SHEET"})
    read = [Citation(CitationKind.TRANSCRIPTION, JsonLocator((0,)), [Ref(case["pdf"], PdfLocator(0, (0.1, 0.1, 0.4, 0.2)))],
                     {"transcriber": "acme-ocr"})]
    with pytest.raises(WriteError) as exc:
        bond.write_document("ocr", content, read, format=Format.JSON)
    [issue] = exc.value.errors
    assert (issue.code, issue.citation_index, issue.hint) == (E.UNKNOWN_PRODUCER, 0, "known producers: pypdf, reducto")
    acme = Policy(producers=PRODUCERS | {"acme-ocr"})  # an importer of another vendor's output
    written = bond.write_document("ocr", content, read, format=Format.JSON, policy=acme)
    assert written.advanced and bond.verify(written.version, acme) == []
    # Verify checks against the policy supplied now, which may not know the producer.
    assert [i.code for i in bond.verify(written.version)] == [E.UNKNOWN_PRODUCER]


# --- source regions -------------------------------------------------------------


def test_part_of_a_document_can_be_a_source_while_the_rest_is_cited(bond, case):
    fact = "Water leaking through the bathroom ceiling for 8 weeks."
    rationale = "In my judgment, repair and deduct is warranted now."
    content = f"{fact}\n\n{rationale}\n"
    r = content.index(rationale)
    own = TextLocator(r, r + len(rationale))
    result = bond.write_document(
        "determination",
        content.encode(),
        [
        quote(bond, TextLocator(0, len(fact)), case["fax"], fact),
        Citation(CitationKind.SOURCE, own, []),
    ],
        format=Format.MARKDOWN,
    )
    assert gaps(bond, result.version) == []

    # Search treats the region like any source: citable primary evidence.
    [hit] = supported(bond, "repair and deduct is warranted")
    assert hit.kinds == {CitationKind.SOURCE}
    [hit] = bond.search("Water leaking through", within=whole(result.version))
    assert hit.kinds == {CitationKind.QUOTE}

    # Another document can cite it, and the trace ends there, at a root.
    summary = "Repair and deduct is warranted now."
    later = bond.write_document(
        "letter",
        summary.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, len(summary)), [Ref(result.version, own)]),
    ],
        format=Format.MARKDOWN,
    )
    trace = bond.trace(Ref(later.version, TextLocator(0, len(summary))))
    [edge] = trace.edges_at(trace.root)
    [node] = edge.citation.supports
    [root] = trace.edges_at(node)
    assert root.citation.kind is CitationKind.SOURCE and root.citation.supports == []


def test_source_validator(bond, case):
    content = "In my judgment, repair and deduct is warranted now."
    claim = TextLocator(0, len(content))
    [support] = [h.ref for h in supported(bond, "Water leaking through the bathroom ceiling for 8 weeks.")]

    def problems(c):
        with pytest.raises(WriteError) as exc:
            bond.write_document("s", content.encode(), [c], format=Format.MARKDOWN)
        return [e.message for e in exc.value.errors]

    assert bond.write_document("s", content.encode(), [Citation(CitationKind.SOURCE, claim, [])],
                               format=Format.MARKDOWN).advanced
    assert any("takes no supports" in m for m in problems(Citation(CitationKind.SOURCE, claim, [support])))
    # A whole-version source is exactly what ingest writes.
    whole = bond.write_document("n", b"A note.", [Citation(CitationKind.SOURCE, VersionLocator(), [])], format=Format.MARKDOWN)
    assert bond.ingest("n", b"A note.").version == whole.version
    for attr in ["reason", "author"]:  # who stands behind a source is a signer's business, not an attr's
        assert any(f"does not accept attrs ['{attr}']" in m for m in problems(
            Citation(CitationKind.SOURCE, claim, [], {attr: "Dr. Reviewer"})))


def test_a_source_can_cite_the_parts_it_got_from_elsewhere(bond, case):
    # A record that is primary throughout, except a forwarded line it quotes.
    forwarded = "Water leaking through the bathroom ceiling for 8 weeks."
    content = f"Intake note: tenant seen today.\n\n{forwarded}\n"
    f0 = content.index(forwarded)
    result = bond.write_document(
        "record",
        content.encode(),
        [
        Citation(CitationKind.SOURCE, VersionLocator(), []),
        quote(bond, TextLocator(f0, f0 + len(forwarded)), case["fax"], forwarded),
    ],
        format=Format.MARKDOWN,
    )
    [own] = bond.search("tenant seen today", within=whole(result.version))
    [quoted] = bond.search("through the bathroom ceiling", within=whole(result.version))
    assert own.kinds == {CitationKind.SOURCE} and quoted.kinds == {CitationKind.SOURCE, CitationKind.QUOTE}
    trace = bond.trace(quoted.ref)
    assert sorted(e.citation.kind.value for e in trace.edges_at(trace.root)) == ["quote", "source"]


# --- checked writes, formats and titles ------------------------------------------------


def test_a_checked_write_stores_nothing(bond, case):
    citations = build_citations(bond, case)
    before = details.versions(bond)
    assert bond.check_write("summary_case_001", SUMMARY, citations) == []  # nothing would stop it
    assert bond.documents() == [d for d in bond.documents() if d.name != "summary_case_001"]
    assert details.versions(bond) == before
    assert write_summary(bond, case, citations=citations).advanced


def test_a_checked_write_reports_filing_problems_too(bond, case):
    first = write_summary(bond, case)
    edited = SUMMARY.replace("No significant damage to other rooms.", "No damage to other rooms.")
    codes = [i.code for i in bond.check_write("summary_case_001", edited, [])]  # no base, but the document exists
    assert E.DOCUMENT_EXISTS in codes and E.UNCOVERED_CLAIM in codes
    note = bond.write_document("note", "A note.", [Citation(CitationKind.SOURCE, VersionLocator(), [])])
    newer = bond.write_document("note", "A newer note.", [Citation(CitationKind.SOURCE, VersionLocator(), [])],
                                base=note.version)
    # A base the document has moved past.
    [stale] = bond.check_write("note", "Another.", [Citation(CitationKind.SOURCE, VersionLocator(), [])],
                               base=note.version)
    assert stale.code is E.STALE_BASE
    assert bond.document("note").head == newer.version
    assert bond.document("summary_case_001").head == first.version


def test_the_format_is_sniffed_and_text_may_be_a_str(bond, case):
    source = [Citation(CitationKind.SOURCE, VersionLocator(), [])]
    md = bond.write_document("md", "Plain text, not JSON.", source)
    js = bond.write_document("js", '{"a": "b"}', source)
    assert details.version(bond, md.version).format is Format.MARKDOWN
    assert details.version(bond, js.version).format is Format.JSON
    # A str is stored as its UTF-8 bytes: the same version as the bytes.
    assert bond.write_document("md2", b"Plain text, not JSON.", source).version == md.version


def test_a_write_can_title_its_document(bond, case):
    source = [Citation(CitationKind.SOURCE, VersionLocator(), [])]
    written = bond.write_document("note", "A note.", source, title="A note for people")
    assert bond.document("note").title == "A note for people"
    # Without a title, the next write leaves it as it is; a title is a label, so ids don't change.
    again = bond.write_document("note", "A note, revised.", source, base=written.version)
    assert bond.document("note").title == "A note for people"
    assert again.version == bond.write_document("other", "A note, revised.", source).version
    with pytest.raises(WriteError) as exc:
        bond.write_document("note", "A note, revised.", source, base=again.version, title="  ")
    assert exc.value.codes == [E.INVALID_NAME] and bond.document("note").title == "A note for people"
