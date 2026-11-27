import pytest

from bond import (
    Bond,
    CitationKind,
    Format,
    JsonLocator,
    PdfLocator,
    Ref,
    TextLocator,
    VersionLocator,
    details,
)
from case_001 import SUMMARY, build_citations, claim_locator


@pytest.fixture
def summary(bond, case) -> str:
    return bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN).version


def test_all_versions_and_blob(bond, case, summary):
    assert [v.id for v in details.versions(bond)] == [case["pdf"], case["fax"], case["policy"], summary]
    assert bond.blob(summary) == SUMMARY.encode()
    assert bond.blob(case["pdf"]).startswith(b"%PDF-")


def test_claim_units(bond, case, summary):
    units = details.claim_units(bond, summary)
    assert len(units) == 13 and all(u.version == summary for u in units)  # headings are claims too
    assert bond.text(units[0]) == "Intake summary: Jordan Avery"
    assert bond.text(units[1]) == (
        "Request: repair and deduct for a bathroom ceiling leak (HC 12.4), referred by Riverside Tenants Union.")
    assert details.claim_units(bond, case["pdf"]) == []
    assert Ref(case["fax"], bond.citations(case["fax"])[0].claim) in details.claim_units(bond, case["fax"])


def test_citations_are_listed_in_reading_order(bond, case, summary):
    starts = [c.claim.start for c in bond.citations(summary)]
    assert starts == sorted(starts)
    # A JSON document's, in the order its values appear.
    pointers = [details.pointer(bond, Ref(case["fax"], c.claim)) for c in bond.citations(case["fax"])]
    blocks = [p for p in pointers if "/blocks/" in p and p.endswith("/content")]
    assert blocks[:3] == [f"/result/chunks/0/blocks/{i}/content" for i in range(3)]


def test_cited_by_is_the_reverse_of_a_hop(bond, case, summary):
    item = claim_locator(SUMMARY, "No significant damage to other rooms.")
    trace = bond.trace(Ref(summary, item))
    [edge] = trace.edges_at(trace.root)
    [hop] = edge.citation.supports
    # Who cites the fax value the summary quotes? The summary's quote, and the fax's own confidence in it.
    cited = bond.cited_by(hop)
    assert [c.id for c in cited if c.owner_version == summary] == [edge.citation.id]
    assert [c.kind for c in cited if c.owner_version == case["fax"]] == [CitationKind.ASSESSMENT]

    everything = bond.cited_by(Ref(case["fax"], VersionLocator()))
    assert {c.owner_version for c in everything} == {summary, case["fax"]}  # the fax's assessments cite itself
    pdf = bond.cited_by(Ref(case["pdf"], VersionLocator()))
    assert pdf and all(c.owner_version == case["fax"] for c in pdf)
    # A region picks up only the citations of regions overlapping it.
    page2 = bond.cited_by(Ref(case["pdf"], PdfLocator(1, (0, 0, 1, 1))))
    assert 0 < len(page2) < len(pdf)
    assert all(s.locator.page == 1 for c in page2 for s in c.supports)


def test_cited_by_transcribed_text_finds_its_assessment(bond, case):
    citations = bond.citations(case["fax"])
    transcription = next(c for c in citations if c.kind is CitationKind.TRANSCRIPTION)
    [assessment] = [c for c in bond.cited_by(Ref(case["fax"], transcription.claim)) if c.owner_version == case["fax"]]
    assert assessment.kind is CitationKind.ASSESSMENT
    with pytest.raises(ValueError, match=r"json path \[99\] does not exist in the document"):
        bond.cited_by(Ref(case["fax"], JsonLocator((99,))))
    with pytest.raises(ValueError, match="text locator is not valid in a json document"):
        bond.cited_by(Ref(case["fax"], TextLocator(0, 1)))


def test_a_small_parsed_cache_still_reads_every_version(bond, case, summary):
    reader = Bond(bond.store, bond.blobs, bond.workspace.name, parsed_cache_size=1)  # shares the store and blobs
    for v in (case["pdf"], case["fax"], summary, case["fax"]):
        assert details.claim_units(reader, v) == details.claim_units(bond, v)
