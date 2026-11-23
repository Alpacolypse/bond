"""Workspaces: a shared policies workspace, and one workspace per case that imports from it.

1. policies holds the legal aid office's intake policies, and a criteria summary of IP-112 that quotes the policy.
2. Each case is a workspace of its own: its fax and the fax's OCR, and what it imports from policies.
   The host brings case-001 the criteria summary with its provenance, which leaves the policy it quotes
   outside, then brings that in by importing the version. In case-003 the agent imports the policy itself, preserving its source declaration,
   and the summary's provenance then needs nothing more: a version's id is the same in every workspace.
3. An agent in each case, handed the authoring tools with policies as the only workspace it may import from,
   writes an intake decision quoting the fax and the policy.
4. The host, who may open every case, asks each what rests on the policy's six-week criterion.

Run with: uv run python examples/workspaces.py [--root .bond-workspaces] [--force]
then:     uv run bond view --workspace .bond-workspaces/case-001
"""

from __future__ import annotations

import argparse
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path

from bond import Bond, SupportStatus, VersionId, details
from bond.authoring import Tools, Written
from bond.importers.reducto import import_reducto

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CRITERION_TEXT = "A needed repair still not made after at least 6 weeks of written notice to the landlord."
CRITERION = "at least 6 weeks of written notice"

# Each case: its fax, what its OCR says about written notice, and the decision's verdict.
CASES = {
    "case-001": ("fax_case_001", "Sent 6 weeks of written notice", "meets"),
    "case-003": ("fax_case_003", "2 weeks of written notice", "does not yet meet"),
}


def build_policies(root: Path) -> Bond:
    """The policies workspace: two policies, and a criteria summary that quotes IP-112."""
    policies = Bond.local(root / "policies")
    policies.ingest("policy/IP-112", FIXTURES / "policy_ip112.md")
    policies.ingest("policy/IP-140", FIXTURES / "policy_ip140.md")
    tools = Tools(policies)
    [hit] = tools.search(CRITERION_TEXT, document="policy/IP-112")
    draft = (f'<bond:cite kind="unsourced">IP-112, criterion 2:</bond:cite> '
             f'<bond:cite kind="quote" from="{hit.handle}">{CRITERION_TEXT}</bond:cite>\n')
    written = tools.write("criteria/IP-112", draft)
    assert isinstance(written, Written), written
    return policies


def build_case(root: Path, name: str, policies: Bond) -> tuple[Bond, VersionId]:
    """A case's workspace, what it imports, and an agent's determination in it; the determination's version."""
    fax, notice, verdict = CASES[name]
    case = Bond.local(root / name)
    import_reducto(case, (FIXTURES / f"{fax}.pdf").read_bytes(), (FIXTURES / f"{fax}.reducto.json").read_bytes(), fax)
    tools = Tools(case, sources={"policies": policies})

    if name == "case-001":
        case.import_document(policies, "criteria/IP-112")
        outside = case.frontier()
        print(f"  imported criteria/IP-112 with its provenance: {len(outside)} version it rests on stayed outside")
        case.import_version(policies, outside[0])
        tools.import_document("policies", "policy/IP-112")  # the policy, now filed: the same version just brought in
        print(f"  importing the version brought it in; outside now: {case.frontier()}")
    else:
        tools.import_document("policies", "policy/IP-112")
        case.import_document(policies, "criteria/IP-112")
        print(f"  the agent imported policy/IP-112; criteria/IP-112 then came with nothing outside: {case.frontier()}")

    # Every match comes back with its support status; an OCR parse also holds unsourced copies of its text.
    [fact] = [h for h in tools.search(notice, document=f"{fax}.reducto") if h.support is SupportStatus.SUPPORTED]
    [rule] = tools.search(CRITERION, document="policy/IP-112")
    draft = (f'# <bond:cite kind="unsourced">Determination</bond:cite>\n\n'
             f'<bond:cite kind="quote" from="{fact.handle}">{notice}</bond:cite>: '
             f'<bond:cite kind="unsourced">this {verdict} the requirement of</bond:cite> '
             f'<bond:cite kind="quote" from="{rule.handle}">{CRITERION}</bond:cite>.\n')
    written = tools.write("determination", draft)
    assert isinstance(written, Written), written
    version = case.document("determination").head
    issues = case.verify(version)
    print(f"  the agent wrote a determination, every word accounted for ({written.support}); "
          f"verify: {issues or 'intact'}")
    return case, version


def ask_every_case(policies: Bond, cases: dict[str, Bond]) -> dict[str, list[str]]:
    """What rests on the six-week criterion of IP-112, in every case: the same version id everywhere."""
    [span] = policies.search(CRITERION, documents=["policy/IP-112"])
    found = {}
    for name, case in cases.items():
        found[name] = sorted({d.name for c in case.cited_by(span.ref) for d in details.filed_in(case, c.owner_version)})
    return found


def build(root: Path) -> dict[str, list[str]]:
    with ExitStack() as stack:
        print("policies")
        policies = stack.enter_context(build_policies(root))
        print(f"  {', '.join(d.name for d in policies.documents())}")
        cases: dict[str, Bond] = {}
        for name in CASES:
            print(name)
            case, _ = build_case(root, name, policies)
            cases[name] = stack.enter_context(case)
        found = ask_every_case(policies, cases)
        policy = policies.document("policy/IP-112").head
        print(f"\nthe host asks every case what rests on {CRITERION!r} (IP-112, version {policy[:12]}…):")
        for name, documents in found.items():
            print(f"  {name}: {', '.join(documents)}")
        return found


def prepare(root: Path, force: bool) -> None:
    if root.exists() and any(root.iterdir()):
        if not force:
            raise SystemExit(f"{root} is not empty; pass --force to replace it")
        workspaces = {"policies", *CASES}
        if not {p.name for p in root.iterdir()} <= workspaces or not all((root / w / "bond.db").exists()
                                                                          for w in workspaces if (root / w).exists()):
            raise SystemExit(f"{root} holds more than this example's workspaces; refusing to delete it")
        shutil.rmtree(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(".bond-workspaces"),
                        help="directory to build the workspaces in (default: .bond-workspaces)")
    parser.add_argument("--force", action="store_true", help="replace the workspaces a previous run built there")
    args = parser.parse_args(argv)
    prepare(args.root, args.force)
    build(args.root)
    print(f"\nview one with: uv run bond view --workspace {args.root / 'case-001'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
