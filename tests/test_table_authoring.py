"""Authoring tables: CSV drafts with citations as tags in their cells, which move with the cells they sit in."""

from pathlib import Path

import pytest
import seed

from bond import ALLOW_GAPS, Bond, Citation, CitationKind, Format, Ref, SelfRef, VersionId, VersionLocator, details
from bond.authoring import Edit, Tools
from bond.authoring.handles import Handle, format_handle, parse_handle
from bond.authoring.markup import Unrenderable
from bond.authoring.tablemarkup import canonical, compile, render
from bond.locators import CsvLocator
from conftest import refused, written


def head(bond: Bond, document: str) -> VersionId:
    """The version a document now points at: what the last write of it stored."""
    return bond.document(document).head


def content(bond: Bond, version: VersionId) -> str:
    text = bond.text(Ref(version, VersionLocator()))
    assert text is not None
    return text


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "expenses_case_001.csv"
OTHER = VersionId("e" * 64)


def evidence(ref):
    return "E1"


def cell(row: int, column: int, start: int | None = None, end: int | None = None) -> CsvLocator:
    return CsvLocator(row, column, None if start is None or end is None else (start, end))


@pytest.fixture
def claims(bond: Bond):
    return bond.ingest("expenses_case_001", FIXTURE).version


def handle(claims: VersionId, locator: CsvLocator) -> str:
    """The handle of a span of the expense log, as search() would give it: the store holds no other version
    whose id starts the same."""
    return format_handle(claims[:8], locator)


# --- render and compile ---------------------------------------------------------------------


def test_render_puts_every_citation_in_a_cell_and_compile_reads_it_back():
    content = canonical([["code", "description"], ["R300", "Ceiling repair, bathroom"], ["R100", ""]], ",")
    citations = [
        Citation(CitationKind.UNSOURCED, CsvLocator(row=0), []),
        Citation(CitationKind.UNSOURCED, CsvLocator(column=0), []),
        Citation(CitationKind.QUOTE, cell(1, 1, 0, 7), [Ref(OTHER, VersionLocator())]),
        Citation(CitationKind.PARAPHRASE, cell(1, 1, 8, 24), [SelfRef(cell(1, 0))]),  # rests on an uncited cell
        Citation(CitationKind.PARAPHRASE, cell(2, 0), [SelfRef(CsvLocator(row=0))]),  # rests on a cited row
    ]
    draft = render(content, ",", citations, evidence)
    assert draft == (
        "\"<bond:cite kind='unsourced' on='column'/>"
        "<bond:cite id='c1' kind='unsourced' on='row'/>code\",description\n"
        "\"<bond:anchor id='a1'/>R300\",\"<bond:cite kind='quote' from='E1'>Ceiling</bond:cite> "
        "<bond:cite kind='paraphrase' from='#a1'>repair, bathroom</bond:cite>\"\n"
        "\"<bond:cite kind='paraphrase' from='#c1'/>R100\",\n"
    )
    compiled = compile(draft, ",")
    assert compiled.issues == [] and compiled.content == content
    assert [(e.kind, e.claim, e.sources) for e in compiled.entries] == [
        (CitationKind.UNSOURCED, CsvLocator(column=0), []),
        (CitationKind.UNSOURCED, CsvLocator(row=0), []),
        (CitationKind.QUOTE, cell(1, 1, 0, 7), ["E1"]),
        (CitationKind.PARAPHRASE, cell(1, 1, 8, 24), ["#a1"]),
        (CitationKind.PARAPHRASE, cell(2, 0), ["#c1"]),
    ]
    assert compiled.names == {"c1": CsvLocator(row=0), "a1": cell(1, 0)}


def test_a_table_is_stored_in_one_layout():
    compiled = compile('﻿a,"b"\r\n"x,y","say ""hi"""\r\n\r\n', ",")
    assert compiled.issues == [] and compiled.content == 'a,b\n"x,y","say ""hi"""\n\n'
    assert compile("a\tb\n1\t2\n", "\t").content == "a\tb\n1\t2\n"


@pytest.mark.parametrize("draft, message", [
    ('a,"b\n', "the draft can't be read as a table"),
    ("\"<bond:cite kind='quote' on='row'>x</bond:cite>\"", "wraps text, so it cites that text; on='row' is only for"),
    ("\"<bond:cite kind='source' on='table'/>x\"", "has on='table'; a self-closing tag cites its cell"),
    ("\"<bond:anchor id='k' on='sheet'/>x\"", "the anchor 'k' at row 1, column 1 has on='sheet'"),
    ("\"<bond:anchor id='k'/>x\",\"<bond:anchor id='k'/>y\"", 'two citations or anchors have the id "k"'),
    ("\"<bond:cite kind='quote'>x\"", "in the cell at row 1, column 1: "),
])
def test_compile_says_what_is_malformed(draft, message):
    issues = compile(draft, ",").issues
    assert any(message in i for i in issues), issues


def test_render_refuses_what_a_table_draft_cant_show():
    with pytest.raises(Unrenderable, match="every citation claims a cell"):
        render("a\n", ",", [Citation(CitationKind.SOURCE, VersionLocator(), [])], evidence)
    with pytest.raises(Unrenderable, match="reserved for citation tags"):
        render("see <bond:x>\n", ",", [], evidence)
    with pytest.raises(Unrenderable, match="row 2 has no cell to hold its citation"):
        render("a\n\n", ",", [Citation(CitationKind.UNSOURCED, CsvLocator(row=1), [])], evidence)


# --- through the tools ------------------------------------------------------------------


