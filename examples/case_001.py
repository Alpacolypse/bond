"""Case 001 end to end: ingest a fax and an intake policy, import its OCR output, write a cited summary, trace it.

The summary's citations are written as phrases, the way an agent works: it
finds each supporting phrase with search(), and pins each claim to the exact
range of the summary it covers. An expense history then cites the client's
expense log, a CSV, by cell, by part of a cell, by row and by column.

Run with: uv run python examples/case_001.py
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from bond import (
    Bond,
    Citation,
    CitationKind,
    Format,
    JsonLocator,
    Locator,
    PdfLocator,
    Ref,
    SelfRef,
    SupportStatus,
    TextLocator,
    VersionId,
    VersionLocator,
    details,
)
from bond.authoring import Tools
from bond.importers.reducto import import_reducto
from bond.locators import CsvLocator, Span
from bond.models import Support, Trace

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Titles for people, for every document the examples write (names are for code and agents).
TITLES = {
    "policy_ip112": "Policy IP-112: Repair and deduct",
    "fax_case_001": "Referral fax: Jordan Avery",
    "fax_case_001.reducto": "Fax OCR: Jordan Avery",
    "expenses_case_001": "Expense log: Jordan Avery",
    "expense_history_case_001": "Expense history: Jordan Avery",
    "expense_rollup_case_001": "Expense rollup: Jordan Avery",
    "summary_case_001": "Intake summary: Jordan Avery",
    "summary_case_001_reviewed": "Intake summary, reviewed: Jordan Avery",
    "fax_case_003": "Referral fax: Samuel Okafor",
    "fax_case_003.reducto": "Fax OCR: Samuel Okafor",
    "review_case_003": "Intake review: Samuel Okafor",
    "determination_case_003": "Intake decision: Samuel Okafor",
    "reconsideration_case_003": "Reconsideration review: Samuel Okafor",
}


def retitle(bond: Bond) -> None:
    """Give every document in the store that has a title in TITLES that title.

    A write can title the document it writes (title=); these were written by helpers and importers that
    don't take one, so they are titled afterwards, through Bond's filing.
    """
    for doc in bond.documents():
        if doc.name in TITLES:
            bond.filing.retitle(doc.name, TITLES[doc.name])


# --- citations, as the examples write them ----------------------------------------------

THIS = "this"  # Cited.document for the document being written


@dataclass(frozen=True)
class Cited:
    """A support as an example writes it: a phrase in a document, found by search, or a locator in it."""

    document: str  # a key of the documents the citations are built against, or THIS
    at: str | Locator  # a phrase, which must match exactly once, or a locator as given
    whole: bool = False  # cite the entire JSON value or table cell the phrase is in


@dataclass(frozen=True)
class Spec:
    """A citation as an example writes it: its claim as a phrase of the content (or a locator), and its supports."""

    kind: CitationKind
    claim: str | Locator
    supports: tuple[Cited, ...] = ()


def quote(claim: str | Locator, *supports: Cited) -> Spec:
    return Spec(CitationKind.QUOTE, claim, supports)


def paraphrase(claim: str | Locator, *supports: Cited) -> Spec:
    return Spec(CitationKind.PARAPHRASE, claim, supports)


def unsourced(claim: str | Locator) -> Spec:
    return Spec(CitationKind.UNSOURCED, claim)


def source(claim: str | Locator) -> Spec:
    return Spec(CitationKind.SOURCE, claim)


def at(document: str, phrase: str | Locator, whole: bool = False) -> Cited:
    return Cited(document, phrase, whole)


SUMMARY = """\
# Intake summary: Jordan Avery

Request: repair and deduct for a bathroom ceiling leak (HC 12.4), referred by Riverside Tenants Union.

## Findings

- Water leaking through the bathroom ceiling for 8 weeks.
- The tenant reports a "progressive ceiling leak in the bathroom" with mold spreading over the wall behind the toilet.
- Active drip from the bathroom ceiling light fixture.
- No significant damage to other rooms.

## Criteria check

| Criterion | Evidence |
|---|---|
| Repair not made | Habitability defect, water intrusion (HC 12.4) |
| At least 6 weeks of written notice | Six weeks of letters and calls, with no response |
| No emergency | No gas leak, no sewage backup, no exposed wiring, no loss of heat. |

