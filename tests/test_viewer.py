from collections import Counter

import pytest

from bond import (
    ALLOW_GAPS,
    Citation,
    CitationKind,
    Format,
    JsonLocator,
    Ref,
    TextLocator,
    VersionLocator,
    details,
)
from bond.http import viewer
from case_001 import SUMMARY, build_citations, claim_locator


@pytest.fixture
def summary(bond, case) -> str:
    return bond.write_document("summary", SUMMARY.encode(), build_citations(bond, case), format=Format.MARKDOWN).version


def test_annotations(bond, case, summary):
    units = viewer.claim_annotations(bond, summary)["units"]
    assert len(units) == 13 and all(u["locator"]["type"] == "text" for u in units)
    assert all(u["kinds"] and "source" not in u["kinds"] for u in units)
    assert units[2]["text"] == "Findings" and units[2]["kinds"] == ["unsourced"]
    assert units[1]["text"] == "Request: repair and deduct for a bathroom ceiling leak (HC 12.4), referred by Riverside Tenants Union."
    assert all(u["kinds"] == ["source"] for u in viewer.claim_annotations(bond, case["policy"])["units"])
    assert viewer.claim_annotations(bond, case["pdf"]) == {"units": [], "gaps": []}
    assert viewer.claim_annotations(bond, summary)["gaps"] == []


def test_annotations_report_exact_gaps(bond, case):
    first = "Water leaking through the bathroom ceiling for 8 weeks."
    [hit] = [h for h in bond.search("Water leaking", within=Ref(case["fax"], VersionLocator())) if h.kinds == {CitationKind.TRANSCRIPTION}]
    v = bond.write_document(
        "thin",
        first.encode(),
        [
        Citation(CitationKind.QUOTE, TextLocator(0, 13), [hit.ref]),
    ],
        format=Format.MARKDOWN,
        policy=ALLOW_GAPS,
    ).version
    [unit] = viewer.claim_annotations(bond, v)["units"]
    assert unit["kinds"] == ["quote"]  # the unit is partly cited...
    # ...and the rest of it is a gap, which the viewer marks as uncited.
    assert viewer.claim_annotations(bond, v)["gaps"] == [{"type": "text", "start": 14, "end": len(first)}]
    fax = {u["locator"]["pointer"]: u["kinds"] for u in viewer.claim_annotations(bond, case["fax"])["units"]}
    assert fax["/job_id"] == ["unsourced"]
    assert fax["/result/chunks/0/blocks/0/content"] == ["transcription"]


def test_trace_nodes_carry_display_data(bond, case):
    content = "One. Two."
    one = [h for h in bond.search("Active drip from the bathroom ceiling light fixture.", within=Ref(case["fax"], VersionLocator())) if h.kinds == {CitationKind.TRANSCRIPTION}][0].ref
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
    g = viewer.trace(bond, Ref(v, VersionLocator()))
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids)) and g["root"] == ids[0]
    [a, b] = [e for e in g["edges"] if e["from"] == g["root"]]
    assert a["to"] == b["to"]
    by_id = {n["id"]: n for n in g["nodes"]}
    fax_node = by_id[a["to"][0]]
    assert fax_node["name"] == "fax_case_001.reducto" and fax_node["format"] == "json" and fax_node["text"]
    assert fax_node["kinds"] == ["transcription"]
    [transcription] = [e for e in g["edges"] if e["from"] == fax_node["id"]]
    [page] = transcription["to"]
    assert by_id[page]["format"] == "pdf" and by_id[page]["text"] is None and by_id[page]["kinds"] == ["source"]


def test_trace_of_a_confidence_reaches_the_transcribed_text(bond, case):
    block = "/result/chunks/0/blocks/0"
    g = viewer.trace(bond, details.locate_pointer(bond, case["fax"], f"{block}/confidence"), max_depth=1)
    [judged] = [n for n in g["nodes"] if n["locator"].get("pointer") == f"{block}/content"]
    assert judged["kinds"] == ["transcription"] and judged["text"] == "FAX COVER SHEET"


