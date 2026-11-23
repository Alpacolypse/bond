"""Regenerate the UI's mock store (ui/src/mocks/store.json and fax.pdf) from the real Bond library.

The mock store is a snapshot of raw data: versions, their claim units and gaps, and their citations
in the API's Citation shape. `src/mocks/server.ts` answers every API route from it in the browser.

Run from the repository root: uv run python ui/scripts/make_mock.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples"))

from bond import (  # noqa: E402
    ALLOW_GAPS,
    Bond,
    Citation,
    CitationKind,
    Format,
    Ref,
    SelfRef,
    VersionId,
    VersionLocator,
    details,
)
from bond.http import viewer  # noqa: E402
from case_001 import (  # noqa: E402
    EXPENSE_CITATIONS,
    EXPENSE_HISTORY,
    EXPENSE_ROLLUP,
    ROLLUP_CITATIONS,
    SUMMARY,
    build_citations,
    claim_locator,
    ingest,
    ingest_expenses,
    retitle,
    summary_citations,
)

OUT = ROOT / "ui" / "src" / "mocks"

# A reviewed copy of the summary, as its own document: a heading with an astral character (to exercise code point
# offsets), a reviewer's own note (a SOURCE region), a conclusion resting on the document's own
# table (self-supports), and a draft paragraph left uncited.
NOTE = "Reviewer note: repair and deduct is reasonable given the ongoing leak."
CONCLUSION = "All three criteria are met."
DRAFT = "Follow-up call with the landlord is pending."
REVIEWED = (
    SUMMARY.replace("# Intake summary", "# \U0001f4c4 Intake summary", 1)
    + f"\n## Decision\n\n{CONCLUSION} {NOTE}\n\n{DRAFT}\n"
)
TABLE_ROWS = ("| Repair not made |", "| At least 6 weeks of written notice |", "| No emergency |")


def reviewed_citations(bond: Bond, docs: dict[str, VersionId]) -> list[Citation]:
    rows = [claim_locator(REVIEWED, row) for row in TABLE_ROWS]
    return build_citations(bond, docs, content=REVIEWED) + [
        Citation(CitationKind.PARAPHRASE, claim_locator(REVIEWED, CONCLUSION), [SelfRef(r) for r in rows]),
        Citation(CitationKind.SOURCE, claim_locator(REVIEWED, NOTE), []),
        Citation(CitationKind.UNSOURCED, claim_locator(REVIEWED, "Decision"), []),
    ]


def main() -> int:
    with tempfile.TemporaryDirectory() as root, Bond.local(root) as bond:
        docs = ingest(bond)
        expenses = ingest_expenses(bond)  # a CSV, for the table view; the summary's request rests on it too
        bond.write_document(
            "summary_case_001",
            SUMMARY.encode(),
            summary_citations(bond, docs, expenses),
            format=Format.MARKDOWN,
        )
        bond.write_document(
            "summary_case_001_reviewed",
            REVIEWED.encode(),
            reviewed_citations(bond, docs),
            format=Format.MARKDOWN,
            policy=ALLOW_GAPS,
        )
        bond.write_document(
            "expense_history_case_001",
            EXPENSE_HISTORY.encode(),
            build_citations(bond, {"expenses": expenses}, EXPENSE_HISTORY, EXPENSE_CITATIONS),
            format=Format.MARKDOWN,
        )
        bond.write_document(
            "expense_rollup_case_001",
            EXPENSE_ROLLUP.encode(),
            build_citations(bond, {"expenses": expenses}, EXPENSE_ROLLUP, ROLLUP_CITATIONS),
            format=Format.CSV,
        )
        retitle(bond)

        versions, citations = [], []
        summaries = viewer.Summaries(bond)
        for version in details.versions(bond):
            summary = summaries.summary(version)
            versions.append({
                "version": version.id, "content_hash": version.content_hash, "name": summary["name"],
                "title": summary["title"], "documents": summary["documents"], "format": version.format.value,
                "created_at": version.created_at,
                "content": None if version.format is Format.PDF else bond.text(Ref(version.id, VersionLocator())),
                # Each unit with its text as the server computes it, so the mock needn't parse every format.
                "units": [{"locator": viewer.shown(bond, u), "text": bond.text(u)} for u in details.claim_units(bond, version.id)],
                "gaps": viewer.claim_annotations(bond, version.id)["gaps"],
            })
            citations += viewer.citations(bond, bond.citations(version.id))
            if version.format is Format.PDF:
                (OUT / "fax.pdf").write_bytes(bond.blob(version.id))

    (OUT / "store.json").write_text(
        json.dumps({"versions": versions, "citations": citations}, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(versions)} versions and {len(citations)} citations to {OUT / 'store.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
