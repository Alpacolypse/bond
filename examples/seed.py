"""Seed the viewer demo: separate case workspaces and shared intake policies.

Every citation kind and every shape of provenance appears at least once, in as
few documents as that takes: a summary quoting and paraphrasing OCR text and an
expense log, a JSON intake decision mixing a reviewer's own SOURCE regions with
cited facts and an unsourced id, a review whose conclusion rests on its own
findings, a reconsideration review drafted with a gap that quotes the decision and
records the reviewer's own finding, a note citing a CSV by cell, row and column,
and a CSV written with citations at every granularity. Every document has a
single version.

Run with: uv run python examples/seed.py --home .bond-demo [--force]; the demo is a Bond home, a workspace
per case and one for the policies they link to and cite.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bond import (
    ALLOW_GAPS,
    STRICT,
    Bond,
    CitationKind,
    Format,
    JsonLocator,
    Policy,
    Ref,
    VersionId,
    VersionLocator,
    details,
)
from bond.importers.reducto import import_reducto
from bond.jsonvalue import JsonObject
from bond.local import DECISIONS, Home
from bond.locators import format_pointer
from bond.models import WriteResult
from bond.parsers import JsonDoc, Malformed
from case_001 import (
    EXPENSE_CITATIONS,
    EXPENSE_HISTORY,
    EXPENSE_ROLLUP,
    ROLLUP_CITATIONS,
    THIS,
    Cited,
    Spec,
    at,
    build_citations,
    ingest_expenses,
    paraphrase,
    quote,
    retitle,
    source,
    summary_citations,
    unsourced,
)
from case_001 import SUMMARY as SUMMARY_001

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

REVIEWER = "Lena Okoro, Supervising Attorney"


def heading(text: str) -> Spec:
    """A section heading with no supporting evidence declared: every word is accounted for."""
    return unsourced(text)


def title(text: str, fax: str, client: str) -> Spec:
    """A title naming the client, which rests on the fax's client line."""
    return paraphrase(text, at(fax, f"Client: {client}", whole=True))


# --- case 003 ------------------------------------------------------------------

REVIEW_003 = """\
# Intake review: Samuel Okafor

## Findings

- The window was damaged in a storm on 09/07/2026, four weeks before the visit.
- 2 weeks of written notice and a portal message to the property manager.
- City inspection request filed; inspection scheduled for 10/14/2026.
- No mold present.

## Conclusion

Written notice so far has lasted about 2 weeks, short of the 6 weeks that policy IP-112 requires, and no mold is present, so conditions are not worsening.
"""

CITATIONS_REVIEW_003: list[Spec] = [
    title("Intake review: Samuel Okafor", "fax_003", "Samuel Okafor"),
    heading("Findings"),
    heading("Conclusion"),
    paraphrase("The window was damaged in a storm on 09/07/2026, four weeks before the visit.",
               at("fax_003", "Damage after a storm on 09/07/2026", whole=True),
               at("fax_003", "cracked and will not close for 4 weeks", whole=True)),
    quote("2 weeks of written notice and a portal message to the property manager.",
          at("fax_003", "2 weeks of written notice and a portal message to the property manager.")),
    quote("City inspection request filed; inspection scheduled for 10/14/2026.",
          at("fax_003", "City inspection request filed; inspection scheduled for 10/14/2026.")),
    quote("No mold present.", at("fax_003", "No mold present.")),
    paraphrase("Written notice so far has lasted about 2 weeks, short of the 6 weeks that policy IP-112 "
               "requires, and no mold is present, so conditions are not worsening.",
               at(THIS, "2 weeks of written notice and a portal message"),
               at(THIS, "City inspection request filed"),
               at(THIS, "No mold present."),
               at("policy_112", "A needed repair still not made after at least 6 weeks of written notice to the "
                                "landlord."),
               at("policy_112", "Worsening conditions or a serious threat to health or safety.")),
]

# The reviewer's own record: primary throughout, except what it quotes from the decision and the policy.
# A draft: the recommendation is not cited yet, so it is stored as a gap.
RECONSIDERATION_003 = """\
# Reconsideration review (draft): Samuel Okafor

The decision found that "Notice is recent and the city inspection is still pending."

On a follow-up call on 10/12/2026, the tenant reported new black mold on the bedroom wall below the window.

New mold after an inspection that found none is worsening, which meets the policy's "Worsening conditions or a serious threat to health or safety." criterion.

Recommend accepting the case.
"""

