"""Write plans: a write decided by plan_write (and plan_ingest, plan_import), done by commit. See bond.plans."""

from __future__ import annotations

import json

import pytest

from bond import Bond, BondError, Citation, CitationKind, Format, IssueCode, TextLocator, VersionLocator, WriteError
from bond.policies import ALLOW_GAPS
from bond.storage.local import LocalBlobs

TEXT = "A note."
WHOLE = [Citation(CitationKind.SOURCE, VersionLocator(), [])]


def test_planning_stores_nothing_and_commit_writes(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    assert plan.checked and plan.version is not None
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


def test_a_staged_plan_commits_from_another_bond(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN, title="A note")
    text = bond.stage(plan)
    assert TEXT not in text  # bytes go by hash, in the blobs
    other = Bond(bond.store, bond.blobs, bond.workspace.name)
    loaded = other.load_plan(text)
    assert not loaded.checked and loaded == plan
    assert other.commit(loaded).advanced
    assert bond.document("note").head == plan.version and bond.document("note").title == "A note"


def test_a_loaded_plan_is_checked_again(bond):
    # A plan made under ALLOW_GAPS, edited to claim STRICT: its ids still hold, but STRICT refuses its gap.
    plan = bond.plan_write("note", "Left uncovered.", [], format=Format.MARKDOWN, policy=ALLOW_GAPS)
    edited = json.loads(bond.stage(plan))
    edited["policy"]["gaps"] = False
    with pytest.raises(WriteError) as e:
        bond.commit(bond.load_plan(json.dumps(edited)))
    assert e.value.codes == [IssueCode.UNCOVERED_CLAIM] and bond.documents() == []


def test_a_loaded_plan_that_doesnt_hash_to_its_ids_is_refused(bond):
    source = bond.ingest("src", b"Six weeks of notice.", format=Format.MARKDOWN).version
    cited = [Citation(CitationKind.QUOTE, TextLocator(0, 9), [bond.search("Six weeks", documents=["src"])[0].ref])]
    plan = bond.plan_write("note", "Six weeks", cited, format=Format.MARKDOWN)
    edited = json.loads(bond.stage(plan))
    edited["versions"][0]["citations"][0]["kind"] = "paraphrase"  # what the id doesn't commit to
    with pytest.raises(BondError, match="doesn't hash to its id"):
        bond.commit(bond.load_plan(json.dumps(edited)))
    assert [d.name for d in bond.documents()] == ["src"] and source


def test_a_plan_needs_its_bytes_staged(bond, tmp_path):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    with pytest.raises(BondError, match="stage the plan first"):
        Bond(bond.store, LocalBlobs(tmp_path / "empty"), bond.workspace.name).load_plan(plan.to_json())


def test_a_plan_commits_only_in_its_workspace(bond):
    plan = bond.plan_write("note", TEXT, WHOLE, format=Format.MARKDOWN)
    elsewhere = Bond(bond.store, bond.blobs, "elsewhere")
    with pytest.raises(ValueError, match="another workspace"):
        elsewhere.commit(plan)


def test_ingesting_what_is_stored_plans_only_its_filing(bond):
    v = bond.ingest("a", b"Same bytes.", format=Format.MARKDOWN).version
    plan = bond.plan_ingest("b", b"Same bytes.", format=Format.MARKDOWN)
    assert plan.versions == () and plan.version == v
    assert bond.commit(plan).advanced and [d.name for d in bond.documents()] == ["a", "b"]


def test_planning_an_import_stores_nothing_and_a_refused_filing_is_refused_up_front(bond):
    policies = Bond(bond.store, bond.blobs, "policies")
    policy = policies.ingest("policy", b"Six weeks of written notice.", format=Format.MARKDOWN).version
    plan = bond.plan_import(policies, "policy")
    assert [pv.new.id for pv in plan.versions] == [policy] and bond.records.version(policy) is None
    assert bond.commit(plan).advanced and bond.document("policy").head == policy
    bond.write_document("other", TEXT, WHOLE, format=Format.MARKDOWN)
    with pytest.raises(WriteError) as e:
        bond.plan_import(policies, "policy", name="other")
    assert e.value.codes == [IssueCode.DOCUMENT_EXISTS]
