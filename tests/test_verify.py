"""Checked writes and verify(): integrity always, a policy for the rest, at write time and offline alike."""

from bond import (
    ALLOW_GAPS,
    STRICT,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    Policy,
    Ref,
    SelfRef,
    TextLocator,
    VersionId,
    VersionLocator,
    details,
)
from bond.models import Issue, SupportStatus
from bond.storage.local import LocalBlobs
from case_001 import SUMMARY, build_citations
from conftest import rewrite_record, store_unchecked

FIRST = "Water leaking through the bathroom ceiling for 8 weeks."


def quote(bond, claim, version, phrase):
    [hit] = supported(bond, phrase, version)
    return Citation(CitationKind.QUOTE, claim, [hit.ref])


def supported(bond, phrase, version):
    """Matches in version whose support reaches declared sources (an OCR parse also holds unsourced copies)."""
    return [h for h in bond.search(phrase, within=Ref(version, VersionLocator()))
            if bond.check_support(h.ref).status is SupportStatus.SUPPORTED]


def check(bond, content: str, citations: list[Citation], policy: Policy = STRICT) -> list[Issue]:
    """The issues a write of a new document would have; nothing is stored."""
    return bond.check_write("checked", content, citations, format=Format.MARKDOWN, policy=policy)


def blob_path(bond, version: VersionId):
    """Where a version's bytes are, to damage them as a failing disk or a careless hand might."""
    assert isinstance(bond.blobs, LocalBlobs)
    return bond.blobs.path(details.version(bond, version).content_hash)


def codes(issues: list[Issue]) -> set[IssueCode]:
    return {i.code for i in issues}


# --- check ------------------------------------------------------------------------


def test_a_checked_write_finds_everything_and_stores_nothing(bond, case):
    content = f"{FIRST}\n\nLandlord ignored every notice.\n"
    citations = [quote(bond, TextLocator(0, len(FIRST)), case["fax"], FIRST)]
    before, documents = details.versions(bond), bond.documents()
    [gap] = check(bond, content, citations)
    assert gap.code is IssueCode.UNCOVERED_CLAIM and gap.version is None
    assert bond.check_write("draft", content, citations, format=Format.MARKDOWN, policy=ALLOW_GAPS) == []
    assert details.versions(bond) == before and bond.documents() == documents  # nothing was stored
    assert bond.write_document("draft", content, citations, format=Format.MARKDOWN, policy=ALLOW_GAPS).advanced


def test_a_policy_is_data_coverage_and_allowed_kinds(bond, case):
    content = f"{FIRST} Pending."
    cite = quote(bond, TextLocator(0, len(FIRST)), case["fax"], FIRST)
    pending = Citation(CitationKind.SOURCE, TextLocator(len(FIRST) + 1, len(content)), [])
    assert codes(check(bond, content, [cite])) == {IssueCode.UNCOVERED_CLAIM}
    assert check(bond, content, [cite], Policy(gaps=True)) == []
    no_sources = Policy(kinds=frozenset({CitationKind.QUOTE, CitationKind.UNSOURCED}))
    [refused] = check(bond, content, [cite, pending], no_sources)
    assert (refused.code, refused.citation_index) == (IssueCode.KIND_NOT_ALLOWED, 1)
    assert codes(check(bond, content, [cite], no_sources)) == {IssueCode.UNCOVERED_CLAIM}
    assert bond.write_document("mostly", content, [cite], format=Format.MARKDOWN, policy=Policy(gaps=True)).advanced


def test_a_policy_cannot_waive_integrity(bond, case):
    citations = [quote(bond, TextLocator(0, len(FIRST)), case["fax"], FIRST)]
    citations[0] = Citation(CitationKind.QUOTE, TextLocator(0, 999), citations[0].supports)
    assert codes(check(bond, FIRST, citations, ALLOW_GAPS)) == {IssueCode.BAD_LOCATOR}


# --- verify -----------------------------------------------------------------------


def set_kind(bond, version, citation, kind):
    """Change one stored citation's kind behind Bond's back."""

    def change(record):
        for c in record["citations"]:
            if c["id"] == citation:
                c["kind"] = kind

    rewrite_record(bond, version, change)


def test_verify_detects_tampering_anywhere_beneath(bond, case):
    summary = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN).version
    assert bond.verify(summary) == []
    # Rewriting one citation in the fax's OCR output, two hops down, breaks its id, and the
    # rewritten citation breaks its kind's rules too.
    first = bond.citations(case["fax"])[0]
    set_kind(bond, case["fax"], first.id, "paraphrase")
    issues = bond.verify(summary)
    assert codes(issues) == {IssueCode.ID_MISMATCH, IssueCode.INVALID_CITATION}
    assert {i.version for i in issues} == {case["fax"]}
    assert any(f"citation {first.id[:12]}… does not hash to its id" in i.message for i in issues)
    set_kind(bond, case["fax"], first.id, first.kind.value)
    assert bond.verify(summary) == []
    # So does changing a blob's bytes.
    path = blob_path(bond, case["policy"])
    path.write_bytes(path.read_bytes() + b"\nAmended.")
    [issue] = bond.verify(summary)
    assert (issue.code, issue.version) == (IssueCode.BLOB_MISMATCH, case["policy"])
    # And losing them.
    path.unlink()
    assert (IssueCode.BLOB_MISSING, case["policy"]) in {(i.code, i.version) for i in bond.verify(summary)}