Policy IP-112 accepts a case when a needed repair is still not made after at least 6 weeks of written notice, and letters and emails both count as written notice.
"""

CITATIONS: list[Spec] = [
    paraphrase("Request: repair and deduct for a bathroom ceiling leak (HC 12.4), referred by Riverside Tenants Union.",
               at("fax", "repair and deduct for a bathroom ceiling leak (HC 12.4)", whole=True),
               at("fax", "From: Riverside Tenants Union", whole=True)),
    quote("Water leaking through the bathroom ceiling for 8 weeks.",
          at("fax", "Water leaking through the bathroom ceiling for 8 weeks.")),
    paraphrase('The tenant reports a "progressive ceiling leak in the bathroom" with mold spreading over the wall '
               "behind the toilet.",
               at("fax", "Tenant reports a progressive ceiling leak", whole=True)),
    quote("progressive ceiling leak in the bathroom",
          at("fax", "progressive ceiling leak in the bathroom")),
    quote("Active drip from the bathroom ceiling light fixture.",
          at("fax", "Active drip from the bathroom ceiling light fixture.")),
    quote("No significant damage to other rooms.", at("fax", "No significant damage to other rooms.")),
    quote("Habitability defect, water intrusion (HC 12.4)", at("fax", "Habitability defect, water intrusion (HC 12.4)")),
    paraphrase("Six weeks of letters and calls, with no response",
               at("fax", "Sent 6 weeks of written notice", whole=True)),
    quote("No gas leak, no sewage backup, no exposed wiring, no loss of heat.",
          at("fax", "No gas leak, no sewage backup, no exposed wiring, no loss of heat.")),
    paraphrase("Policy IP-112 accepts a case when a needed repair is still not made after at least 6 weeks of written "
               "notice, and letters and emails both count as written notice.",
               at("policy", "A needed repair still not made after at least 6 weeks of written notice to the landlord."),
               at("policy", "Written notice includes a letter, an email, or a message through a tenant portal")),
    # Every word is accounted for: the title and the criteria rest on the fax and the policy, and the
    # writer's own headings have no supporting evidence declared.
    paraphrase("Intake summary: Jordan Avery", at("fax", "Client: Jordan Avery", whole=True)),
    unsourced("Findings"),
    unsourced("Criteria check"),
    unsourced("Criterion | Evidence"),
    paraphrase("| Repair not made |", at("policy", "A needed repair still not made")),
    paraphrase("At least 6 weeks of written notice",
               at("policy", "after at least 6 weeks of written notice")),
    paraphrase("No emergency",
               at("policy", "Emergency conditions include a gas leak, sewage backup, and no heat when the outside "
                            "temperature is below freezing.")),
]


# The client's expense log (fixtures/expenses_case_001.csv), rows and columns from 0: row 0 is its header
# row, which is data like any other. A history of it, citing cells, part of a cell, rows and columns.
EXPENSE_HISTORY = """\
# Expense history: Jordan Avery