def test_graph(bond, case, summary):
    g = viewer.graph(bond)
    nodes = {n["version"]: n for n in g["nodes"]}
    assert list(nodes) == [v.id for v in details.versions(bond)]
    assert nodes[case["pdf"]]["is_source"] and nodes[case["policy"]]["is_source"]
    assert not nodes[case["fax"]]["is_source"] and not nodes[summary]["is_source"]
    assert nodes[summary]["latest"] and nodes[summary]["name"] == "summary"
    fax_citations = bond.citations(case["fax"])
    fax = nodes[case["fax"]]
    assert fax["citation_count"] == len(fax_citations)
    # Assessments rest on the OCR output's own transcribed text.
    assert fax["self_citation_count"] == sum(c.kind is CitationKind.ASSESSMENT for c in fax_citations)

    edges = {(e["from"], e["to"]): e for e in g["edges"]}
    assert all(a != b for a, b in edges) and all(e["id"] == f"{e['from']}->{e['to']}" for e in g["edges"])
    ocr_to_pdf = edges[(case["fax"], case["pdf"])]
    assert ocr_to_pdf["kinds"] == dict(Counter(
        c.kind.value for c in fax_citations if any(s.version == case["pdf"] for s in c.supports)))
    assert ocr_to_pdf["count"] == sum(ocr_to_pdf["kinds"].values())
    assert (summary, case["fax"]) in edges and (summary, case["policy"]) in edges


def test_graph_counts_a_citation_once_per_cited_version(bond, case):
    claim = claim_locator(SUMMARY, "No significant damage to other rooms.")
    [hit] = [h for h in bond.search("No significant damage to other rooms.", within=Ref(case["fax"], VersionLocator())) if h.kinds == {CitationKind.TRANSCRIPTION}]
    [policy] = bond.search("6 weeks of written notice", within=Ref(case["policy"], VersionLocator()))
    content = SUMMARY[claim.start:claim.end]
    v = bond.write_document(
        "two",
        content.encode(),
        [
        Citation(CitationKind.PARAPHRASE, TextLocator(0, len(content)), [hit.ref, hit.ref, policy.ref]),
    ],
        format=Format.MARKDOWN,
    ).version
    edges = {(e["from"], e["to"]): e for e in viewer.graph(bond)["edges"]}
    assert edges[(v, case["fax"])]["count"] == 1 and edges[(v, case["policy"])]["count"] == 1


def test_summaries_follow_the_home_documents_history(bond):
    v1 = bond.ingest("note", b"One.").version
    v2 = bond.ingest("note", b"Two.", base=v1).version
    assert bond.ingest("note", b"One.", base=v2).version == v1  # a revert: the same bytes are the same version
    bond.ingest("elsewhere", b"Two.")  # the same version, filed in a second document
    s = viewer.Summaries(bond)
    one, two = s.summary(details.version(bond, v1)), s.summary(details.version(bond, v2))
    # Each version counts once, at its latest entry: after the revert, v1 is last and latest.
    assert (one["version_index"], one["version_count"], one["latest"]) == (2, 2, True)
    assert (two["version_index"], two["latest"]) == (1, False)
    assert (two["name"], two["documents"]) == ("note", ["note", "elsewhere"])
    assert [x["version"] for x in s.siblings(v1)] == [v2, v1]


def test_a_hub_doesnt_fill_a_neighborhood_with_its_other_citers(bond):
    text = "Six weeks of written notice."
    policy = bond.ingest("policy", text.encode(), format=Format.MARKDOWN).version
    summaries = []
    for i in range(40):
        content = f"{text} Case {i}."
        summaries.append(bond.write_document(
            f"summary/{i}",
            content.encode(),
            [
            Citation(CitationKind.QUOTE, TextLocator(0, len(text)), [Ref(policy, TextLocator(0, len(text)))]),
            Citation(CitationKind.UNSOURCED, TextLocator(len(text) + 1, len(content)), []),
        ],
            format=Format.MARKDOWN,
        ).version)
    # From one summary, two hops out: the policy, and at most fanout of the policy's other citers.
    near = viewer.neighborhood(bond, summaries[0], depth=2, fanout=10)
    ids = [n["version"] for n in near["nodes"]]
    assert ids[:2] == [summaries[0], policy] and len(ids) == 12 and near["truncated"]
    # From the hub itself, everything next to it is shown, up to the limit.
    hub = viewer.neighborhood(bond, policy, depth=1, fanout=10)
    assert len(hub["nodes"]) == 41 and not hub["truncated"]
    capped = viewer.neighborhood(bond, policy, depth=1, limit=20)
    assert len(capped["nodes"]) == 20 and capped["truncated"]
