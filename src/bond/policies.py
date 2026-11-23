"""Policies: what a version must meet beyond integrity, configurable per check.

Integrity (well-formed content, valid locators, kind rules, no support loops)
is always checked and never configurable: a version that fails it is corrupt,
not lenient. A policy decides the rest, and it is plain data: whether words may
be left uncovered, which citation kinds are allowed, and which producers a
citation may name.

A caller selects the policy at write time and independently at verification
time. Policies are not stored; verification applies current rules and the
policy supplied now. Coverage describes declarations, not factual correctness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import kinds
from .locators import Locator
from .models import Citation, CitationKind, Issue, IssueCode
from .normalize import clip
from .parsers import ParsedDoc

# The producers a policy knows unless it says otherwise: the importers Bond ships.
PRODUCERS = frozenset({"pypdf", "reducto"})


@dataclass(frozen=True)
class Policy:
    """What a version must meet beyond integrity.

    gaps: words may be left uncovered (they stay gaps, never unsourced). kinds: the citation kinds allowed.
    producers: the tools a transcription, classification or assessment may name as its producer, by id; an
    importer of another vendor's output adds its own, e.g. Policy(producers=PRODUCERS | {"acme-ocr"}).
    """

    gaps: bool = False
    kinds: frozenset[CitationKind] = frozenset(CitationKind)
    producers: frozenset[str] = PRODUCERS

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
        known = ", ".join(sorted(self.producers)) or "none"
        found += [
            Issue(IssueCode.UNKNOWN_PRODUCER, f"{c.kind.value} names producer {named!r}, which this policy doesn't know",
                  citation_index=i, locator=c.claim, hint=f"known producers: {known}")
            for i, c in citations if (named := kinds.producer(c)) is not None and named not in self.producers
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