def test_verify_rechecks_the_rules_behind_valid_hashes(bond, case):
    [hit] = supported(bond, "Active drip from the bathroom ceiling light fixture.", case["fax"])
    misquote = "No active drip."
    v = store_unchecked(bond, misquote, [Citation(CitationKind.QUOTE, TextLocator(0, len(misquote)), [hit.ref])],
                        "misquote")
    # Its hashes are intact; its quote is not.
    [issue] = bond.verify(v)
    assert (issue.code, issue.version, issue.citation_index) == (IssueCode.QUOTE_MISMATCH, v, 0)


def test_verify_applies_the_policy(bond, case):
    content = f"{FIRST}\n\nLandlord ignored every notice.\n"
    v = store_unchecked(bond, content, [quote(bond, TextLocator(0, len(FIRST)), case["fax"], FIRST)], "draft")
    [issue] = bond.verify(v)
    assert (issue.code, issue.version) == (IssueCode.UNCOVERED_CLAIM, v)
    assert bond.verify(v, ALLOW_GAPS) == []


def test_verify_applies_the_policy_to_every_version_beneath(bond, case):
    content = f"{FIRST}\n\nLandlord ignored every notice.\n"
    cite = quote(bond, TextLocator(0, len(FIRST)), case["fax"], FIRST)
    draft = bond.write_document("draft", content.encode(), [cite], format=Format.MARKDOWN, policy=ALLOW_GAPS).version
    # A summary that cites only the draft's cited part passes on its own...
    summary = "Leg pain for eight weeks."
    v = bond.write_document(
        "summary",
        summary.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, len(summary)), [Ref(draft, TextLocator(0, len(FIRST)))]),
    ],
        format=Format.MARKDOWN,
    ).version
    assert check(bond, summary, bond_citations(bond, v)) == []
    # ...but verify holds everything beneath it to the policy too.
    [issue] = bond.verify(v, STRICT)
    assert (issue.code, issue.version) == (IssueCode.UNCOVERED_CLAIM, draft)
    assert bond.verify(v, ALLOW_GAPS) == []


def test_every_written_version_verifies_under_the_policy_it_was_written_with(bond, case):
    content, p1 = f"{FIRST}\n\nLeg pain for two months.\n", TextLocator(0, len(FIRST))
    p2 = TextLocator(len(FIRST) + 2, len(content) - 1)
    v = bond.write_document(
        "self",
        content.encode(),
        [
        quote(bond, p1, case["fax"], FIRST),
        Citation(CitationKind.PARAPHRASE, p2, [SelfRef(p1)]),
    ],
        format=Format.MARKDOWN,
    ).version
    # Supports into the version itself are checked as they were written: as SELF.
    assert bond.verify(v) == []
    for written in details.versions(bond):
        assert bond.verify(written.id) == []


def test_verify_reports_a_cited_version_it_cant_read_without_failing(bond, case):
    summary = bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN).version
    # The policy's bytes are replaced by bytes that aren't text at all: the policy says so, and what rests on it is
    # taken as written, not checked against what can't be read.
    blob_path(bond, case["policy"]).write_bytes(b"\xff\xfe not text")
    assert {(i.code, i.version) for i in bond.verify(summary)} == {
        (IssueCode.BLOB_MISMATCH, case["policy"]), (IssueCode.INVALID_CONTENT, case["policy"])}


def test_a_lost_blob_beneath_hides_nothing_about_what_rests_on_it(bond, case):
    # A version stored with an uncovered claim, resting on the policy, whose blob is then lost.
    content = "Made up.\n\nNot cited at all.\n"
    v = store_unchecked(bond, content, [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, 8), [Ref(case["policy"], TextLocator(0, 5))])], "draft")
    blob_path(bond, case["policy"]).unlink()
    issues = bond.verify(v)
    assert {(i.code, i.version) for i in issues} == {
        (IssueCode.UNCOVERED_CLAIM, v), (IssueCode.BLOB_MISSING, case["policy"])}
    [uncovered] = [i for i in issues if i.version == v]
    assert uncovered.message == "no citation covers 'Not cited at all.'"


def bond_citations(bond, version: VersionId) -> list[Citation]:
    return [Citation(c.kind, c.claim, list(c.supports), c.attrs) for c in bond.citations(version)]
