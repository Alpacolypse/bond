"""Records hold no content: the rows Bond stores for versions and their citations are hashes, enums, numbers,
positional locators and registered producer ids, whatever the documents say. See docs/storage.md, "What Bond stores".

Document names, titles and origins are filing, and may hold anything a person types; they live in other tables.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import LiteralString

import pytest
import seed

from bond import Bond, Citation, CitationKind, Format, JsonLocator, PdfLocator, Ref, VersionLocator, WriteError, details
from bond.authoring import AGENT_POLICY, Tools
from bond.importers.text_layer import TEXT_LAYER
from bond.jsonvalue import JsonValue
from bond.locators import locator_from_dict
from bond.policies import PRODUCERS
from bond.storage.sql import Row, SqlCatalog
from bond.transcription import transcribe
from conftest import refused, written

# The record tables and their columns, as selected here. A new column must be added to this closed schema, with a
# check that it can't hold content.
RECORD_TABLES: dict[LiteralString, tuple[LiteralString, ...]] = {
    "bond_versions": ("workspace", "id", "content_hash", "format", "created_at"),
    "bond_citations": ("workspace", "owner_version", "id", "kind", "claim_locator", "attrs"),
    "bond_supports": ("workspace", "owner_version", "citation_id", "position", "version", "locator"),
}
ORDERED = {"bond_versions", "bond_citations"}  # record tables the catalog lists in insertion order
# Workspace and document names, titles, where an import came from (a workspace's name) and where an ingested file
# came from (its origin, a path or URL) are filing: labels a person or host chose.
FILING_TABLES: dict[LiteralString, tuple[LiteralString, ...]] = {
    "bond_workspaces": ("id", "name", "created_at"),
    "bond_documents": ("id", "workspace", "name", "title", "created_at"),
    "bond_history": ("workspace", "document_id", "seq", "version", "base", "at", "from_workspace",
                     "from_workspace_name", "from_version", "origin"),
}

HASH = re.compile(r"[0-9a-f]{64}")
WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{32}")  # Bond's id for a workspace, never its name
PRODUCER_ATTRS = {"transcriber", "classifier", "assessor"}

# Words from the documents that must never reach a record. Each has a letter outside 0-9a-f, so no hash can hold
# it by chance. The synthetic clients and reviewers of the fixtures and the seed, and an adversarial source's.
CANARIES = ["Jordan Avery", "Samuel Okafor", "Priya Natarajan", "Lena Okoro", "Elena Marsh", "Hannah Cho",
            "Tomas Reyes", "NWL-4471902", "Zelphine Quarrow", "CL-QX7731", "Wexmoor Tenants Union", "1961-04-17"]
WORDS = sorted({w.lower() for c in CANARIES for w in re.split(r"\s+", c) if len(w) >= 3} | {c.lower() for c in CANARIES})

# A source keyed by data, as customer JSON often is: the keys are as private as the values.
INTAKE = {"clients": {"CL-QX7731": {"name": "Zelphine Quarrow", "born": "1961-04-17", "member of": "Wexmoor Tenants Union"}}}
ROSTER = "Zelphine Quarrow,CL-QX7731\nWexmoor Tenants Union,1961-04-17\n"


@pytest.fixture(params=["001", "003"])
def store(bond: Bond, request) -> Bond:
    """The demo store, plus adversarial sources and an agent's writes about them."""
    seed.seed(bond, request.param)
    written_by_pypdf = transcribe(bond, f"fax_case_{request.param}", TEXT_LAYER)  # the fax's own text layer, too
    assert written_by_pypdf.advanced
    bond.ingest("intake/Zelphine Quarrow (CL-QX7731).json", json.dumps(INTAKE).encode(), format=Format.JSON)
    bond.ingest("roster/Wexmoor Tenants Union.csv", ROSTER.encode(), format=Format.CSV,
                origin="Unions/Wexmoor Tenants Union/roster.csv")
    bond.ingest(
        "notes/Zelphine Quarrow.md",
        b"# Zelphine Quarrow\n\nMet at Wexmoor Tenants Union on 1961-04-17 (CL-QX7731).\n",
        format=Format.MARKDOWN,
        title="Intake note: Zelphine Quarrow, CL-QX7731",
    )

    tools = Tools(bond, AGENT_POLICY)

    def handle(phrase: str, document: str) -> str:
        [hit] = tools.search(phrase, document=document)
        return hit.handle

    # The JSON source's handles name its values by keys that are data: /clients/CL-QX7731/name.
    name = handle("Zelphine Quarrow", "intake/Zelphine Quarrow (CL-QX7731).json")
    union = handle("Wexmoor Tenants Union", "intake/Zelphine Quarrow (CL-QX7731).json")
    assert "CL-QX7731" in name
    markdown = (f'<bond:cite kind="unsourced">Visit summary</bond:cite>\n\n'
                f'<bond:cite kind="quote" from="{name}">Zelphine Quarrow</bond:cite> '
                f'<bond:cite kind="paraphrase" from="{union} {handle("Met at Wexmoor", "notes/Zelphine Quarrow.md")}">'
                f'was met at Wexmoor Tenants Union.</bond:cite>\n')
    written(tools.write("summaries/Zelphine Quarrow.md", markdown))
    draft = {"CL-QX7731": {"Zelphine Quarrow": {"$bond": {"cite": {"kind": "quote", "from": name}},
                                                 "value": "Zelphine Quarrow"}}}
    written(tools.write("summaries/CL-QX7731.json", json.dumps(draft), format=Format.JSON))
    table = (f"\"<bond:cite kind='quote' from='{handle('Zelphine Quarrow', 'roster/Wexmoor Tenants Union.csv')}'/>"
             "Zelphine Quarrow\"\n")
    written(tools.write("summaries/roster.csv", table, format=Format.CSV))
    return bond