CITATIONS_RECONSIDERATION_003: list[Spec] = [
    title("Reconsideration review (draft): Samuel Okafor", "fax_003", "Samuel Okafor"),
    paraphrase("The decision found that", at("determination_003", "declined", whole=True)),
    quote("Notice is recent and the city inspection is still pending.",
          at("determination_003", "Notice is recent and the city inspection is still pending.")),
    # The reviewer's own record of the call: a declared source.
    source("On a follow-up call on 10/12/2026, the tenant reported new black mold on the bedroom wall below the "
           "window."),
    paraphrase("New mold after an inspection that found none is worsening, which meets the policy's",
               at(THIS, "new black mold on the bedroom wall below the window"),
               at("review_003", "no mold is present")),
    quote("Worsening conditions or a serious threat to health or safety.",
          at("policy_112", "Worsening conditions or a serious threat to health or safety.")),
    unsourced("criterion."),
]

# --- determinations ----------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    text: str  # quoted from the policy
    evidence: str  # the reviewer's paraphrase of the record
    supports: tuple[Cited, ...]
    met: bool


@dataclass(frozen=True)
class Determination:
    id: str
    fax: str
    policy: str
    client: str
    request: str
    policy_name: str
    criteria: list[Criterion]
    decision: str
    rationale: str


DETERMINATIONS = {
    "determination_003": Determination(
        "INT-2026-10-0003", "fax_003", "policy_112", "Samuel Okafor", "repair and deduct for a bedroom window (HC 12.6)",
        "Intake Policy IP-112",
        [Criterion("A needed repair still not made after at least 6 weeks of written notice to the landlord.",
                   "Written notice has lasted about 2 weeks, and the city inspection has not happened.",
                   (at("review_003", "Written notice so far has lasted about 2 weeks"),
                    at("fax_003", "City inspection request filed", whole=True)), False),
         Criterion("Worsening conditions or a serious threat to health or safety.",
                   "The sill is damp but there is no mold; nothing is getting worse.",
                   (at("review_003", "no mold is present"),
                    at("fax_003", "Sill damp on the right side.")), False)],
        "declined",
        "Notice is recent and the city inspection is still pending. The case can be reconsidered if the window is "
        "still not repaired after the inspection or conditions worsen.",
    ),
}


def determination(d: Determination) -> tuple[str, list[Spec]]:
    """An intake decision as JSON, with its citations: facts cite the record, judgments are the reviewer's own."""
    doc: JsonObject = {
        "determination_id": d.id,
        "client": d.client,
        "request": d.request,
        "policy": d.policy_name,
        "criteria": [{"criterion": c.text, "evidence": c.evidence, "met": c.met} for c in d.criteria],
        "decision": d.decision,
        "rationale": d.rationale,
        "reviewer": REVIEWER,
    }
    content = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"  # the layout authoring stores JSON in
    parsed = JsonDoc.parse(content)
    if isinstance(parsed, Malformed):
        raise ValueError(f"the determination isn't valid JSON: {parsed.reason}")

    def value(*path: str | int) -> JsonLocator:
        """A value of the determination by its keys, as the positional locator Bond stores."""
        located = parsed.locate(format_pointer([str(p) for p in path]))
        if located is None:
            raise KeyError(f"the determination has no value at {path}")
        return located

    specs = [
        unsourced(value("determination_id")),  # an id assigned by the review system
        quote(value("client"), at(d.fax, d.client)),
        quote(value("request"), at(d.fax, d.request)),
        quote(value("policy"), at(d.policy, d.policy_name)),
    ]
    for i, c in enumerate(d.criteria):
        specs += [quote(value("criteria", i, "criterion"), at(d.policy, c.text)),
                  paraphrase(value("criteria", i, "evidence"), *c.supports),
                  source(value("criteria", i, "met"))]
    specs += [source(value(key)) for key in ("decision", "rationale", "reviewer")]
    return content, specs


# --- building ----------------------------------------------------------------------


def write(bond: Bond, docs: dict[str, VersionId], key: str, name: str, content: str, specs: list[Spec],
          fmt: Format = Format.MARKDOWN, policy: Policy = STRICT) -> WriteResult:
    citations = build_citations(bond, docs, content, specs)
    result = bond.write_document(name, content.encode(), citations, format=fmt, policy=policy)
    docs[key] = result.version
    return result


def import_fax(bond: Bond, docs: dict[str, VersionId], case: str, ocr: Path) -> None:
    pdf = (FIXTURES / f"fax_case_{case}.pdf").read_bytes()
    imported = import_reducto(bond, pdf, ocr.read_bytes(), f"fax_case_{case}")
    docs[f"pdf_{case}"] = imported.pdf.version
    docs[f"fax_{case}"] = imported.parse.version