- Four expenses were logged between 2026-08-05 and 2026-09-30, for $2,665.00 in all.
- A dehumidifier and a plumber visit in August were paid; the plumber notes "no response" from the landlord.
- The ceiling repair is awaiting the landlord: Ceiling repair, bathroom.
- Mold cleanup supplies bought for the repair were refused.
"""

EXPENSE_CITATIONS: list[Spec] = [
    unsourced("Expense history: Jordan Avery"),
    # A column: every date, and every amount.
    paraphrase("Four expenses were logged between 2026-08-05 and 2026-09-30, for $2,665.00 in all.",
               at("expenses", CsvLocator(column=3)), at("expenses", CsvLocator(column=7))),
    # Rows: whole expense lines.
    paraphrase("A dehumidifier and a plumber visit in August were paid; the plumber notes",
               at("expenses", CsvLocator(row=1)), at("expenses", CsvLocator(row=2))),
    # Part of a cell, found by search, and the rest of that cell.
    quote("no response", at("expenses", "no response")),
    paraphrase("from the landlord.", at("expenses", "Leak traced to the unit above", whole=True)),
    paraphrase("The ceiling repair is awaiting the landlord", at("expenses", CsvLocator(row=3))),
    # A whole cell.
    quote("Ceiling repair, bathroom", at("expenses", "Ceiling repair, bathroom", whole=True)),
    paraphrase("Mold cleanup supplies bought for the repair were refused.", at("expenses", CsvLocator(row=4))),
]


# A table written with citations: a rollup of the expense log, citing at every granularity a table can.
#   a row:    the header row is the writer's own column names (unsourced)
#   a column: the line numbers the writer assigned (unsourced)
#   a cell:   each code, description, amount and status quotes the expense log's cell
#   a range:  a note quotes part of the expense log's notes cell, and paraphrases the rest
#   in-table: the total rests on the three amounts above it, in this same table
EXPENSE_ROLLUP = """\
line,code,description,amount,status,note
1,R100,Dehumidifier rental,960.00,paid,Rented for the leak
2,R200,"Plumber visit, leak diagnosis",145.00,paid,"Plumber notes ""no response"" from the landlord"
3,R300,"Ceiling repair, bathroom",1250.00,pending,Awaiting landlord response
,TOTAL,,2355.00,,Sum of the amounts above
"""

# The expense log's notes cells (column 9), whose parts the rollup's notes cite.
_PLUMBER_NOTE = 'Leak traced to the unit above; "no response" from landlord'
_REPAIR_NOTE = "Repair and deduct requested.\nAwaiting landlord response."
_ROLLUP_PLUMBER_NOTE = 'Plumber notes "no response" from the landlord'


def _part(cell: str, phrase: str) -> Span:
    start = cell.index(phrase)
    return start, start + len(phrase)


ROLLUP_CITATIONS: list[Spec] = [
    unsourced(CsvLocator(row=0)),
    unsourced(CsvLocator(column=0)),
    *(quote(CsvLocator(r, c), at("expenses", CsvLocator(r, column)))
      for r in (1, 2, 3) for c, column in zip((1, 2, 3, 4), (4, 5, 7, 8), strict=True)),
    paraphrase(CsvLocator(1, 5), at("expenses", CsvLocator(row=1))),
    paraphrase(CsvLocator(2, 5, _part(_ROLLUP_PLUMBER_NOTE, "Plumber notes")), at("expenses", CsvLocator(2, 9))),
    quote(CsvLocator(2, 5, _part(_ROLLUP_PLUMBER_NOTE, "no response")),
          at("expenses", CsvLocator(2, 9, _part(_PLUMBER_NOTE, "no response")))),
    paraphrase(CsvLocator(2, 5, _part(_ROLLUP_PLUMBER_NOTE, "from the landlord")),
               at("expenses", CsvLocator(2, 9, _part(_PLUMBER_NOTE, "from landlord")))),
    quote(CsvLocator(3, 5, (0, len("Awaiting landlord response"))),
          at("expenses", CsvLocator(3, 9, _part(_REPAIR_NOTE, "Awaiting landlord response")))),
    unsourced(CsvLocator(4, 1)),
    paraphrase(CsvLocator(4, 3), *(at(THIS, CsvLocator(r, 3)) for r in (1, 2, 3))),
    unsourced(CsvLocator(4, 5)),
]


REQUEST = "Request: repair and deduct for a bathroom ceiling leak (HC 12.4), referred by Riverside Tenants Union."


def summary_citations(bond: Bond, docs: Mapping[str, VersionId], expenses: VersionId | None = None) -> list[Citation]:
    """The summary's citations; with the expense log, its request also rests on the log's ceiling repair line."""
    citations = build_citations(bond, docs)
    if expenses is None:
        return citations
    request = claim_locator(SUMMARY, REQUEST)
    return [Citation(c.kind, c.claim, [*c.supports, Ref(expenses, CsvLocator(row=3))], c.attrs) if c.claim == request
            else c for c in citations]


def ingest(bond: Bond) -> dict[str, VersionId]:
    """Ingest case 001's sources and import the fax's OCR output. Returns document -> version."""
    pdf = (FIXTURES / "fax_case_001.pdf").read_bytes()
    ocr = (FIXTURES / "fax_case_001.reducto.json").read_bytes()
    imported = import_reducto(bond, pdf, ocr, "fax_case_001")
    policy = bond.ingest("policy_ip112", FIXTURES / "policy_ip112.md")
    return {"pdf": imported.pdf.version, "fax": imported.parse.version, "policy": policy.version}


def ingest_expenses(bond: Bond) -> VersionId:
    """Ingest the client's expense log, a CSV named by its suffix."""
    return bond.ingest("expenses_case_001", FIXTURES / "expenses_case_001.csv").version


def claim_locator(content: str, phrase: str) -> TextLocator:
    start = content.find(phrase)
    if start == -1 or content.find(phrase, start + 1) != -1:
        raise ValueError(f"claim phrase must occur exactly once: {phrase!r}")
    return TextLocator(start, start + len(phrase))


