import pytest

from bond import CitationKind, Format, IssueCode, Ref, UnknownVersion, VersionLocator, WriteError, details
from conftest import FIXTURES


def whole(version):
    return Ref(version, VersionLocator())


def test_read_returns_exact_content(bond):
    raw = "# Note\r\n\r\nCafé “quoted”﻿\n"
    ref = bond.ingest("note", raw.encode(), format=Format.MARKDOWN)
    assert bond.text(whole(ref.version)) == raw
    assert bond.blob(ref.version) == raw.encode()
    assert details.version(bond, ref.version).format is Format.MARKDOWN
    assert bond.document("note").head == ref.version
    # An ingested file is a version that is a source throughout.
    [c] = bond.citations(ref.version)
    assert (c.kind, c.claim, c.supports) == (CitationKind.SOURCE, VersionLocator(), [])


def test_reingest_is_the_same_version_wherever_it_is_filed(bond):
    a = bond.ingest("policy", FIXTURES / "policy_ip112.md")
    b = bond.ingest("policy", (FIXTURES / "policy_ip112.md").read_bytes())
    c = bond.ingest("other name", str(FIXTURES / "policy_ip112.md"))
    assert a.version == b.version == c.version
    assert (a.advanced, b.advanced, c.advanced) == (True, False, True)
    assert [d.name for d in details.filed_in(bond, a.version)] == ["policy", "other name"]
    assert len(bond.history("policy")) == 1
    assert len(details.versions(bond)) == 1


def test_an_origin_is_filed_with_the_version_not_part_of_it(bond):
    with_origin = bond.ingest("policy", FIXTURES / "policy_ip112.md", origin="Policies/Acme/IP-112.md")
    without = bond.ingest("copy", FIXTURES / "policy_ip112.md")
    assert with_origin.version == without.version
    [entry] = bond.history("policy")
    assert (entry.version, entry.origin) == (with_origin.version, "Policies/Acme/IP-112.md")
    assert entry.to_dict()["origin"] == "Policies/Acme/IP-112.md"
    assert bond.history("copy")[0].origin is None
    assert bond.verify(with_origin.version) == []


def test_reingesting_where_the_bytes_are_records_no_new_origin(bond):
    first = bond.ingest("policy", FIXTURES / "policy_ip112.md", origin="inbox/IP-112.md")
    again = bond.ingest("policy", FIXTURES / "policy_ip112.md", origin="archive/IP-112.md")
    assert (again.version, again.advanced) == (first.version, False)
    assert [e.origin for e in bond.history("policy")] == ["inbox/IP-112.md"]


def test_each_entry_keeps_the_origin_it_was_filed_with(bond):
    v1 = bond.ingest("note", b"draft one", origin="drafts/one.md")
    bond.ingest("note", b"draft two", base=v1.version, origin="drafts/two.md")
    assert [e.origin for e in bond.history("note")] == ["drafts/one.md", "drafts/two.md"]


def test_format_inference(bond):
    def fmt(r):
        return details.version(bond, r.version).format

    assert fmt(bond.ingest("fax", FIXTURES / "fax_case_001.pdf")) is Format.PDF
    assert fmt(bond.ingest("pdf bytes", (FIXTURES / "fax_case_001.pdf").read_bytes())) is Format.PDF
    assert fmt(bond.ingest("j", b'{"a": 1}')) is Format.JSON
    assert fmt(bond.ingest("t", b"plain text")) is Format.MARKDOWN


def test_reingest_new_bytes_needs_the_head_as_base(bond):
    v1 = bond.ingest("note", b"draft one")
    with pytest.raises(WriteError) as e:
        bond.ingest("note", b"draft two")
    assert e.value.codes == [IssueCode.DOCUMENT_EXISTS]
    v2 = bond.ingest("note", b"draft two", base=v1.version)
    assert [h.version for h in bond.history("note")] == [v1.version, v2.version]
    assert bond.document("note").head == v2.version


def test_a_pdf_has_no_text_of_its_own(bond):
    ref = bond.ingest("fax", FIXTURES / "fax_case_001.pdf")
    assert bond.text(whole(ref.version)) is None
    assert bond.blob(ref.version) == (FIXTURES / "fax_case_001.pdf").read_bytes()


def test_malformed_sources_are_rejected(bond):
    for name, data, fmt in [("dup", b'{"a": 1, "a": 2}', Format.JSON), ("bin", b"\xff\xfe", Format.MARKDOWN),
                            ("pdf", b"%PDF-1.4 truncated", None)]:
        with pytest.raises(WriteError) as e:
            bond.ingest(name, data, format=fmt)
        assert e.value.codes == [IssueCode.INVALID_CONTENT]
    assert details.versions(bond) == []


def test_unknown_version(bond):
    with pytest.raises(UnknownVersion):
        bond.blob("0" * 64)
    with pytest.raises(UnknownVersion):
        bond.citations("0" * 64)


def test_store_persists_across_instances(tmp_path):
    from bond import Bond

    with Bond.local(tmp_path) as b:
        ref = b.ingest("p", b"persist me")
    with Bond.local(tmp_path) as b:
        assert b.text(whole(ref.version)) == "persist me"


def test_ingest_takes_a_path_or_bytes(bond, tmp_path):
    table = tmp_path / "claims.csv"
    table.write_bytes(b"code,amount\nR300,1250.00\n")
    # A path's suffix names its format; bytes are sniffed, and tables are never sniffed, so name them.
    from_path = bond.ingest("claims", table)
    assert details.version(bond, from_path.version).format is Format.CSV
    assert bond.ingest("claims copy", table.read_bytes(), format=Format.CSV).version == from_path.version
    assert details.version(bond, bond.ingest("as text", table.read_bytes()).version).format is Format.MARKDOWN
