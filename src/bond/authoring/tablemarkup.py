"""Table drafts: a CSV or TSV document with its citations written into its cells, the form agents read and write tables in.

The draft is the table itself, read as CSV first; citations are tags inside cell values:

    expense_id,code,description
    "<bond:cite kind='paraphrase' from='2c1394cc:r4' on='row'/>EXP-0930-01","<bond:cite kind='quote' from='2c1394cc:r4:c5'/>R300","<bond:cite kind='quote' from='2c1394cc:r4:c6@0-7'>Ceiling</bond:cite> repair"

A wrapping tag cites part of a cell, as in text. A self-closing tag cites the cell it sits in, or with
on='row' or on='column' that cell's row or column. A self-closing <bond:anchor id='…'/> names its cell (or
row or column, with `on`) for `#id`, without citing it. Every citation lives in a cell, so it moves with
the cell when the writer moves rows or columns. Attributes are in single quotes, so a field holding a tag
is an ordinary quoted CSV field; read quotes every such field.

Every row is data. render and compile are inverses; content is always stored in one layout (canonical).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence

from ..locators import CsvLocator, Locator, Span
from ..models import Citation, Ref
from ..parsers import Malformed, read_rows
from . import markup
from .drafts import Compiled, Entry
from .markup import CLOSE_ANCHOR, CLOSE_TAG, Element, Unrenderable

ON = "on"

Site = tuple[int | None, int | None, Span | None]  # (row, column, range): a cell, row, column or range


def canonical(rows: Sequence[Sequence[str]], delimiter: str) -> str:
    """The one layout a table is stored in: the format's delimiter, quotes only where needed, "\\n" line ends."""
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return out.getvalue()


def where(locator: Locator) -> str:
    """How a message names a place in the table, numbered from 1 as handles are."""
    if not isinstance(locator, CsvLocator):
        return ""
    parts = ([f"row {locator.row + 1}"] if locator.row is not None else []) + (
        [f"column {locator.column + 1}"] if locator.column is not None else [])
    return ", ".join(parts)


# --- render ---------------------------------------------------------------------


def render(content: str, delimiter: str, citations: Sequence[Citation], evidence: Callable[[Ref], str]) -> str:
    """The draft of a table version: each citation as a tag in a cell. Raises Unrenderable for what tags can't show.

    That is: a claim that isn't a cell, row, column or range (a claim on the whole version), a row with no cell to
    hold its tag, "<bond:" in a cell, or two ranges of one cell that partly overlap.
    """
    rows = read_rows(content, delimiter)
    if isinstance(rows, Malformed):
        raise Unrenderable(rows.reason)
    for r, row in enumerate(rows):
        for col, value in enumerate(row):
            if markup.RESERVED in value:
                raise Unrenderable(f"the cell at {where(CsvLocator(r, col))} contains {markup.RESERVED!r}, which is "
                                   "reserved for citation tags")
    claims: dict[int, CsvLocator] = {}  # citation index -> what it claims
    home: dict[int, tuple[int, int]] = {}  # citation index -> the cell its tag goes in
    for i, c in enumerate(citations):
        if not isinstance(c.claim, CsvLocator):
            raise Unrenderable(f"a {c.kind.value} citation claims a {c.claim.type.value} locator; in a table draft "
                               "every citation claims a cell, a part of one, a row or a column")
        claims[i] = c.claim
        home[i] = _home(rows, c.claim)

    def site_order(site: Site) -> tuple[int, ...]:
        row, column, span = site
        r, col = _home(rows, CsvLocator(row, column))
        # Within a cell: its column's tags, then its row's, then the cell's, then ranges by position.
        rank = 0 if row is None else 1 if column is None else 2 if span is None else 3
        return (r, col, rank, span[0] if span else 0)

    appear = sorted(home, key=lambda i: (site_order(_site_of(claims[i])), i))
    names = markup.name(citations, appear, _site, site_order)
    order = {i: n for n, i in enumerate(appear)}

    def sources(i: int) -> list[str]:
        return [names.token(s, evidence) for s in citations[i].supports]

    def point(i: int) -> str:
        claim = claims[i]
        tag = markup.open_tag(citations[i], names.tags.get(i), sources(i), whole=True, quote="'")
        if claim.is_cell:
            return tag
        return tag[:-len("/>")] + f" {ON}='{'row' if claim.column is None else 'column'}'/>"

    cells: list[list[str]] = [list(row) for row in rows]
    for r, row in enumerate(rows):
        for col, value in enumerate(row):
            here = [i for i in appear if home[i] == (r, col)]
            points = [point(i) for i in here if claims[i].span is None]
            points += [_anchor_point(a, site) for site, a in names.anchors.items()
                       if site[2] is None and _home(rows, CsvLocator(site[0], site[1])) == (r, col)]
            elements = [Element(*span, order[i], markup.open_tag(citations[i], names.tags.get(i), sources(i), quote="'"),
                                CLOSE_TAG)
                        for i in here if (span := claims[i].span) is not None]
            elements += [Element(*span, len(citations) + n, markup.open_anchor(a, quote="'"), CLOSE_ANCHOR)
                         for n, ((at_row, at_column, span), a) in enumerate(names.anchors.items())
                         if span and (at_row, at_column) == (r, col)]
            if points or elements:
                cells[r][col] = "".join(points) + markup.place_elements(value, elements)
    return _draft_text(cells, delimiter)


