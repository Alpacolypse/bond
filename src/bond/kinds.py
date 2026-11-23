"""Citation kinds. Each kind is an enum member plus a validator.

A validator checks a citation as strongly as its kind honestly allows: always
its structure (supports, locator types, attrs), and for QUOTE its content.
Vendor values are never copied into attrs: a vendor's label or score is a value
in its own document, cited with CLASSIFICATION or ASSESSMENT.
A kind's meaning never changes once added; a changed meaning is a new kind.

Attrs are stored with the citation, so they never hold free text: the only
attrs are the producers of transcriptions, classifications and assessments,
each named by an id. Which producers are known is a policy's business
(Policy.producers). Stored records hold no content.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .locators import Addresses, VersionLocator, names_of
from .models import Citation, CitationKind, Issue, IssueCode, SelfRef, Support
from .normalize import clip, normalize

# Text at a support, or at a claim given as a SelfRef; None if it addresses none.
TextAt = Callable[[Support], str | None]

_TEXT = names_of(Addresses.TEXT)  # e.g. "text, json or csv", for messages
_REGION = names_of(Addresses.REGION)
_CITE_THE_TRANSCRIPTION = "cite the text transcribed from a region of a page, not the region itself"

# A producer's id: a lowercase letter followed by lowercase letters, digits and hyphens. Never free text.
_PRODUCER_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")


@dataclass(frozen=True)
class _Problem:
    """A problem a kind finds with a citation; validate_citation makes it an Issue about that citation."""

    code: IssueCode
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class KindSpec:
    description: str
    producer: str | None  # the one attr the kind takes, naming the tool that produced the claim; None if it takes none
    validate: Callable[[Citation, TextAt], list[_Problem]]


def _invalid(message: str, hint: str | None = None) -> _Problem:
    return _Problem(IssueCode.INVALID_CITATION, message, hint)


def _check_claim_is_text(c: Citation, text_at: TextAt) -> list[_Problem]:
    if c.claim.addresses is not Addresses.TEXT:
        return [_invalid(f"{c.kind.value} claim must be a {_TEXT} locator, got {c.claim.type.value}")]
    if text_at(SelfRef(c.claim)) is None:
        return [_invalid(f"{c.kind.value} claim must address text, but its locator holds none",
                         "point at a string value or a range inside one")]
    return []


def _check_one_region_support(c: Citation) -> list[_Problem]:
    """Exactly one support, addressing a region of a page: what a transcription reads, or a classification labels."""
    if len(c.supports) != 1:
        return [_invalid(f"{c.kind.value} needs exactly 1 support, got {len(c.supports)}")]
    locator = c.supports[0].locator
    if locator.addresses is not Addresses.REGION:
        return [_invalid(f"{c.kind.value} support must be a region of a page (a {_REGION} locator), "
                         f"got {locator.type.value}")]
    return []


def _check_supports_are_text(c: Citation, hint: str | None = None) -> list[_Problem]:
    return [
        _invalid(f"{c.kind.value} support {i} must be a {_TEXT} locator, got {s.locator.type.value}", hint)
        for i, s in enumerate(c.supports)
        if s.locator.addresses is not Addresses.TEXT
    ]


def _validate_region_reading(c: Citation, text_at: TextAt) -> list[_Problem]:
    """A transcription or classification: text a producer read from, or a label it gave, a region of a page."""
    return _check_claim_is_text(c, text_at) + _check_one_region_support(c)


def _validate_quote(c: Citation, text_at: TextAt) -> list[_Problem]:
    problems = _check_claim_is_text(c, text_at)
    if len(c.supports) != 1:
        return problems + [_invalid(f"quote needs exactly 1 support, got {len(c.supports)}",
                                    "cite several sources with a paraphrase")]
    support = c.supports[0]
    if support.locator.addresses is not Addresses.TEXT:
        return problems + [_invalid(f"quote support must be a {_TEXT} locator, got {support.locator.type.value}",
                                    _CITE_THE_TRANSCRIPTION)]
    support_text = text_at(support)
    if support_text is None:
        return problems + [_invalid("quote support must address text, but its locator holds none")]
    claim_text = text_at(SelfRef(c.claim))
    if problems or claim_text is None:
        return problems
    if normalize(claim_text, fold_case=False).text != normalize(support_text, fold_case=False).text:
        problems.append(_Problem(
            IssueCode.QUOTE_MISMATCH,
            f"quote claim {clip(claim_text)!r} does not match its support {clip(support_text)!r}",
            "quote verbatim (whitespace, curly quotes and line-break hyphens are forgiven), or use paraphrase",
        ))
    return problems


def _validate_paraphrase(c: Citation, text_at: TextAt) -> list[_Problem]:
    problems = _check_claim_is_text(c, text_at)
    if not c.supports:
        problems.append(_invalid("paraphrase needs at least 1 support"))
    return problems + _check_supports_are_text(c, _CITE_THE_TRANSCRIPTION)


def _validate_unsourced(c: Citation, text_at: TextAt) -> list[_Problem]:
    problems = []
    if c.supports:
        problems.append(_invalid(f"unsourced takes no supports, got {len(c.supports)}",
                                 "cite content that rests on a source with quote, paraphrase or transcription"))
    if isinstance(c.claim, VersionLocator):
        problems.append(_invalid("unsourced claim cannot be the whole version",
                                 "use an explicit text or value locator for regions with no declared support"))
    return problems


def _validate_source(c: Citation, text_at: TextAt) -> list[_Problem]:
    problems = []
    if c.supports:
        problems.append(_invalid(f"source takes no supports, got {len(c.supports)}",
                                 "a source region is a declared origin of evidence; cite what rests on other versions separately"))
    return problems


def _validate_assessment(c: Citation, text_at: TextAt) -> list[_Problem]:
    problems = _check_claim_is_text(c, text_at)
    if not c.supports:
        problems.append(_invalid("assessment needs at least 1 support"))
    return problems + _check_supports_are_text(c, "an assessment judges text, e.g. a transcriber's confidence in the "
                                                  "text it read")


KINDS: dict[CitationKind, KindSpec] = {
    CitationKind.TRANSCRIPTION: KindSpec(
        "claim is text read from a visual region of a page, e.g. of a PDF", "transcriber", _validate_region_reading,
    ),
    CitationKind.QUOTE: KindSpec("claim is the supporting text, verbatim", None, _validate_quote),
    CitationKind.PARAPHRASE: KindSpec(
        "writer declares the text supports the claim; semantic support is not evaluated", None, _validate_paraphrase,
    ),
    CitationKind.UNSOURCED: KindSpec(
        "no supporting evidence is declared for the claim, e.g. a heading, id or vendor metadata", None,
        _validate_unsourced,
    ),
    CitationKind.SOURCE: KindSpec(
        "claim is a declared origin of evidence, without an assertion of authority: a whole file or a "
        "reviewer's own rationale", None, _validate_source,
    ),
    CitationKind.CLASSIFICATION: KindSpec(
        "claim is a label a producer assigned to a visual region of a page, e.g. a block type", "classifier",
        _validate_region_reading,
    ),
    CitationKind.ASSESSMENT: KindSpec(
        "claim is a producer's judgment of the text it rests on, e.g. a transcriber's confidence in the text it read",
        "assessor", _validate_assessment,
    ),
}


def validate_citation(c: Citation, index: int, text_at: TextAt) -> list[Issue]:
    """Every problem with citation index's attrs, supports and claim, by the rules of its kind."""
    spec = KINDS[c.kind]
    return [Issue(p.code, p.message, citation_index=index, locator=c.claim, hint=p.hint)
            for p in _check_attrs(c, spec) + spec.validate(c, text_at)]


def producer(c: Citation) -> str | None:
    """The producer a citation names, if its kind names one and it does."""
    attr = KINDS[c.kind].producer
    return None if attr is None else c.attrs.get(attr)


def _check_attrs(c: Citation, spec: KindSpec) -> list[_Problem]:
    hint = f"{c.kind.value}: {spec.description}"
    expected = set() if spec.producer is None else {spec.producer}
    problems = []
    if missing := expected - c.attrs.keys():
        problems.append(_invalid(f"{c.kind.value} requires attrs {sorted(missing)}", hint))
    if extra := c.attrs.keys() - expected:
        problems.append(_invalid(f"{c.kind.value} does not accept attrs {sorted(extra)}", hint))
    if spec.producer is not None and (named := c.attrs.get(spec.producer)) is not None \
            and not _PRODUCER_ID.fullmatch(named):
        problems.append(_invalid(f"attrs.{spec.producer} must be a producer id, got {clip(named)!r}",
                                 "a tool's id: a lowercase letter followed by up to 62 lowercase letters, digits or "
                                 "hyphens, e.g. 'reducto'; attrs never hold free text"))
    return problems
