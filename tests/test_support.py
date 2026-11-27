"""Recursive support is complete declared ancestry, not semantic entailment or an integrity audit."""

import pytest

from bond import (
    ALLOW_GAPS,
    Bond,
    Citation,
    Format,
    JsonLocator,
    PdfLocator,
    Ref,
    SelfRef,
    TextLocator,
    VersionId,
    VersionLocator,
    details,
)
from bond import (
    CitationKind as K,
)
from bond import (
    SupportStatus as S,
)
from bond.authoring import ToolError, Tools
from bond.locators import CsvLocator
from bond.models import SupportReason as R
from conftest import FIXTURES, rewrite_record, store_unchecked


def write(bond, name, text, citations):
    return bond.write_document(name, text.encode(), citations, format=Format.MARKDOWN, policy=ALLOW_GAPS).version


def source(bond, text="source"):
    return Ref(bond.ingest(text, text.encode()).version, TextLocator(0, len(text)))


def rests_on(bond: Bond, citing: Ref, *cited: Ref) -> None:
    """Rewrite the records of citing's version, whose one citation is a source, so that it is a paraphrase resting on
    cited: what no write can store, since a version's id commits to what it rests on, but another code path writing
    rows could. Bond reads such records as they are."""
    def change(record):
        [c] = record["citations"]
        c["kind"] = "paraphrase"
        c["supports"] += [[ref.version, ref.locator.to_dict()] for ref in cited]

    rewrite_record(bond, citing.version, change)


def derived(bond, name, supports):
    return Ref(write(bond, name, name, [Citation(K.PARAPHRASE, TextLocator(0, len(name)), supports)]),
               TextLocator(0, len(name)))


def test_recursive_region_results_and_issue_paths(bond):
    root = source(bond)
    mixed = write(bond, "mixed", "good bad gap", [
        Citation(K.PARAPHRASE, TextLocator(0, 4), [root]),
        Citation(K.UNSOURCED, TextLocator(5, 8), []),
    ])
    ref = Ref(mixed, VersionLocator())
    result = bond.check_support(ref)
    assert result.status is S.PARTIALLY_SUPPORTED
    assert [(bond.text(r.ref), r.status) for r in result.regions] == [
        ("good", S.SUPPORTED), ("bad", S.UNSUPPORTED), ("gap", S.UNSUPPORTED),
    ]
    assert {i.reason for i in result.issues} == {R.UNSOURCED, R.UNDECLARED}
    assert all(i.path[0] == ref and i.path[-1] == i.ref for i in result.issues)
    top = derived(bond, "top", [Ref(mixed, TextLocator(0, 12))])
    checked = bond.check_support(top)
    assert checked.status is S.UNSUPPORTED  # no invented word-level attribution from a mixed dependency
    assert all(i.path[:2] == (top, Ref(mixed, TextLocator(0, 12))) for i in checked.issues)


def test_every_support_must_pass_even_when_another_is_a_source(bond):
    good = source(bond)
    bad = Ref(write(bond, "bad", "bad", [Citation(K.UNSOURCED, TextLocator(0, 3), [])]), TextLocator(0, 3))
    top = derived(bond, "top", [good, bad])
    assert bond.check_support(top).status is S.UNSUPPORTED
    [hit] = Tools(bond).search("top")
    assert hit.support is S.UNSUPPORTED


def test_source_does_not_hide_overlapping_unsourced_or_broken_declarations(bond):
    good = source(bond)
    bad = Ref(write(bond, "bad", "bad", []), TextLocator(0, 3))
    version = write(bond, "overlap", "alpha", [
        Citation(K.SOURCE, TextLocator(0, 5), []),
        Citation(K.UNSOURCED, TextLocator(0, 2), []),
        Citation(K.PARAPHRASE, TextLocator(2, 4), [good, bad]),
    ])
    r = bond.check_support(Ref(version, TextLocator(0, 5)))
    assert r.status is S.PARTIALLY_SUPPORTED
    assert [part.status for part in r.regions] == [S.UNSUPPORTED, S.UNSUPPORTED, S.SUPPORTED]
    assert len(bond.declarations(Ref(version, TextLocator(0, 5))).citations) == 3


