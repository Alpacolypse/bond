"""CSV and TSV: cells, rows and columns are citable; every row is data; values are decoded."""

import re
from pathlib import Path

import pytest

from bond import (
    ALLOW_GAPS,
    Bond,
    Citation,
    CitationKind,
    Format,
    Ref,
    SelfRef,
    TextLocator,
    VersionLocator,
    WriteError,
    details,
)
from bond import IssueCode as E
from bond.authoring import ToolError, Tools
from bond.authoring.handles import BadHandle, format_handle, parse_handle
from bond.formats import sniff
from bond.locators import CsvLocator, LocatorError, contains, locator_from_dict, overlaps
from conftest import refused, written

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "expenses_case_001.csv"
NOTE = 'Leak traced to the unit above; "no response" from landlord'


@pytest.fixture
def claims(bond: Bond):
    return bond.ingest("expenses_case_001", FIXTURE).version


def cell(row: int, column: int, start: int | None = None, end: int | None = None) -> CsvLocator:
    return CsvLocator(row, column, None if start is None or end is None else (start, end))


# --- reading -------------------------------------------------------------------------


def test_a_csv_is_named_by_its_suffix_never_sniffed(bond, claims):
    assert details.version(bond, claims).format is Format.CSV
    assert sniff(FIXTURE.read_bytes()) is Format.MARKDOWN  # any text would pass for a table


def test_cells_hold_decoded_values_and_every_row_is_data(bond, claims):
    assert bond.text(Ref(claims, cell(0, 0))) == "expense_id"  # the byte-order mark is skipped; the header is data
    assert bond.text(Ref(claims, cell(1, 2))) == "Avery, Jordan"  # quotes are syntax
    assert bond.text(Ref(claims, cell(2, 9))) == NOTE  # doubled quotes are one quote
    assert bond.text(Ref(claims, cell(3, 9))) == "Repair and deduct requested.\nAwaiting landlord response."
    assert bond.text(Ref(claims, cell(1, 9))) == ""  # an empty cell is a cell
    # A row or a column is many values, like a JSON object: it has no single text.
    assert bond.text(Ref(claims, CsvLocator(row=1))) is None and bond.text(Ref(claims, CsvLocator(column=4))) is None


def test_claim_units_are_the_non_empty_cells_in_reading_order(bond, claims):
    units = [u.locator for u in details.claim_units(bond, claims)]
    assert units[:3] == [cell(0, 0), cell(0, 1), cell(0, 2)]
    assert cell(1, 9) not in units and cell(4, 4) not in units  # empty
    assert len(units) == 10 + 9 + 10 + 10 + 8  # the last row is short and has an empty code


@pytest.mark.parametrize("locator, problem", [
    (CsvLocator(row=5), "row 5 does not exist (the table has 5 rows"),
    (CsvLocator(column=10), "column 10 does not exist (the table has 10 columns"),
    (cell(4, 9), "row 4 has no column 9 (it has 9 cells)"),
    (cell(1, 2, 0, 20), "ends past the cell at row 1, column 2 (13 characters)"),
    (TextLocator(0, 1), "text locator is not valid in a csv document"),
])
def test_a_locator_must_fit_the_table(bond, claims, locator, problem):
    with pytest.raises(ValueError, match=re.escape(problem)):
        bond.text(Ref(claims, locator))


def test_malformed_csv_is_refused(bond):
    with pytest.raises(WriteError, match="invalid CSV"):
        bond.ingest("broken", b'a,"b\n', format=Format.CSV)
    with pytest.raises(WriteError, match="not valid UTF-8"):
        bond.ingest("latin", "café,1\n".encode("cp1252"), format=Format.CSV)


def test_tsv_is_tab_separated(bond):
    v = bond.ingest("codes", b"code\tdescription\nR300\tCeiling repair, bathroom\n", format=Format.TSV).version
    assert bond.text(Ref(v, cell(1, 1))) == "Ceiling repair, bathroom"


# --- locators and handles ----------------------------------------------------------------


def test_csv_locators_address_cells_rows_and_columns():
    for loc in (cell(2, 9), cell(2, 9, 3, 8), CsvLocator(row=2), CsvLocator(column=9)):
        assert locator_from_dict(loc.to_dict()) == loc
    assert cell(2, 9, 3, 8).to_dict() == {"type": "csv", "row": 2, "column": 9, "start": 3, "end": 8}
    for bad in ({"type": "csv"}, {"type": "csv", "row": -1}, {"type": "csv", "row": 1, "start": 0, "end": 2},
                {"type": "csv", "row": 1, "column": 1, "start": 0}):
        with pytest.raises(LocatorError):
            locator_from_dict(bad)
    row, column = CsvLocator(row=2), CsvLocator(column=9)
    assert contains(row, cell(2, 9)) and contains(column, cell(2, 9)) and contains(cell(2, 9), cell(2, 9, 0, 3))
    assert not contains(row, column) and not contains(cell(2, 9, 0, 3), cell(2, 9)) and not contains(row, cell(3, 9))
    assert overlaps(row, column) and overlaps(cell(2, 9), row) and overlaps(cell(2, 9, 0, 3), cell(2, 9, 2, 5))
    assert not overlaps(cell(2, 9, 0, 3), cell(2, 9, 3, 5)) and not overlaps(CsvLocator(row=1), CsvLocator(row=2))


