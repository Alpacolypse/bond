"""The filing layer: documents, their histories, and how writes advance them."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from bond import Citation, CitationKind, Format, Ref, UnknownDocument, VersionLocator, WriteError, details
from bond import IssueCode as E


def source():
    return [Citation(CitationKind.SOURCE, VersionLocator(), [])]


def test_a_write_creates_a_document_and_later_writes_advance_it(bond):
    v1 = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    first = bond.document("note")
    assert v1.advanced and first.name == "note" and first.head == v1.version
    v2 = bond.write_document("note", b"Two.", source(), format=Format.MARKDOWN, base=v1.version)
    assert bond.document("note").id == first.id and bond.document("note").head == v2.version
    assert [(h.seq, h.version, h.base) for h in bond.history("note")] == [
        (1, v1.version, None), (2, v2.version, v1.version),
    ]


def test_base_must_match_the_document(bond):
    v1 = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    with pytest.raises(WriteError) as e:
        bond.write_document("note", b"Two.", source(), format=Format.MARKDOWN)
    assert e.value.codes == [E.DOCUMENT_EXISTS]
    with pytest.raises(WriteError) as e:
        bond.write_document("missing", b"Two.", source(), format=Format.MARKDOWN, base=v1.version)
    assert e.value.codes == [E.UNKNOWN_DOCUMENT]

    # Two writers start from v1; the second one to write is stale and stores nothing.
    a = bond.write_document("note", b"A.", source(), format=Format.MARKDOWN, base=v1.version)
    with pytest.raises(WriteError) as e:
        bond.write_document("note", b"B.", source(), format=Format.MARKDOWN, base=v1.version)
    assert e.value.codes == [E.STALE_BASE]
    assert e.value.errors[0].hint == "read its head, reconcile your changes with it, and write again with it as base"
    assert bond.document("note").head == a.version
    assert len(details.versions(bond)) == 2


def test_filing_errors_come_with_every_other_error(bond):
    bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    with pytest.raises(WriteError) as e:
        bond.write_document("note", b"Uncited claim.", [], format=Format.MARKDOWN)
    assert sorted(c.value for c in e.value.codes) == sorted([E.UNCOVERED_CLAIM.value, E.DOCUMENT_EXISTS.value])


def test_a_retried_write_is_a_no_op_whatever_its_base(bond):
    v1 = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    v2 = bond.write_document("note", b"Two.", source(), format=Format.MARKDOWN, base=v1.version)
    retry = bond.write_document("note", b"Two.", source(), format=Format.MARKDOWN, base=v1.version)
    assert retry.version == v2.version and not retry.advanced
    assert len(bond.history("note")) == 2


def test_writers_racing_from_one_base_file_exactly_one_version(bond):
    # Each writer's check passes against the head it read; filing admits the first, and the rest store nothing.
    v1 = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    writers = 8
    start = threading.Barrier(writers)

    def write(i: int) -> WriteError | None:
        start.wait()
        try:
            bond.write_document("note", f"Writer {i}.".encode(), source(), format=Format.MARKDOWN, base=v1.version)
        except WriteError as e:  # what a refused write raises: this test counts them
            return e
        return None

    with ThreadPoolExecutor(writers) as pool:
        outcomes = list(pool.map(write, range(writers)))
    refusals = [e for e in outcomes if e is not None]
    assert len(refusals) == writers - 1 and all(e.codes == [E.STALE_BASE] for e in refusals)
    [_, winner] = bond.history("note")
    assert winner.base == v1.version
    assert {v.id for v in details.versions(bond)} == {v1.version, winner.version}  # no loser stored a version


def test_a_document_needs_a_name_and_a_title_if_any(bond):
    with pytest.raises(WriteError) as e:
        bond.write_document("  ", b"One.", source(), format=Format.MARKDOWN, title=" ")
    assert e.value.codes == [E.INVALID_NAME, E.INVALID_NAME]
    assert [i.message for i in e.value.errors] == ["a document's name must be a non-empty string",
                                                   "a document's title must be a non-empty string, or None"]
    assert details.versions(bond) == [] and bond.documents() == []
    with pytest.raises(WriteError) as e:
        bond.ingest("", b"A policy.")
    assert e.value.codes == [E.INVALID_NAME]


def test_one_version_can_be_filed_in_many_documents(bond):
    fax = bond.ingest("case_002/fax", b"Fax text.")
    again = bond.ingest("case_007/fax", b"Fax text.")
    assert again.version == fax.version and again.advanced
    assert [d.name for d in details.filed_in(bond, fax.version)] == ["case_002/fax", "case_007/fax"]
    # Ingesting the same bytes files the same version again, wherever: there is no separate filing call.
    filed = bond.ingest("archive/fax", b"Fax text.")
    assert filed.version == fax.version and bond.document("archive/fax").head == fax.version


def test_a_revert_files_an_older_version_as_the_head(bond):
    v1 = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN)
    v2 = bond.write_document("note", b"Two.", source(), format=Format.MARKDOWN, base=v1.version)
    # Writing v1's content and citations again is v1 itself: a revert.
    back = bond.write_document("note", b"One.", source(), format=Format.MARKDOWN, base=v2.version)
    assert back.version == v1.version
    assert back.advanced and bond.document("note").head == v1.version
    assert [h.version for h in bond.history("note")] == [v1.version, v2.version, v1.version]


def test_a_title_is_a_label_set_by_a_write(bond):
    v1 = bond.write_document("draft_case_001", b"One.", source(), format=Format.MARKDOWN)
    assert bond.document("draft_case_001").title is None
    titled = bond.write_document("draft_case_001", b"Two.", source(), base=v1.version, title="Draft: case 001")
    assert bond.document("draft_case_001").title == "Draft: case 001"
    # A title is filing, not provenance: the same content is the same version, titled or not.
    assert titled.version == bond.write_document("plain", b"Two.", source()).version
    assert bond.verify(titled.version) == []
    bond.ingest("policy", b"A policy.", title="Policy")
    assert bond.document("policy").title == "Policy"


def test_search_reads_current_versions(bond):
    v1 = bond.ingest("policy", b"Six weeks of notice.")
    v2 = bond.ingest("policy", b"Eight weeks of notice.", base=v1.version)
    bond.ingest("other", b"Notice is optional.")
    assert [h.ref.version for h in bond.search("weeks of notice")] == [v2.version]
    # An older version is still searched when asked for by reference.
    assert [h.ref.version for h in bond.search("weeks of notice", within=Ref(v1.version, VersionLocator()))] == [
        v1.version]
    assert [h.documents for h in bond.search("notice", documents=["other"])] == [["other"]]
    with pytest.raises(UnknownDocument):
        bond.search("notice", documents=["missing"])