def test_budget_unknown_keeps_established_regions_and_unreachable_is_not_unsupported(bond, tmp_path):
    with Bond.local(tmp_path / "external") as external:
        s = source(external)
        bond.set_links([external])
        v = write(bond, "mixed", "known missing bad", [
            Citation(K.SOURCE, TextLocator(0, 5), []),
            Citation(K.PARAPHRASE, TextLocator(6, 13), [s]),
            Citation(K.UNSOURCED, TextLocator(14, 17), []),
        ])
        bond.set_links([])  # the link is removed: what rests on external can't be followed
        ref = Ref(v, VersionLocator())
        r = bond.check_support(ref)
        assert r.status is S.UNKNOWN
        assert [part.status for part in r.regions] == [S.SUPPORTED, S.UNKNOWN, S.UNSUPPORTED]
        assert R.UNREACHABLE in {i.reason for i in r.issues}
        bond.set_links([external])
        assert bond.check_support(ref).status is S.PARTIALLY_SUPPORTED
        for kwargs, reason in [({"limit": 1}, R.LIMIT), ({"max_depth": 0}, R.DEPTH_LIMIT)]:
            r = bond.check_support(ref, **kwargs)
            assert r.status is S.UNKNOWN
            assert reason in {i.reason for i in r.issues}
        assert bond.check_support(ref, limit=2, max_depth=1).status is S.PARTIALLY_SUPPORTED


def test_shared_ancestry_uses_one_budget_slot_and_one_inspection(bond):
    base = source(bond)
    left = derived(bond, "left", [base])
    right = derived(bond, "right", [base])
    top = derived(bond, "top", [left, right])
    # Four references in all, base counted once though two paths reach it: the budget says so.
    assert bond.check_support(top, limit=4).status is S.SUPPORTED
    assert bond.check_support(top, limit=3).status is S.UNKNOWN


def test_shared_dependency_uses_shortest_path_for_depth_budget(bond):
    base = source(bond)
    middle = derived(bond, "middle", [base])
    top = derived(bond, "top", [middle, base])
    assert bond.check_support(top, max_depth=1, limit=3).status is S.SUPPORTED


@pytest.mark.parametrize("fmt,data,loc", [
    (Format.MARKDOWN, b"# Heading\n\n---\n", TextLocator(11, 14)),
    (Format.MARKDOWN, b"! word", TextLocator(0, 1)),
    (Format.JSON, b'{"x":null}', JsonLocator((0,))),
    (Format.JSON, b'{"x":""}', JsonLocator((0,))),
    (Format.JSON, b'{"x":"!"}', JsonLocator((0,))),
    (Format.JSON, b'{"x":[]}', JsonLocator((0,))),
    (Format.CSV, b"!,hello", CsvLocator(0, 0)),
    (Format.TSV, b"!\thello", CsvLocator(0, 0)),
])
def test_not_applicable_means_exempt_content_not_absence_of_citations(bond, fmt, data, loc):
    v = bond.write_document("doc", data, [], format=fmt, policy=ALLOW_GAPS).version
    r = bond.check_support(Ref(v, loc))
    assert r.status is S.NOT_APPLICABLE
    assert r.regions == r.issues == ()


def test_heading_still_needs_declaration_and_empty_support_does_not_prove_claim(bond):
    v = write(bond, "heading", "# Findings", [])
    assert bond.check_support(Ref(v, VersionLocator())).status is S.UNSUPPORTED
    empty = source(bond, "!")
    top = derived(bond, "assertion", [empty])
    r = bond.check_support(top)
    assert r.status is S.UNSUPPORTED and r.issues[0].reason is R.NO_REQUIRED_CONTENT