def _home(rows: Sequence[Sequence[str]], loc: CsvLocator) -> tuple[int, int]:
    """The cell a tag for loc goes in: the cell itself; a row's first cell; a column's first cell, top down."""
    if loc.row is not None and loc.column is not None:
        return loc.row, loc.column
    if loc.row is not None:
        if loc.row >= len(rows) or not rows[loc.row]:
            raise Unrenderable(f"row {loc.row + 1} has no cell to hold its citation")
        return loc.row, 0
    column = loc.column if loc.column is not None else 0
    for r, row in enumerate(rows):
        if column < len(row):
            return r, column
    raise Unrenderable(f"no row has column {column + 1} to hold its citation")


def _site_of(locator: CsvLocator) -> Site:
    return (locator.row, locator.column, locator.span)


def _site(locator: Locator) -> Site | None:
    return _site_of(locator) if isinstance(locator, CsvLocator) else None


def _anchor_point(anchor_id: str, site: Site) -> str:
    row, column, _ = site
    on = "" if row is not None and column is not None else f" {ON}='{'row' if column is None else 'column'}'"
    return f"<{markup.ANCHOR} id='{anchor_id}'{on}/>"


def _draft_text(cells: list[list[str]], delimiter: str) -> str:
    """Rows as CSV, quoting every field that holds a tag (and any that needs quotes), "\\n" line ends."""
    def field(value: str) -> str:
        if markup.RESERVED in value or any(ch in value for ch in (delimiter, '"', "\n", "\r")):
            return '"' + value.replace('"', '""') + '"'
        return value
    return "".join(delimiter.join(field(v) for v in row) + "\n" for row in cells)


# --- compile --------------------------------------------------------------------


def compile(draft: str, delimiter: str) -> Compiled:
    """The content a table draft stands for, in the canonical layout, and its citations as entries."""
    rows = read_rows(draft, delimiter)
    if isinstance(rows, Malformed):
        return Compiled("", [], {}, [f"the draft can't be read as a table ({rows.reason}); a field holding a tag, a "
                                  f"{'tab' if delimiter == chr(9) else 'comma'}, a quote or a line break must be in "
                                  'double quotes, with any " inside it doubled'])
    entries: list[Entry] = []
    names: dict[str, Locator] = {}
    ids: list[str] = []
    issues: list[str] = []
    clean: list[list[str]] = []
    for r, row in enumerate(rows):
        clean_row = []
        for col, value in enumerate(row):
            if "bond:" not in value:
                clean_row.append(value)
                continue
            here = where(CsvLocator(r, col))
            parsed = markup.parse(value, points=True)
            issues.extend(f"in the cell at {here}: {i}" for i in parsed.issues)
            for tag in parsed.tags:
                on = tag.attrs.pop(ON, None)
                label = f"{markup.label(tag)} at {here}"
                if tag.span is not None:
                    if on is not None:
                        issues.append(f"{label} wraps text, so it cites that text; {ON}='{on}' is only for a "
                                      "self-closing tag, which cites its cell, row or column")
                        continue
                    claim = CsvLocator(r, col, tag.span)
                else:
                    located = _placed(r, col, on)
                    if located is None:
                        issues.append(f"{label} has {ON}='{on}'; a self-closing tag cites its cell, or with "
                                      f"{ON}='row' or {ON}='column' its row or column")
                        continue
                    claim = located
                entries.append(Entry(tag.kind, claim, list(tag.sources), dict(tag.attrs), label, tag.id))
                if tag.id is not None:
                    ids.append(tag.id)
                    names.setdefault(tag.id, claim)
            for anchor, span in parsed.anchors.items():
                ids.append(anchor)
                names.setdefault(anchor, CsvLocator(r, col, span))
            for anchor, on in parsed.points.items():
                ids.append(anchor)
                located = _placed(r, col, on)
                if located is None:
                    issues.append(f"the anchor {anchor!r} at {here} has {ON}='{on}'; it names its cell, or with "
                                  f"{ON}='row' or {ON}='column' its row or column")
                else:
                    names.setdefault(anchor, located)
            clean_row.append(parsed.content)
        clean.append(clean_row)
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(f'two citations or anchors have the id "{dup}"; ids must be unique in the draft')
    return Compiled(canonical(clean, delimiter), entries, names, list(dict.fromkeys(issues)))


def _placed(row: int, column: int, on: str | None) -> CsvLocator | None:
    """What a self-closing tag or anchor in the cell at (row, column) names, given its `on`; None if `on` is bad."""
    match on:
        case None:
            return CsvLocator(row, column)
        case "row":
            return CsvLocator(row=row)
        case "column":
            return CsvLocator(column=column)
        case _:
            return None
