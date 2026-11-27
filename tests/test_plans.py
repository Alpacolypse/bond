"""Write plans: a write decided by plan_write (and plan_ingest), done by commit, in memory. See bond.plans."""

from __future__ import annotations

from dataclasses import replace

import pytest

from bond import (
    Bond,
    BondError,
    Citation,
    CitationKind,
    Format,
    IssueCode,
    Ref,
    TextLocator,
    VersionLocator,
    WriteError,
)
from bond.plans import WritePlan
from bond.policies import ALLOW_GAPS

TEXT = "A note."
WHOLE = [Citation(CitationKind.SOURCE, VersionLocator(), [])]


def test_planning_stores_nothing_and_commit_writes(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    assert plan.version is not None
    assert bond.documents() == [] and bond.records.version(plan.version) is None
    result = bond.commit(plan)
    assert result.advanced and result.version == plan.version and bond.document("note").head == plan.version


def test_a_plan_is_refused_with_every_issue_a_write_would_have(bond):
    with pytest.raises(WriteError) as planned:
        bond.plan_write("note", "Uncovered words.", [], format=Format.MARKDOWN)
    with pytest.raises(WriteError) as written:
        bond.write_document("note", "Uncovered words.", [], format=Format.MARKDOWN)
    assert planned.value.codes == written.value.codes == [IssueCode.UNCOVERED_CLAIM]


def test_committing_twice_is_harmless(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    assert bond.commit(plan).advanced
    assert not bond.commit(plan).advanced and len(bond.history("note")) == 1


def test_a_plan_whose_document_moved_is_refused_and_files_nothing(bond):
    first = bond.write_document("note", TEXT, WHOLE, format=Format.MARKDOWN)
    late = bond.plan_write("note", "Mine.", WHOLE, format=Format.MARKDOWN, base=first.version)
    bond.write_document("note", "Theirs.", WHOLE, format=Format.MARKDOWN, base=first.version)
    with pytest.raises(WriteError) as e:
        bond.commit(late)
    assert e.value.codes == [IssueCode.STALE_BASE]
    assert late.version is not None and bond.records.version(late.version) is None


def test_a_plan_commits_from_another_bond_of_its_workspace(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN, title="A note")
    other = Bond(bond.store, bond.blobs, bond.workspace.name)
    assert other.commit(plan).advanced
    assert bond.document("note").head == plan.version and bond.document("note").title == "A note"


def test_only_a_plan_bonds_checks_made_is_committed(bond):
    """commit doesn't check a plan again, so a plan made by hand, which no check ever saw, is refused."""
    plan = bond.plan_write("note", "Left uncovered.", [], format=Format.MARKDOWN, policy=ALLOW_GAPS)
    forged = WritePlan(plan.workspace, plan.new, plan.filing)
    with pytest.raises(ValueError, match="wasn't made by Bond's checks"):
        bond.commit(forged)
    assert bond.documents() == []


def test_a_changed_plan_that_doesnt_hash_to_its_ids_is_refused(bond):
    source = bond.ingest("src", b"Six weeks of notice.", format=Format.MARKDOWN).version
    cited = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [bond.search("Six weeks", documents=["src"])[0].ref])]
    plan = bond.plan_write("note", "Six weeks", cited, format=Format.MARKDOWN)
    assert plan.new is not None
    paraphrased = [replace(cited[0], kind=CitationKind.PARAPHRASE)]  # what the id doesn't commit to
    with pytest.raises(BondError, match="doesn't hash to its id"):
        bond.commit(replace(plan, new=replace(plan.new, citations=paraphrased)))
    with pytest.raises(BondError, match="but holds"):
        bond.commit(replace(plan, filing=replace(plan.filing, version=source)))
    assert [d.name for d in bond.documents()] == ["src"]


def test_a_plan_commits_only_in_its_workspace(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    elsewhere = Bond(bond.store, bond.blobs, "elsewhere")
    with pytest.raises(ValueError, match="another workspace"):
        elsewhere.commit(plan)


def test_ingesting_what_is_stored_plans_only_its_filing(bond):
    v = bond.ingest("a", b"Same bytes.", format=Format.MARKDOWN).version
    plan = bond.plan_ingest("b", b"Same bytes.", format=Format.MARKDOWN)
    assert plan.new is None and plan.version == v
    assert bond.commit(plan).advanced and [d.name for d in bond.documents()] == ["a", "b"]


def test_a_plan_may_cite_a_linked_workspace(bond):
    policies = Bond(bond.store, bond.blobs, "policies")
    text = b"Six weeks of written notice."
    policy = policies.ingest("policy", text, format=Format.MARKDOWN).version
    bond.set_links([policies])
    quoted = [Citation(CitationKind.QUOTE, TextLocator(0, len(text)), [Ref(policy, TextLocator(0, len(text)))])]
    assert bond.commit(bond.plan_write("summary", text, quoted, format=Format.MARKDOWN)).advanced
    assert bond.records.version(policy) is None  # cited where it is, not copied


def test_committing_a_plan_again_keeps_a_title_given_since(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN, title="A")
    bond.commit(plan)
    bond.filing.retitle("note", "B")
    assert not bond.commit(plan).advanced
    assert bond.document("note").title == "B"


def test_committing_a_plan_again_titles_it_if_its_title_never_landed(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN, title="A")
    bond.commit(replace(plan, filing=replace(plan.filing, title=None)))  # as if it stopped before titling
    assert bond.commit(plan).advanced is False and bond.document("note").title == "A"


def test_relabelling_the_head_applies_its_title(bond):
    v = bond.write_document("note", TEXT, WHOLE, format=Format.MARKDOWN, title="A").version
    bond.filing.retitle("note", "B")
    bond.write_document("note", TEXT, WHOLE, format=Format.MARKDOWN, base=v, title="C")
    assert bond.document("note").title == "C"
