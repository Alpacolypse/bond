"""Records hold no content: the records Bond stores for versions and their citations are hashes, enums, numbers,
positional locators and registered producer ids, whatever the documents say, and no key holds any text at all.
See docs/storage.md, "What Bond stores".

Document names, titles and origins are filing, and may hold anything a person types; they live in other keys' values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest
import seed

from bond import Bond, Citation, CitationKind, Format, JsonLocator, PdfLocator, Ref, VersionLocator, WriteError, details
from bond.authoring import AGENT_POLICY, Tools
from bond.importers.text_layer import TEXT_LAYER
from bond.jsonvalue import JsonValue
from bond.locators import locator_from_dict
from bond.storage.store import Key
from bond.transcription import transcribe
from conftest import refused, written

HASH = re.compile(r"[0-9a-f]{64}")
WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{32}")  # Bond's id for a workspace, never its name
DOCUMENT_ID = re.compile(r"doc_[0-9a-f]{32}")
PRODUCERS = {"pypdf", "reducto"}  # the producers this workspace's transcriptions name

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


def assert_closed_locator(d: Mapping[str, JsonValue]) -> None:
    """A stored locator: its type, and numbers. No keys, no text."""
    locator_from_dict(d)
    assert d["type"] in {"text", "json", "csv", "pdf", "version"}
    for key, value in d.items():
        if key == "type":
            continue
        values = value if isinstance(value, list) else [value]
        assert all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in values), (key, value)


def test_words_offered_as_tag_attributes_are_refused(store):
    tools = Tools(store, AGENT_POLICY)
    for draft in ['<bond:cite kind="unsourced" reason="Zelphine Quarrow\'s birth date">Born</bond:cite>\n',
                  '<bond:cite kind="unsourced" author="Dr. Lena Okoro">Noted</bond:cite>\n']:
        [issue] = refused(tools.write("attempts/attr.md", draft))
        assert "which a tag doesn't take" in issue
    pdf = next(v.id for v in details.versions(store) if v.format is Format.PDF)
    text = json.dumps({"text": "Jordan Avery"}).encode()
    with pytest.raises(WriteError, match="a producer is a tool's id"):
        store.write_document(
            "attempts/ocr.json",
            text,
            [Citation(CitationKind.TRANSCRIPTION, JsonLocator((0,)), [Ref(pdf, PdfLocator(0, (0.1, 0.1, 0.5, 0.2)))],
                      "Zelphine Quarrow")],
            format=Format.JSON,
        )


def keyed(bond: Bond) -> dict[Key, Any]:
    """Every key in the store, with its value as JSON."""
    store = bond.store
    found = {}
    for key in store.list(()):
        data = store.get(key)
        assert data is not None
        found[key] = json.loads(data)
    return found


def test_no_key_holds_text_from_any_document_or_name(store):
    # Keys reach logs that values don't, so even names, which are filing, are hashed in them.
    for key in keyed(store):
        for e in key:
            assert isinstance(e, int) or e in {"workspaces", "records", "names", "entries", "titles"} or any(
                p.fullmatch(e) for p in (HASH, WORKSPACE_ID, DOCUMENT_ID)), key


def test_every_record_value_is_a_hash_an_enum_a_number_or_a_registered_id(store):
    kinds, formats = {k.value for k in CitationKind}, {f.value for f in Format}
    records = [value for key, value in keyed(store).items() if key[1:2] == ("records",)]
    assert records
    for r in records:
        assert set(r) == {"id", "content_hash", "format", "created_at", "citations"}, "a field this check doesn't cover"
        assert HASH.fullmatch(r["id"]) and HASH.fullmatch(r["content_hash"]) and r["format"] in formats
        datetime.fromisoformat(r["created_at"])
        for c in r["citations"]:
            assert set(c) == {"id", "kind", "claim", "producer", "supports"}, "a field this check doesn't cover"
            assert HASH.fullmatch(c["id"]) and c["kind"] in kinds
            assert_closed_locator(c["claim"])
            assert c["producer"] is None or c["producer"] in PRODUCERS
            for version, locator in c["supports"]:
                assert HASH.fullmatch(version)
                assert_closed_locator(locator)
        text = json.dumps(r, ensure_ascii=False).lower()
        assert not [w for w in WORDS if w in text], r


def test_the_words_are_in_the_store_so_their_absence_from_records_means_something(store):
    filing = " ".join(json.dumps(v, ensure_ascii=False) for k, v in keyed(store).items() if k[1:2] != ("records",))
    assert "Zelphine Quarrow" in filing and "CL-QX7731" in filing  # document names and a title are filing
    assert "Unions/Wexmoor Tenants Union/roster.csv" in filing  # and so is an origin
    # And the content itself is the agent's to read, through the version.
    [summary] = [d for d in store.documents() if d.name == "summaries/CL-QX7731.json"]
    text = store.text(Ref(summary.head, VersionLocator()))
    assert text is not None and "CL-QX7731" in text
    [cited] = store.citations(summary.head)
    assert details.pointer(store, Ref(summary.head, cited.claim)) == "/CL-QX7731/Zelphine Quarrow"
    assert "CL-QX7731" in details.pointer(store, cited.supports[0])