def support_ref(bond: Bond, version: VersionId, phrase: str | Locator, whole: bool) -> Ref:
    """The span a phrase matches in version (whole: the entire value or cell it is in), or a locator as given."""
    if not isinstance(phrase, str):
        return Ref(version, phrase)
    # Only matches whose support reaches declared sources: an OCR parse also holds unsourced copies of its text.
    hits = [h for h in bond.search(phrase, within=Ref(version, VersionLocator()))
            if bond.check_support(h.ref).status is SupportStatus.SUPPORTED]
    if len(hits) != 1:
        raise ValueError(f"support phrase must match exactly once, got {len(hits)}: {phrase!r}")
    ref = hits[0].ref
    if whole and isinstance(ref.locator, (JsonLocator, CsvLocator)):
        return Ref(ref.version, replace(ref.locator, span=None))
    return ref


def build_citations(bond: Bond, docs: Mapping[str, VersionId], content: str = SUMMARY,
                    specs: Sequence[Spec] = CITATIONS) -> list[Citation]:
    """Citations from specs: claims and THIS supports are phrases of content (or locators in it); other supports
    are phrases found in docs, by key."""
    def claim(c: str | Locator) -> Locator:
        return claim_locator(content, c) if isinstance(c, str) else c

    def support(s: Cited) -> Support:
        if s.document == THIS:
            return SelfRef(claim(s.at))
        return support_ref(bond, docs[s.document], s.at, s.whole)

    return [Citation(spec.kind, claim(spec.claim), [support(s) for s in spec.supports]) for spec in specs]


def leaves(trace: Trace) -> list[Ref]:
    """The roots of a trace: nodes with no edge that rests on anything further."""
    return [n for n in trace.nodes if not any(e.citation.supports for e in trace.edges_at(n))]


def print_trace(bond: Bond, trace: Trace, node: Ref | None = None, indent: int = 0) -> None:
    node = node or trace.root
    pad = "  " * indent
    name = details.filed_in(bond, node.version)[0].name
    loc = node.locator
    if isinstance(loc, PdfLocator):
        where = f"page {loc.page + 1}, bbox {[round(v, 3) for v in loc.bbox]}"
    else:
        text = " ".join((bond.text(node) or "").split())
        quoted = repr(text[:70] + ("…" if len(text) > 70 else "")) if text else ""
        # A table's cell says where it is too; a row or column has no single text, only a place.
        where = (", ".join(p for p in (details.where(bond, node), quoted) if p) if isinstance(loc, CsvLocator)
                 else quoted or str(loc.to_dict()))
    print(f"{pad}{name}: {where}")
    for e in trace.edges_at(node):
        print(f"{pad}  └─ {e.citation.kind.value}")
        for s in e.citation.supports:
            print_trace(bond, trace, s, indent + 2)


def main() -> int:
    with tempfile.TemporaryDirectory() as root, Bond.local(root) as bond:
        docs = ingest(bond)
        result = bond.write_document("summary_case_001", SUMMARY.encode(), build_citations(bond, docs), format=Format.MARKDOWN)
        print(f"wrote summary {result.version[:12]}… with {len(bond.citations(result.version))} citations, "
              "every word accounted for\n")

        ref = Ref(result.version, claim_locator(SUMMARY, "No significant damage to other rooms."))
        print_trace(bond, bond.trace(ref))
        print()
        ref = Ref(result.version, claim_locator(SUMMARY, "Six weeks of letters and calls, with no response"))
        print_trace(bond, bond.trace(ref))

        expenses = ingest_expenses(bond)
        history = bond.write_document(
            "expense_history_case_001",
            EXPENSE_HISTORY.encode(),
            build_citations(bond, {"expenses": expenses}, EXPENSE_HISTORY, EXPENSE_CITATIONS),
            format=Format.MARKDOWN,
        )
        print(f"\nwrote expense history {history.version[:12]}… citing a CSV by cell, row and column\n")
        for phrase in ("Four expenses were logged between 2026-08-05 and 2026-09-30, for $2,665.00 in all.",
                       "no response", "The ceiling repair is awaiting the landlord"):
            print_trace(bond, bond.trace(Ref(history.version, claim_locator(EXPENSE_HISTORY, phrase))))
            print()

        rollup = bond.write_document(
            "expense_rollup_case_001",
            EXPENSE_ROLLUP.encode(),
            build_citations(bond, {"expenses": expenses}, EXPENSE_ROLLUP, ROLLUP_CITATIONS),
            format=Format.CSV,
        )
        print("wrote an expense rollup table, cited by row, column, cell and part of a cell; as an agent reads it:\n")
        print(Tools(bond).read("expense_rollup_case_001").text)
        print_trace(bond, bond.trace(Ref(rollup.version, CsvLocator(4, 3))))  # the total
    return 0


if __name__ == "__main__":
    sys.exit(main())