def test_handles_number_rows_and_columns_from_one():
    for loc, handle in [(cell(2, 9), "4b7e0d21:r3:c10"), (cell(2, 9, 3, 8), "4b7e0d21:r3:c10@3-8"),
                        (CsvLocator(row=2), "4b7e0d21:r3"), (CsvLocator(column=9), "4b7e0d21:c10")]:
        assert format_handle("4b7e0d21", loc) == handle
        assert parse_handle(handle) == ("4b7e0d21", loc)
    for bad, why in [("4b7e0d21:r0:c1", "numbered from 1"), ("4b7e0d21:r1@0-2", "only a cell can have a range"),
                     ("4b7e0d21:c1:r1", "isn't a locator")]:
        problem = parse_handle(bad)
        assert isinstance(problem, BadHandle) and why in problem.message, bad


# --- searching and citing ------------------------------------------------------------------


def test_search_finds_cells_and_says_where_they_are(bond, claims):
    tools = Tools(bond)
    [hit] = tools.search('"no response"', document="expenses_case_001")
    assert hit.handle.endswith(":r3:c10@31-44") and hit.text == '"no response"'
    assert hit.where == "row 3, column 10"
    assert hit.passage is not None and hit.passage.endswith(":r3:c10")
    with pytest.raises(ToolError, match="in a table draft every citation claims a cell"):
        tools.draft("expenses_case_001")  # an ingested file is a source throughout: one claim on the whole version
    assert tools.read("expenses_case_001").text == FIXTURE.read_bytes().decode("utf-8")  # exactly as it is


def test_a_summary_quotes_a_cell_and_paraphrases_a_row_and_a_column(bond, claims):
    tools = Tools(bond)
    code = next(h for h in tools.search("R300", document="expenses_case_001") if h.where == "row 4, column 5")
    p = code.handle.split(":")[0]
    draft = (f'<bond:cite kind="quote" from="{p}:r3:c10">{NOTE}</bond:cite>\n\n'
             f'<bond:cite kind="quote" from="{code.handle}">R300</bond:cite> '
             f'<bond:cite kind="paraphrase" from="{p}:r4">is pending review</bond:cite>.\n\n'
             f'<bond:cite kind="paraphrase" from="{p}:c8">Four expenses were logged.</bond:cite>\n')
    written(tools.write("summary", draft))
    trace = bond.trace(Ref(bond.document("summary").head, VersionLocator()))
    reached = {(n.version, n.locator) for n in trace.nodes}
    assert (claims, cell(2, 9)) in reached and (claims, CsvLocator(row=3)) in reached
    assert (claims, CsvLocator(column=7)) in reached
    # Each rests on the expense log, a source throughout.
    assert {e.citation.kind for e in trace.edges if e.node.version == claims} == {CitationKind.SOURCE}
    # A row isn't text, so it can't be quoted.
    [issue] = refused(tools.write("bad", f'<bond:cite kind="quote" from="{p}:r4">review</bond:cite>\n', dry_run=True))
    assert issue == '<bond:cite kind="quote">: quote support must address text, but its locator holds none'


def test_a_written_table_accounts_for_every_cell(bond, claims):
    content = b"code,meaning\nR300,Ceiling repair\nR100,-\n"
    quote = Citation(CitationKind.QUOTE, cell(1, 0), [Ref(claims, cell(3, 4))])
    with pytest.raises(WriteError) as exc:
        bond.write_document("codes", content, [quote], format=Format.CSV)
    gaps = [i.locator for i in exc.value.errors if i.code is E.UNCOVERED_CLAIM]
    # The header is data like any row; a cell with no word in it ("-") needs no citation.
    assert gaps == [cell(0, 0), cell(0, 1), cell(1, 1), cell(2, 0)]
    # A whole row or column covers its cells; a range covers part of one.
    header = Citation(CitationKind.UNSOURCED, CsvLocator(row=0), [])
    codes = Citation(CitationKind.UNSOURCED, CsvLocator(column=0), [])
    part = Citation(CitationKind.QUOTE, cell(1, 1, 0, 7), [Ref(claims, cell(3, 5, 0, 7))])
    v = bond.write_document("codes", content, [quote, header, codes, part], format=Format.CSV, policy=ALLOW_GAPS).version
    assert [g.locator for g in bond.declarations(Ref(v, VersionLocator())).undeclared] == [cell(1, 1, 8, 14)]
    # Listed in reading order: the column first (it spans every row), then row by row.
    assert [c.claim for c in bond.citations(v)] == [CsvLocator(column=0), CsvLocator(row=0), cell(1, 0),
                                                     cell(1, 1, 0, 7)]
    assert bond.verify(v, ALLOW_GAPS) == []


def test_a_cell_rests_on_another_cell_of_the_same_table(bond):
    content = b"total,note\n1250.00,see total\n"
    citations = [
        Citation(CitationKind.UNSOURCED, CsvLocator(row=0), []),
        Citation(CitationKind.UNSOURCED, cell(1, 0), []),
        Citation(CitationKind.PARAPHRASE, cell(1, 1), [SelfRef(cell(1, 0))]),
    ]
    v = bond.write_document("totals", content, citations, format=Format.CSV).version
    [edge] = bond.trace(Ref(v, cell(1, 1))).edges_at(Ref(v, cell(1, 1)))
    assert edge.citation.supports == [Ref(v, cell(1, 0))]


def test_the_viewer_api_serves_cells(bond, claims):
    from bond.http import viewer

    a = viewer.claim_annotations(bond, claims)
    assert a["units"][0] == {"locator": {"type": "csv", "row": 0, "column": 0}, "kinds": ["source"], "text": "expense_id"}
    assert a["gaps"] == []