def sql(bond: Bond) -> SqlCatalog:
    assert isinstance(bond.catalog, SqlCatalog)
    return bond.catalog


def rows(bond: Bond, table: LiteralString, columns: tuple[LiteralString, ...]) -> list[Row]:
    with sql(bond)._read() as ex:
        return ex.all(f"SELECT {', '.join(columns)} FROM {table}")


def test_record_tables_have_only_the_columns_this_schema_checks(store):
    catalog = sql(store)
    for table, columns in RECORD_TABLES.items():
        with catalog._read() as ex:
            [row] = ex.all(f"SELECT * FROM {table} LIMIT 1")
        # Postgres gives the tables it orders by insertion a column of their own (SQLite's rowid is implicit).
        ordered = bool(catalog.ORDER_DEFINITION) and table in ORDERED
        assert len(row) == len(columns) + ordered, f"{table} has a column this closed schema doesn't check"
        if ordered:
            with catalog._read() as ex:
                assert all(isinstance(n, int) for (n,) in ex.all(f"SELECT {catalog.ORDER} FROM {table}"))


def texts(row: Row) -> tuple[str, ...]:
    """A record's columns, which must all be text."""
    assert all(isinstance(value, str) for value in row), row
    return tuple(str(value) for value in row)


def test_every_record_field_is_a_hash_an_enum_a_number_or_a_registered_id(store):
    kinds, formats = {k.value for k in CitationKind}, {f.value for f in Format}
    versions = rows(store, "bond_versions", RECORD_TABLES["bond_versions"])
    citations = rows(store, "bond_citations", RECORD_TABLES["bond_citations"])
    supports = rows(store, "bond_supports", RECORD_TABLES["bond_supports"])
    assert versions and citations and supports
    for ws, v, content_hash, fmt, created_at in map(texts, versions):
        assert WORKSPACE_ID.fullmatch(ws) and HASH.fullmatch(v) and HASH.fullmatch(content_hash) and fmt in formats
        datetime.fromisoformat(created_at)
    for ws, owner, cid, kind, claim, attrs in map(texts, citations):
        assert WORKSPACE_ID.fullmatch(ws) and HASH.fullmatch(owner) and HASH.fullmatch(cid) and kind in kinds
        assert_closed_locator(json.loads(claim))
        attrs = json.loads(attrs)
        assert set(attrs) <= PRODUCER_ATTRS and all(value in PRODUCERS for value in attrs.values()), attrs
    for row in supports:
        ws, owner, cid, version, locator = texts(row[:3] + row[4:])
        position = row[3]
        assert WORKSPACE_ID.fullmatch(ws)
        assert HASH.fullmatch(owner) and HASH.fullmatch(cid) and HASH.fullmatch(version)
        assert isinstance(position, int) and not isinstance(position, bool)
        assert_closed_locator(json.loads(locator))


def assert_closed_locator(d: Mapping[str, JsonValue]) -> None:
    """A stored locator: its type, and numbers. No keys, no text."""
    locator_from_dict(d)
    assert d["type"] in {"text", "json", "csv", "pdf", "version"}
    for key, value in d.items():
        if key == "type":
            continue
        values = value if isinstance(value, list) else [value]
        assert all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in values), (key, value)


def test_no_word_of_any_document_reaches_a_record(store):
    for table, columns in RECORD_TABLES.items():
        for row in rows(store, table, columns):
            text = json.dumps(row, ensure_ascii=False).lower()
            assert not [w for w in WORDS if w in text], (table, row)


def test_the_words_are_in_the_store_so_their_absence_from_records_means_something(store):
    filing = " ".join(json.dumps(r, ensure_ascii=False) for t, c in FILING_TABLES.items() for r in rows(store, t, c))
    assert "Zelphine Quarrow" in filing and "CL-QX7731" in filing  # document names and a title are filing
    assert "Unions/Wexmoor Tenants Union/roster.csv" in filing  # and so is an origin
    # And the content itself is the agent's to read, through the version.
    [summary] = [d for d in store.documents() if d.name == "summaries/CL-QX7731.json"]
    text = store.text(Ref(summary.head, VersionLocator()))
    assert text is not None and "CL-QX7731" in text
    [cited] = store.citations(summary.head)
    assert details.pointer(store, Ref(summary.head, cited.claim)) == "/CL-QX7731/Zelphine Quarrow"
    assert "CL-QX7731" in details.pointer(store, cited.supports[0])


def test_words_offered_as_attrs_are_refused(store):
    tools = Tools(store, AGENT_POLICY)
    for draft in ['<bond:cite kind="unsourced" reason="Zelphine Quarrow\'s birth date">Born</bond:cite>\n',
                  '<bond:cite kind="unsourced" author="Dr. Lena Okoro">Noted</bond:cite>\n']:
        [issue] = refused(tools.write("attempts/attr.md", draft))
        assert "unsourced does not accept attrs" in issue
    pdf = next(v.id for v in details.versions(store) if v.format is Format.PDF)
    text = json.dumps({"text": "Jordan Avery"}).encode()
    with pytest.raises(WriteError, match="attrs.transcriber must be a producer id"):
        store.write_document(
            "attempts/ocr.json",
            text,
            [Citation(CitationKind.TRANSCRIPTION, JsonLocator((0,)), [Ref(pdf, PdfLocator(0, (0.1, 0.1, 0.5, 0.2)))],
                      {"transcriber": "Zelphine Quarrow"})],
            format=Format.JSON,
        )