@pytest.mark.parametrize("fmt,data,whole,left,right", [
    (Format.JSON, b'{"text":"alpha beta"}', JsonLocator((0,)), JsonLocator((0,), (0, 5)), JsonLocator((0,), (6, 10))),
    (Format.CSV, b"alpha beta", CsvLocator(0, 0), CsvLocator(0, 0, (0, 5)), CsvLocator(0, 0, (6, 10))),
    (Format.TSV, b"alpha beta", CsvLocator(0, 0), CsvLocator(0, 0, (0, 5)), CsvLocator(0, 0, (6, 10))),
])
def test_partial_coverage_on_arbitrary_structured_spans(bond, fmt, data, whole, left, right):
    v = bond.write_document("mixed", data, [Citation(K.SOURCE, left, [])], format=fmt, policy=ALLOW_GAPS).version
    r = bond.check_support(Ref(v, whole))
    assert r.status is S.PARTIALLY_SUPPORTED
    assert [(p.ref.locator, p.status) for p in r.regions] == [(left, S.SUPPORTED), (right, S.UNSUPPORTED)]
    assert bond.check_support(Ref(v, left)).status is S.SUPPORTED
    assert bond.check_support(Ref(v, right)).status is S.UNSUPPORTED


def test_json_container_scalar_and_table_scope_results(bond):
    v = bond.write_document(
        "json",
        b'{"a":42,"b":false,"c":"yes"}',
        [
        Citation(K.SOURCE, JsonLocator((0,)), []),
    ],
        format=Format.JSON,
        policy=ALLOW_GAPS,
    ).version
    r = bond.check_support(Ref(v, JsonLocator()))
    assert r.status is S.PARTIALLY_SUPPORTED
    assert [p.status for p in r.regions] == [S.SUPPORTED, S.UNSUPPORTED, S.UNSUPPORTED]
    v = bond.write_document(
        "csv",
        b"a,b\nc,d",
        [
        Citation(K.SOURCE, CsvLocator(row=0), []),
    ],
        format=Format.CSV,
        policy=ALLOW_GAPS,
    ).version
    assert bond.check_support(Ref(v, CsvLocator(row=0))).status is S.SUPPORTED
    assert bond.check_support(Ref(v, CsvLocator(column=0))).status is S.PARTIALLY_SUPPORTED


def test_pdf_coverage_is_geometric_and_never_vacuously_supported(bond):
    data = next(FIXTURES.rglob("*.pdf")).read_bytes()
    left, right = PdfLocator(0, (0, 0, .5, 1)), PdfLocator(0, (.5, 0, 1, 1))
    v = bond.write_document("partial", data, [Citation(K.SOURCE, left, [])], format=Format.PDF).version
    whole = Ref(v, PdfLocator(0, (0, 0, 1, 1)))
    r = bond.check_support(whole)
    assert r.status is S.PARTIALLY_SUPPORTED
    assert bond.declarations(whole).undeclared == (Ref(v, right),)
    assert bond.check_support(Ref(v, left)).status is S.SUPPORTED
    empty = bond.write_document("empty", data, [], format=Format.PDF).version
    assert bond.check_support(Ref(empty, right)).status is S.UNSUPPORTED
    both = bond.write_document(
        "both",
        data,
        [
        Citation(K.SOURCE, left, []), Citation(K.SOURCE, right, []),
    ],
        format=Format.PDF,
    ).version
    assert bond.check_support(Ref(both, whole.locator)).status is S.SUPPORTED
    ingested = bond.ingest("source-pdf", data, format=Format.PDF).version
    assert bond.check_support(Ref(ingested, VersionLocator())).status is S.SUPPORTED


def test_cycle_does_not_loop_or_pass_and_invalid_ancestor_is_diagnosed(bond):
    base = source(bond)
    top = derived(bond, "top", [base])
    rests_on(bond, base, top)  # base now rests on top, which rests on base
    r = bond.check_support(top)
    assert r.status is S.UNSUPPORTED
    assert [i.reason for i in r.issues] == [R.CYCLE]


