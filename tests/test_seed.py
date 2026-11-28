from __future__ import annotations

from collections import Counter
from contextlib import ExitStack

import pytest
import seed

from bond import ALLOW_GAPS, Bond, CitationKind, Format, PdfLocator, Ref, VersionLocator, details
from bond.local import Home
from bond.locators import CsvLocator


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("seed")
    seed.build(home)
    return home


@pytest.fixture(scope="module")
def seeded(root):
    with ExitStack() as stack:
        policies = stack.enter_context(Bond.local(root / "workspaces" / "policies", create=False))
        cases = {name: stack.enter_context(Bond.local(root / "workspaces" / name, create=False, links=[policies]))
                 for name in seed.STORES if name != "policies"}
        yield {**cases, "policies": policies}


def case_data(seeded, case="001"):
    bond = seeded[f"case-{case}"]
    docs = {"policy_112": seeded["policies"].document("policy_ip112").head}
    for doc in bond.documents():
        if doc.name.endswith(".reducto"):
            docs[f"fax_{case}"] = doc.head
        elif doc.name == f"fax_case_{case}":
            docs[f"pdf_{case}"] = doc.head
        elif "_case_" in doc.name:
            docs[doc.name.replace("_case_", "_")] = doc.head
    return bond, docs


@pytest.mark.parametrize("case, count", [("001", 7), ("003", 6)])
def test_store_shape(seeded, case, count):
    bond, docs = case_data(seeded, case)
    versions = {details.version(bond, v) for v in docs.values()}
    assert len(versions) == count
    kinds = Counter(c.kind for version in versions for c in bond.citations(version.id))
    assert set(kinds) == set(CitationKind)
    # One version per document, and one document per version, for a legible demo.
    assert all(len(bond.history(d.name)) == 1 for d in bond.documents())
    assert all(len(details.filed_in(bond, version.id)) == 1 for version in versions)


def gaps(bond, version):
    """The undeclared spans of a whole version: what a policy that allows gaps left uncovered."""
    return list(bond.declarations(Ref(version, VersionLocator())).undeclared)


def test_draft_has_a_gap(seeded):
    bond, docs = case_data(seeded, "003")
    [gap] = gaps(bond, docs["reconsideration_003"])
    assert bond.text(gap) == "Recommend accepting the case."
    # The rollup is left out here: see test_a_range_over_a_whole_cell_declares_it.
    assert all(gaps(bond, v) == [] for k, v in docs.items()
               if k not in ("reconsideration_003", "expense_rollup_001") and details.version(bond, v).format is not Format.PDF)


def test_self_supports_and_mixed_sources(seeded):
    bond, docs = case_data(seeded, "003")
    review = bond.citations(docs["review_003"])
    assert any(s.version == docs["review_003"] for c in review for s in c.supports)
    determination = bond.citations(docs["determination_003"])
    assert {CitationKind.SOURCE, CitationKind.UNSOURCED, CitationKind.QUOTE, CitationKind.PARAPHRASE} <= {
        c.kind for c in determination}
    # The reconsideration review records the reviewer's own finding, and quotes the decision it answers.
    [own] = bond.search("follow-up call", within=Ref(docs["reconsideration_003"], VersionLocator()))
    [quoted] = bond.search("the city inspection is still pending", within=Ref(docs["reconsideration_003"], VersionLocator()))
    assert own.kinds == {CitationKind.SOURCE} and CitationKind.QUOTE in quoted.kinds


def test_the_reconsideration_traces_through_the_review_to_the_page(seeded):
    bond, docs = case_data(seeded, "003")
    [hit] = bond.search("is worsening", within=Ref(docs["reconsideration_003"], VersionLocator()))
    pages = [n for n in bond.trace(hit.ref).nodes if isinstance(n.locator, PdfLocator)]
    assert pages and {d.name for p in pages for d in details.filed_in(bond, p.version)} == {"fax_case_003"}


@pytest.mark.parametrize("case", ["001", "003"])
def test_each_case_is_a_connected_graph(seeded, case):
    """Connected through what it cites in the policies it links to, too."""
    bond, _ = case_data(seeded, case)
    graph: dict[str, set[str]] = {v.id: set() for v in details.versions(bond)}
    graph |= {v.id: set() for v in details.versions(seeded["policies"])}
    for v in graph:
        for c in bond.citations(v):
            for s in c.supports:
                if s.version != v:
                    graph[v].add(s.version)
                    graph[s.version].add(v)
    start = next(iter(graph))
    seen, stack = {start}, [start]
    while stack:
        for n in graph[stack.pop()] - seen:
            seen.add(n)
            stack.append(n)
    assert seen == set(graph)


def test_the_expense_history_cites_a_csv_by_cell_row_and_column(seeded):
    bond, docs = case_data(seeded)
    assert details.version(bond, docs["expenses_001"]).format is Format.CSV
    trace = bond.trace(Ref(docs["expense_history_001"], VersionLocator()))
    reached = [n.locator for n in trace.nodes if n.version == docs["expenses_001"]]
    assert all(isinstance(loc, CsvLocator) for loc in reached)
    assert {"cell", "range", "row", "column"} == {
        "range" if loc.span else "cell" if loc.is_cell else "row" if loc.row is not None else "column"
        for loc in reached if isinstance(loc, CsvLocator)}
    assert gaps(bond, docs["expense_history_001"]) == []


def test_the_claims_rollup_is_a_table_cited_at_every_granularity(seeded):
    bond, docs = case_data(seeded)
    rollup = docs["expense_rollup_001"]
    claims = [c.claim for c in bond.citations(rollup) if isinstance(c.claim, CsvLocator)]
    assert CsvLocator(row=0) in claims and CsvLocator(column=0) in claims
    assert any(c.is_cell and c.span is None for c in claims) and any(c.span for c in claims)
    # The total rests on cells of the same table.
    [total] = [c for c in bond.citations(rollup) if c.claim == CsvLocator(4, 3)]
    assert {s.version for s in total.supports} == {rollup}


def test_a_range_over_a_whole_cell_declares_it(seeded):
    bond, docs = case_data(seeded)
    assert gaps(bond, docs["expense_rollup_001"]) == []


def test_cli_refuses_to_clobber(tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="is not a demo Bond home"):
        seed.main(["--home", str(other), "--force"])
    assert (other / "notes.txt").exists()

    store = tmp_path / "store"
    assert seed.main(["--home", str(store)]) == 0
    with pytest.raises(SystemExit, match="is not empty; pass --force to replace it"):
        seed.main(["--home", str(store)])
    assert seed.main(["--home", str(store), "--force"]) == 0
    assert "7 versions" in capsys.readouterr().out


def test_clients_are_isolated_and_only_the_policy_is_shared(seeded, root):
    a, b, policies = (seeded[name] for name in seed.STORES)
    assert a.search("Jordan Avery") and not a.search("Samuel Okafor")
    assert b.search("Samuel Okafor") and not b.search("Jordan Avery")
    assert [Home(root).links(c) for c in ("case-001", "case-003")] == [["policies"], ["policies"]]
    assert not {v.id for v in details.versions(a)} & {v.id for v in details.versions(b)}  # nothing copied
    policy = policies.document("policy_ip112").head
    assert len(policies.documents()) == 1
    for bond in (a, b):
        assert bond.find("policy_ip112") is None and details.owner(bond, policy) is policies
        assert any(policy in {s.version for c in bond.citations(d.head) for s in c.supports} for d in bond.documents())
        assert all(not bond.verify(doc.head, policy=ALLOW_GAPS) for doc in bond.documents())
