"""Policies: what a version must meet beyond integrity, configurable per check.

Integrity (well-formed content, valid locators, kind rules, no support loops)
is always checked and never configurable: a version that fails it is corrupt,
not lenient. A policy decides the rest, and it is plain data: whether words may
be left uncovered, and which citation kinds are allowed.

A caller selects the policy at write time and independently at verification
time. Policies are not stored; verification applies current rules and the
policy supplied now. Coverage describes declarations, not factual correctness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .locators import Locator
from .models import Citation, CitationKind, Issue, IssueCode
from .normalize import clip
from .parsers import ParsedDoc


@dataclass(frozen=True)
class Policy:
    """What a version must meet beyond integrity.

    gaps: words may be left uncovered (they stay gaps, never unsourced). kinds: the citation kinds allowed.

    A policy judges a version alone, never where it is filed: so verify under the same policy gives the same
    answer whenever it runs. Whether a citation's source has a newer version since is a warning at write
    (cites_superseded), and a question for the application, not a rule here.
    """

    gaps: bool = False
    kinds: frozenset[CitationKind] = frozenset(CitationKind)

    def issues(self, doc: ParsedDoc, citations: Sequence[tuple[int, Citation]]) -> list[Issue]:
        """The issues this policy finds with a version's parsed content and its citations whose locators are
        valid, each with its index among the citations written."""
        found: list[Issue] = []
        if not self.gaps:
            found += [_uncovered(doc, gap) for gap in doc.gaps([c.claim for _, c in citations])]
        names = ", ".join(sorted(k.value for k in self.kinds))
        found += [
            Issue(IssueCode.KIND_NOT_ALLOWED, f"{c.kind.value} citations are not allowed here", citation_index=i,
                  locator=c.claim, hint=f"use one of: {names}")
            for i, c in citations if c.kind not in self.kinds
        ]
        return found


STRICT = Policy()  # every word is accounted for: the default
ALLOW_GAPS = Policy(gaps=True)  # for drafts and migrations


def _uncovered(doc: ParsedDoc, gap: Locator) -> Issue:
    return Issue(
        IssueCode.UNCOVERED_CLAIM, f"no citation covers {clip(doc.text(gap) or '')!r}", locator=gap,
        hint="cite it with quote or paraphrase, mark it source to declare an origin of evidence, "
             "mark it unsourced if no supporting evidence is declared, or remove it",
    )