def seed(bond: Bond, case: str = "001", policies: Bond | None = None) -> dict[str, VersionId]:
    """Build one case only. With a policies workspace, the case links to it and cites its policy where it is;
    without one, the case holds the policy itself."""
    if case not in ("001", "003"):
        raise ValueError(f"unknown demo case {case!r}")
    if policies is not None:
        bond.set_links([policies])
        policy = policies.document("policy_ip112").head
    else:
        policy = bond.ingest("policy_ip112", FIXTURES / "policy_ip112.md").version
    docs = {"policy_112": policy}
    import_fax(bond, docs, case, FIXTURES / f"fax_case_{case}.reducto.json")
    if case == "001":
        seed_expenses(bond, docs)
    else:
        seed_review(bond, docs)
    retitle(bond)
    return docs


def seed_expenses(bond: Bond, docs: dict[str, VersionId]) -> None:
    case_001 = {"pdf": docs["pdf_001"], "fax": docs["fax_001"], "policy": docs["policy_112"]}
    docs["expenses_001"] = ingest_expenses(bond)  # a CSV, cited by cell, row and column
    docs["summary_001"] = bond.write_document(
        "summary_case_001",
        SUMMARY_001.encode(),
        summary_citations(bond, case_001, docs["expenses_001"]),
        format=Format.MARKDOWN,
    ).version
    docs["expense_history_001"] = bond.write_document(
        "expense_history_case_001",
        EXPENSE_HISTORY.encode(),
        build_citations(bond, {"expenses": docs["expenses_001"]}, EXPENSE_HISTORY, EXPENSE_CITATIONS),
        format=Format.MARKDOWN,
    ).version
    docs["expense_rollup_001"] = bond.write_document(
        "expense_rollup_case_001",
        EXPENSE_ROLLUP.encode(),
        build_citations(bond, {"expenses": docs["expenses_001"]}, EXPENSE_ROLLUP, ROLLUP_CITATIONS),
        format=Format.CSV,
    ).version


def seed_review(bond: Bond, docs: dict[str, VersionId]) -> None:
    write(bond, docs, "review_003", "review_case_003", REVIEW_003, CITATIONS_REVIEW_003)
    for key, d in DETERMINATIONS.items():
        content, specs = determination(d)
        write(bond, docs, key, key.replace("_", "_case_", 1), content, specs, Format.JSON)
    write(bond, docs, "reconsideration_003", "reconsideration_case_003", RECONSIDERATION_003,
          CITATIONS_RECONSIDERATION_003, policy=ALLOW_GAPS)


def print_summary(bond: Bond, root: Path, docs: dict[str, VersionId]) -> None:
    versions = {details.version(bond, v) for v in docs.values()}
    kinds = Counter(c.kind.value for version in versions for c in bond.citations(version.id))
    gaps = sum(len(bond.declarations(Ref(version.id, VersionLocator())).undeclared)
               for version in versions if version.format is not Format.PDF)
    documents = sorted(bond.documents(), key=lambda d: d.name)
    print(f"seeded {root}: {len(versions)} versions in {len(documents)} documents, "
          f"{sum(kinds.values())} citations, {gaps} gap(s)\n")
    for d in documents:
        n = len(bond.history(d.name))
        print(f"  {d.name:<28} {details.version(bond, d.head).format.value:<9} {n} version{'s' if n > 1 else ''}")
    print()
    for kind in CitationKind:
        print(f"  {kind.value:<15} {kinds[kind.value]}")


STORES = ("case-001", "case-003", "policies")


def prepare(root: Path, force: bool) -> None:
    if root.exists() and any(root.iterdir()):
        if not force:
            raise SystemExit(f"{root} is not empty; pass --force to replace it")
        home = Home(root)
        if {p.name for p in root.iterdir()} - {"workspaces", DECISIONS} or set(home.names()) - set(STORES) or any(
                p.name not in STORES for p in (root / "workspaces").iterdir()):
            raise SystemExit(f"{root} is not a demo Bond home; refusing to delete it")
        shutil.rmtree(root)


def build(root: Path) -> None:
    """A Bond home at root: a workspace per case, and a policies workspace each case links to and cites."""
    home = Home(root)
    with Bond.local(home.path("policies")) as policies:
        policy = policies.ingest("policy_ip112", FIXTURES / "policy_ip112.md")
        retitle(policies)
        for case in ("001", "003"):
            path = home.path(f"case-{case}")
            with Bond.local(path) as bond:
                docs = seed(bond, case, policies)
                print_summary(bond, path, docs)
            home.link(f"case-{case}", "policies")
        print_summary(policies, home.path("policies"), {"policy_112": policy.version})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", dest="root", type=Path, default=Path(".bond-demo"),
                        help="the demo Bond home to write (default: .bond-demo)")
    parser.add_argument("--force", action="store_true", help="replace a demo home from a previous run")
    args = parser.parse_args(argv)
    prepare(args.root, args.force)
    build(args.root)
    print(f"view with: uv run bond view --home {args.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