def test_an_invalid_ancestor_is_diagnosed(bond):
    base = source(bond)
    top = derived(bond, "top", [base])
    def past_the_end(record):  # of base's text
        for c in record["citations"]:
            c["supports"] = [[v, TextLocator(0, 1000).to_dict()] for v, _ in c["supports"]]

    rewrite_record(bond, top.version, past_the_end)
    r = bond.check_support(top)
    assert r.status is S.UNSUPPORTED
    assert [(i.reason, i.ref) for i in r.issues] == [(R.INVALID_REFERENCE, Ref(base.version, TextLocator(0, 1000)))]


def test_support_budgets_are_validated_and_agent_status_matches_core(bond):
    base = source(bond)
    top = derived(bond, "top", [base])
    for kwargs, problem in (({"limit": 0}, "limit must be at least 1"),
                            ({"max_depth": -1}, "max_depth must be non-negative")):
        with pytest.raises(ValueError, match=problem):
            bond.check_support(top, **kwargs)
    tools = Tools(bond)
    [hit] = tools.search("top")
    assert hit.support is S.SUPPORTED
    assert tools.trace(hit.handle).status is S.SUPPORTED is bond.check_support(top).status
    with pytest.raises(ToolError, match="limit must be at least 1"):
        tools.trace(hit.handle, limit=0)


def chain(length: int) -> list[Citation]:
    """Each character resting on the next, and the last a source: a chain of self-supports length long."""
    return [Citation(K.PARAPHRASE, TextLocator(i, i + 1), [SelfRef(TextLocator(i + 1, i + 2))]) for i in range(length - 1)] + [
        Citation(K.SOURCE, TextLocator(length - 1, length), [])]


def test_deep_chain_does_not_depend_on_python_recursion_limit(bond):
    length = 1050
    v = store_unchecked(bond, "x" * length, chain(length), "deep")
    ref = Ref(v, TextLocator(0, 1))
    assert bond.check_support(ref, limit=length).status is S.SUPPORTED
    assert bond.check_support(ref).status is S.UNKNOWN  # the default budget is 1000 references


def test_a_chain_deeper_than_the_stack_can_be_written(bond):
    length = 1050
    v = bond.write_document("deep", "x" * length, chain(length), format=Format.MARKDOWN).version
    assert len(bond.citations(v)) == length


def test_unknown_propagates_through_a_cycle_without_hiding_other_regions(bond):
    a, b = source(bond, "a"), source(bond, "b")
    missing = Ref(VersionId("f" * 64), TextLocator(0, 1))
    rests_on(bond, a, b)
    rests_on(bond, b, a, missing)
    r = bond.check_support(a)
    assert r.status is S.UNKNOWN
    assert {i.reason for i in r.issues} == {R.CYCLE, R.UNREACHABLE}


def test_support_is_recomputed_from_workspace_evidence_without_persisting_assessments(bond, tmp_path):
    def snapshot():
        versions = details.versions(bond)
        documents = bond.documents()
        return (versions, documents, [bond.citations(v.id) for v in versions],
                [bond.history(d.name) for d in documents])

    with (Bond.local(tmp_path / "external") as external, Bond.local(tmp_path / "library") as library,
          Bond.local(tmp_path / "other") as other):
        base = source(external)
        library.set_links([external])
        top = derived(library, "top", [base])
        library.set_links([])  # the library no longer links to what top rests on
        bond.set_links([library])
        other.set_links([library])
        before = snapshot()
        unknown = bond.check_support(top)
        assert unknown.status is S.UNKNOWN
        assert snapshot() == before
        assert set(unknown.to_dict()) == {"ref", "status", "regions", "issues"}
        assert set(unknown.issues[0].to_dict()) == {"ref", "reason", "path"}

        bond.set_links([library, external])  # bond can read what top rests on directly
        after_import = snapshot()
        supported = bond.check_support(top)
        assert supported.ref == unknown.ref and supported.status is S.SUPPORTED
        assert snapshot() == after_import
        assert unknown.status is S.UNKNOWN  # an earlier result remains a snapshot, not mutable version metadata
        assert other.check_support(top).status is S.UNKNOWN  # same version, different available dependencies
        assert bond.check_support(top, limit=1).status is S.UNKNOWN  # query budgets also affect the result