def test_an_agent_writes_a_cited_table(bond, claims):
    tools = Tools(bond)  # the agent policy
    repair = handle(claims, cell(3, 4))
    [hit] = tools.search("bathroom", document="expenses_case_001")
    assert hit.support == "supported" and hit.kinds == [CitationKind.SOURCE]
    draft = (
        "\"<bond:cite kind='unsourced' on='row'/>code\",description\n"
        f"\"<bond:cite kind='quote' from='{repair}'/>R300\",\"<bond:cite kind='quote' from='{hit.handle}'>bathroom"
        "</bond:cite>, <bond:cite kind='unsourced'>ceiling</bond:cite>\"\n"
    )
    written(tools.write("rollup", draft, format=Format.CSV))
    version = head(bond, "rollup")
    assert content(bond, version) == "code,description\nR300,\"bathroom, ceiling\"\n"
    claims_cited = {s.locator for c in bond.citations(version) for s in c.supports}
    assert claims_cited == {cell(3, 4), hit_locator(bond, hit.handle)}
    reading = tools.draft("rollup")
    again = written(tools.write("rollup", reading.text, reading.base))
    assert (head(bond, "rollup"), again.advanced) == (version, False)


def hit_locator(bond: Bond, h: str) -> CsvLocator:
    """The cell, or part of one, a search hit's handle names."""
    named = parse_handle(h)
    assert isinstance(named, Handle) and isinstance(named.locator, CsvLocator), named
    return named.locator


def test_an_uncited_cell_is_named_by_its_row_and_column(bond, claims):
    assert refused(Tools(bond).write("t", "code\nR300\n", format=Format.CSV, dry_run=True)) == [
        "no citation covers 'code' at row 1, column 1 (tag it in its cell: <bond:cite …>part of it</bond:cite>, or a "
        "self-closing <bond:cite …/> for the whole cell (with on='row' or on='column' for its row or column): a quote "
        'or paraphrase citing a handle from search(), or kind "unsourced" if you made it up; or remove it)',
        "no citation covers 'R300' at row 2, column 1 (tag it in its cell: <bond:cite …>part of it</bond:cite>, or a "
        "self-closing <bond:cite …/> for the whole cell (with on='row' or on='column' for its row or column): a quote "
        'or paraphrase citing a handle from search(), or kind "unsourced" if you made it up; or remove it)',
    ]


CODES = """\
code,description
R100,Dehumidifier rental
R200,"Plumber visit, leak diagnosis"
R300,"Ceiling repair, bathroom"
"""


def test_citations_move_with_the_cells_they_sit_in(bond, claims):
    # A table whose cells quote the expense log: rows 1-3 here are its rows 1-3, columns 4 and 5.
    citations = [Citation(CitationKind.UNSOURCED, CsvLocator(row=0), [])]
    citations += [Citation(CitationKind.QUOTE, cell(r, c), [Ref(claims, cell(r, 4 + c))]) for r in (1, 2, 3) for c in (0, 1)]
    bond.write_document("codes", CODES.encode(), citations, format=Format.CSV)
    tools = Tools(bond)
    reading = tools.draft("codes")
    header, rental, visit, repair = reading.text.splitlines(keepends=True)
    # Moving a row moves its citations: the repair row, now first, still quotes the expense log's repair line.
    written(tools.edit("codes", [Edit(rental + visit + repair, repair + rental + visit)], reading.base))
    by_claim = {c.claim: c.supports for c in bond.citations(head(bond, "codes"))}
    assert by_claim[cell(1, 0)] == [Ref(claims, cell(3, 4))] and by_claim[cell(3, 1)] == [Ref(claims, cell(2, 5))]
    # Deleting a row deletes its citations with it.
    reading = tools.draft("codes")
    written(tools.edit("codes", [Edit(reading.text.splitlines(keepends=True)[3], "")], reading.base))
    assert {c.claim.row for c in bond.citations(head(bond, "codes")) if isinstance(c.claim, CsvLocator)} == {0, 1, 2}
    # Inserting a column moves the cells after it: the agent adds its cell, cited, to every row.
    reading = tools.draft("codes")
    lines = reading.text.splitlines(keepends=True)
    edits = [Edit(lines[0], "\"<bond:cite kind='unsourced' on='column'/>line\"," + lines[0])]
    edits += [Edit(line, f"{n}," + line) for n, line in enumerate(lines[1:], 1)]
    written(tools.edit("codes", edits, reading.base))
    by_claim = {c.claim: c.supports for c in bond.citations(head(bond, "codes"))}
    assert CsvLocator(column=0) in by_claim and by_claim[cell(1, 1)] == [Ref(claims, cell(3, 4))]


def test_every_seeded_table_written_with_citations_round_trips(tmp_path):
    with Bond.local(tmp_path / "store") as bond:
        seed.seed(bond)
        tools = Tools(bond, ALLOW_GAPS)
        tables = [d for d in bond.documents() if details.version(bond, d.head).format in (Format.CSV, Format.TSV)
                  and not any(c.claim == VersionLocator() for c in bond.citations(d.head))]  # not sources
        assert [d.name for d in tables] == ["expense_rollup_case_001"]
        for doc in tables:
            reading = tools.draft(doc.name)
            out = written(tools.write(doc.name, reading.text, reading.base))
            assert (head(bond, doc.name), out.advanced) == (doc.head, False), doc.name


def test_a_tsv_is_written_the_same_way(bond, claims):
    tools = Tools(bond)
    draft = f"<bond:cite kind='quote' from='{handle(claims, cell(3, 4))}'/>R300\tok\n"
    written(tools.write("codes", draft.replace("\tok", "\t<bond:cite kind='unsourced'/>ok"), format=Format.TSV))
    assert content(bond, head(bond, "codes")) == "R300\tok\n"
