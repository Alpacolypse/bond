"""The authoring tools: search, read, draft, write and edit a document as text with inline citations, citing
what is in the workspace and in the workspaces it links to.

Each tool is a plain function of a workspace and its arguments; nothing is remembered between calls.
Handles name their spans outright (see handles.py), and the base a write or edit starts from is passed
in explicitly: draft returns it, and a successful write returns the next one. The tools translate; the
core enforces: every write goes through write_document under the policy given.

A write the core would refuse is an Outcome (Refused, with every issue in the writer's terms), never an
error: the writer fixes it and writes again. A ToolError is a call that can't be carried out at all.

Tools binds a workspace and a policy once, so a host can hand an agent tools it can't widen. What the agent can read
besides the workspace is what the workspace links to, which its deployment decides.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from .. import details
from ..api import Bond
from ..formats import spec
from ..locators import Addresses, JsonLocator, Locator, VersionLocator, contains, overlaps
from ..models import (
    BondError,
    Citation,
    CitationKind,
    Format,
    Issue,
    IssueCode,
    Ref,
    SelfRef,
    Support,
    SupportReason,
    SupportStatus,
    Trace,
    VersionId,
    WriteError,
)
from ..normalize import clip
from ..policies import Policy
from .codecs import CODECS, DEFAULT_FORMAT, Codec
from .drafts import Compiled, Entry
from .handles import BadHandle, Pointer, format_handle, parse_handle
from .markup import Unrenderable

# What an agent may write by default: every word covered, and nothing marked as a declared source.
AGENT_KINDS = frozenset({CitationKind.QUOTE, CitationKind.PARAPHRASE, CitationKind.UNSOURCED})
AGENT_POLICY = Policy(kinds=AGENT_KINDS)

SUPPORT_LIMIT = 1000  # references inspected per support check: a match's, a span's or a written version's


class ToolError(BondError):
    """A tool can't do what was asked; the message is meant for whoever called it."""


@dataclass(frozen=True)
class Problem:
    """Why a handle, a base or an edit can't be used, in the writer's terms."""

    message: str


@dataclass(frozen=True)
class Evidence:
    """A span that can be cited, by its handle: its version's id prefix and the span written out."""

    handle: str
    workspace: str  # the workspace its version is filed in: this one, or one it links to
    documents: list[str]  # where the span's version is filed there
    text: str  # exactly the text the handle cites
    support: SupportStatus  # whether everything in it rests, hop by hop, on declared sources; trace() says how
    kinds: list[CitationKind]  # the kinds of the citations overlapping it
    # Documents that hold its version but have moved past it, each with the handle of its whole head now: search
    # that for the same text before citing an older version. Empty when its version is current wherever it is.
    newer: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SearchHit(Evidence):
    """Evidence a search found: which phrase it matched, and where."""

    phrase: str  # the phrase it matched
    where: str  # page, heading path and line, or JSON path
    context: str  # the text around it, with the match marked ▸like this◂
    passage: str | None  # the handle of the larger passage it sits in, if it is inside one


@dataclass(frozen=True)
class Listing:
    """A document in the store: its name, and the format of its current version."""

    document: str
    format: Format


@dataclass(frozen=True)
class Reading:
    """A document as it is, to read in full before citing it: nothing hidden, nothing added."""

    workspace: str  # the workspace it is in: this one, or one it links to
    document: str
    format: Format
    text: str


@dataclass(frozen=True)
class Draft:
    """A document as text to edit: its citations as tags, and the base a write or edit of it starts from."""

    document: str
    text: str  # its draft: its content with its citations written in (tags in text; $bond wrappers in JSON)
    evidence: list[Evidence]  # what the tags' handles point at
    base: str  # the handle of the version read; pass it to write or edit


@dataclass(frozen=True)
class Link:
    """A citation a span rests on: its kind, its producer, if any, and the spans it rests on in turn."""

    kind: CitationKind
    attrs: dict[str, str]  # the tool that produced it (transcriber, classifier, assessor), by id
    handles: list[str]  # what it rests on; none for a source or unsourced text


@dataclass(frozen=True)
class Grounds:
    """One span in a chain of provenance, and what it rests on."""

    handle: str  # for an unreachable span, its version's whole id: nothing here can resolve a handle to it
    workspace: str | None  # the workspace its version is filed in; None if unreachable
    documents: list[str]  # where the span's version is filed there
    text: str | None  # its text, cut short if long; None for a region of a page, or an unreachable span
    where: str  # page, heading path and line, or JSON path
    rests_on: list[Link]  # none if nothing covers it
    undeclared: list[str]  # handles of the parts of it no citation covers
    # In neither this workspace nor any it links to (a link was removed, say), so nothing more is known of it here.
    unreachable: bool = False
    newer: dict[str, str] = field(default_factory=dict)  # documents that moved past its version: their heads' handles


@dataclass(frozen=True)
class Shortfall:
    """Where a span's support falls short of declared sources, and why."""

    reason: SupportReason
    handle: str


@dataclass(frozen=True)
class Chain:
    """Whether a span's support reaches declared sources, and what it rests on, nearest first."""

    status: SupportStatus
    issues: list[Shortfall]
    spans: list[Grounds]
    truncated: bool  # the limit cut the chain short: trace a handle where it stopped
    unreachable: list[VersionId]  # versions it rests on in neither this workspace nor any it links to


@dataclass(frozen=True)
class Edit:
    old: str  # must occur exactly once in the text, unless replace_all
    new: str
    replace_all: bool = False


@dataclass(frozen=True)
class Written:
    """A write or an edit that went through."""

    base: str  # the handle of the document's version: the base for the next change
    advanced: bool  # whether the document moved; False if it already held exactly this
    support: SupportStatus  # whether all of it rests on declared sources
    warnings: list[str] = field(default_factory=list)  # what didn't stop the write but is worth acting on
    status: Literal["written"] = "written"


@dataclass(frozen=True)
class Refused:
    """A write or an edit that was stopped, and nothing written: every issue, in the writer's terms."""

    issues: list[str]
    status: Literal["refused"] = "refused"


@dataclass(frozen=True)
class Checked:
    """A dry run that found nothing to stop the write; nothing was written."""

    warnings: list[str] = field(default_factory=list)  # what wouldn't stop the write but is worth acting on
    status: Literal["checked"] = "checked"


Outcome = Written | Refused | Checked  # what a write or an edit did


# --- search ---------------------------------------------------------------------


def search(
    bond: Bond, phrases: str | Sequence[str], document: str | None = None, within: str | None = None, limit: int = 20,
    workspace: str | None = None,
) -> list[SearchHit]:
    """Matching text with exact handles, each with whether its support reaches declared sources.

    Searches the current version of every document in the workspace and every workspace it links to, or only
    one of those workspaces (workspace, by name), or one document (in that workspace, or this one), or only
    inside the span of a handle (within), e.g. to find a phrase inside a passage found earlier. Several phrases
    are searched in one call, up to limit matches each, per workspace searched; each match says which phrase it
    matched, which workspace it is in, where it is, the text around it, and the handle of the whole passage it
    sits in, which can be cited instead. Support is structural: it does not evaluate semantic support or source
    authority.
    """
    if limit < 1:
        raise ToolError("limit must be at least 1")
    if within is not None and (document is not None or workspace is not None):
        raise ToolError("search within one handle's span, or in a document or workspace, not both: the span is "
                        "in one version")
    scope: Ref | None = None
    if within is not None:
        resolved = _resolve(bond, within)
        if isinstance(resolved, Problem):
            raise ToolError(resolved.message)
        scope = resolved
    searched = [bond] if scope is not None else [linked(bond, workspace)] if workspace is not None else (
        [bond] if document is not None else [bond, *bond.reachable()])
    if document is not None:
        _head(searched[0], document)  # the document must exist
    phrases = [phrases] if isinstance(phrases, str) else list(phrases)
    found: list[SearchHit] = []
    for phrase in phrases:
        for where in searched:
            hits = where.search(phrase, documents=None if document is None else [document], within=scope,
                                limit=limit)
            found += [_hit(bond, phrase, hit.ref, hit.workspace, hit.documents) for hit in hits]
    return found


def linked(bond: Bond, workspace: str) -> Bond:
    """The workspace bond can read by this name: itself, or one it links to (directly or not)."""
    if workspace == bond.workspace.name:
        return bond
    reachable = bond.reachable()
    for other in reachable:
        if other.workspace.name == workspace:
            return other
    names = ", ".join(repr(b.workspace.name) for b in reachable) or "none"
    raise ToolError(f"this workspace doesn't link to {workspace!r} (it links to: {names})")


def _hit(bond: Bond, phrase: str, ref: Ref, workspace: str, documents: list[str]) -> SearchHit:
    evidence = _evidence(bond, ref, workspace, documents)
    passage = _passage(bond, ref)
    return SearchHit(evidence.handle, evidence.workspace, evidence.documents, evidence.text, evidence.support,
                     evidence.kinds, evidence.newer, phrase=phrase, where=_where(bond, ref),
                     context=_context(bond, ref), passage=None if passage is None else _handle(bond, passage))


def _evidence(bond: Bond, ref: Ref, workspace: str | None = None, documents: list[str] | None = None) -> Evidence:
    """A span to cite, with whether its support reaches declared sources and the kinds overlapping it."""
    if workspace is None or documents is None:
        workspace = details.held_by(bond, ref.version).workspace.name
        documents = [d.name for d in details.filed_in(bond, ref.version)]
    kinds = sorted({c.kind for c in bond.declarations(ref).citations}, key=lambda k: k.value)
    support = bond.check_support(ref, limit=SUPPORT_LIMIT).status
    return Evidence(_handle(bond, ref), workspace, documents, bond.text(ref) or "", support, kinds,
                    _newer(bond, ref.version))


def _newer(bond: Bond, version: VersionId) -> dict[str, str]:
    """The documents holding version that have moved past it, each with the handle of its whole head."""
    return {d.name: _handle(bond, Ref(d.head, VersionLocator())) for d in details.filed_in(bond, version)
            if d.head != version}


def _passage(bond: Bond, ref: Ref) -> Ref | None:
    """The claim unit a match lies in, if it is larger than the match: what a paraphrase would rest on."""
    for unit in details.claim_units(bond, ref.version):
        if unit.locator != ref.locator and contains(unit.locator, ref.locator):
            return unit
    return None


def _where(bond: Bond, ref: Ref) -> str:
    """Where a match is, as a reader would find it: the region it was read from, if any, then its place."""
    read_from = dict.fromkeys(
        details.where(bond, s)
        for c in bond.citations(ref.version) if overlaps(c.claim, ref.locator)
        for s in c.supports if s.locator.addresses is Addresses.REGION
    )
    return " · ".join(part for part in [", ".join(read_from), details.where(bond, ref)] if part)


def _context(bond: Bond, ref: Ref, pad: int = 40) -> str:
    """The text around a match, on one line, with the match marked."""
    before, at, after = details.excerpt(bond, ref, pad)

    def flat(t: str) -> str:
        return " ".join(t.split())

    return (("…" if len(before) == pad else "") + flat(before) + (" " if before[-1:].isspace() else "")
            + f"▸{at}◂" + (" " if after[:1].isspace() else "") + flat(after) + ("…" if len(after) == pad else ""))


# --- documents ------------------------------------------------------------------


def documents(bond: Bond) -> list[Listing]:
    """Every document in the workspace, oldest first, with the format of its current version."""
    return [Listing(d.name, details.version(bond, d.head).format) for d in bond.documents()]


# --- trace ----------------------------------------------------------------------

TRACE_TEXT = 200  # characters of a span's text to show; a whole document would be far too long


def trace(bond: Bond, handle: str, limit: int = 30) -> Chain:
    """Whether the span a handle names rests on declared sources, and what it rests on, hop by hop, down to
    its sources: pages, declared sources, or text with no declared support.

    status is the structural support check of the whole span, with issues saying where it falls short. Up to
    limit spans follow, nearest first. Each comes with its own handle, so anything along the way can be cited
    directly, or asked about again where the limit cut the chain short.
    """
    if limit < 1:
        raise ToolError("limit must be at least 1")
    ref = _resolve(bond, handle)
    if isinstance(ref, Problem):
        raise ToolError(ref.message)
    report = bond.check_support(ref, limit=SUPPORT_LIMIT)
    trace = bond.trace(ref, limit=limit)
    return Chain(report.status, [Shortfall(i.reason, _ref_handle(bond, i.ref)) for i in report.issues],
                 [_grounds(bond, trace, node) for node in trace.nodes], not trace.complete,
                 sorted(trace.unreachable))


def _grounds(bond: Bond, trace: Trace, node: Ref) -> Grounds:
    if node.version in trace.unreachable:  # nothing of it is here to show or cite: only its id, which can't change
        return Grounds(node.version, None, [], None, "", [], [], unreachable=True)
    text = bond.text(node)
    links = [
        Link(e.citation.kind, e.citation.attrs, [_ref_handle(bond, s) for s in e.citation.supports])
        for e in trace.edges_at(node)
    ]
    newer = {name: _handle(bond, Ref(head, VersionLocator())) for name, head in trace.newer.get(node.version, {}).items()}
    return Grounds(_handle(bond, node), details.held_by(bond, node.version).workspace.name,
                   [d.name for d in details.filed_in(bond, node.version)],
                   None if text is None else clip(text, TRACE_TEXT), details.where(bond, node), links,
                   [_handle(bond, r) for r in bond.declarations(node).undeclared], newer=newer)


# --- read -----------------------------------------------------------------------


def read(bond: Bond, document: str) -> Reading:
    """A document's current version exactly as it is, to read in full: every heading, label and field.

    Nothing in it is a handle: to cite part of it, search for that text (with document=, or within= a
    passage) and cite the handle of the match. A format with no text of its own (a PDF) points to the
    documents that transcribe it instead.
    """
    version = details.version(bond, _head(bond, document))
    if not spec(version.format).has_text:
        # Documents whose head transcribes this version: one that held such a transcription once may hold one of
        # another version of the source now.
        transcribing = sorted({d for t in bond.transcriptions_of(version.id) for d in t.current})
        if transcribing:
            where = f"; documents that transcribe it: {', '.join(map(repr, transcribing))}"
        else:
            earlier = sorted({d for e in bond.history(document) if e.version != version.id
                              for t in bond.transcriptions_of(e.version) for d in t.documents})
            where = "; no document transcribes its current version yet, so its text can't be read or cited: ask the "
            where += "person to have it transcribed"
            if earlier:
                where += (f" (these transcribe an earlier version of it, and may not match it now: "
                          f"{', '.join(map(repr, earlier))})")
        raise ToolError(f"{document!r} is {version.format.value}, which has no text of its own{where}")
    return Reading(bond.workspace.name, document, version.format, bond.text(Ref(version.id, VersionLocator())) or "")


def _head(bond: Bond, document: str) -> VersionId:
    doc = bond.filing.document(document)
    if doc is None:
        raise ToolError(f"no document is named {document!r}")
    return doc.head


# --- draft ----------------------------------------------------------------------


def draft(bond: Bond, document: str) -> Draft:
    """A document's current version as a draft to edit; its base is what a write or edit starts from."""
    head = _head(bond, document)
    if unreachable := [s.version for c in bond.citations(head) for s in c.supports
                       if details.owner(bond, s.version) is None]:
        raise ToolError(f"{document!r} rests on {len(set(unreachable))} version(s) in workspaces this one doesn't "
                        "link to, so its citations can't be written out for editing; read() shows it as it is")
    text = _render(bond, document, head)
    if isinstance(text, Problem):
        raise ToolError(text.message)
    return Draft(document, text, _evidence_of(bond, head), _prefix(head))


def _render(bond: Bond, document: str, version: VersionId) -> str | Problem:
    """The draft of a version, or why it has none."""
    fmt = details.version(bond, version).format
    codec = CODECS.get(fmt)
    if codec is None:
        writable = ", ".join(f.value for f in CODECS)
        return Problem(f"{document!r} is {fmt.value}, which can't be read for editing yet (only {writable})")
    citations = [c.as_written() for c in bond.citations(version)]
    content = bond.text(Ref(version, VersionLocator())) or ""
    try:
        return codec.render(content, citations, lambda ref: _handle(bond, ref))
    except Unrenderable as e:  # found deep inside rendering, which stops there
        return Problem(f"{document!r} can't be shown with its citations: {e}")


def _evidence_of(bond: Bond, version: VersionId) -> list[Evidence]:
    """What the version's citations rest on in other versions, each with its handle."""
    refs = dict.fromkeys(s for c in bond.citations(version) for s in c.supports if s.version != version)
    return [_evidence(bond, r) for r in refs]


# --- write / edit -----------------------------------------------------------------


def write(
    bond: Bond, document: str, draft: str, base: str | None = None, *, policy: Policy, dry_run: bool = False,
    format: Format | None = None,
) -> Outcome:
    """Write draft (the whole document, as draft() shows it) as document's next version, starting from base.

    base is the handle draft() returned for the document, or the base of the last write's outcome; leave it
    out only to create a new document, in format (DEFAULT_FORMAT unless given). A document keeps its format.
    JSON is stored in one layout (see jsonmarkup.canonical), so a document laid out otherwise is reformatted
    by its first write. With dry_run, nothing is written: the issues are what a write would find.
    """
    base_version: VersionId | None = None
    if base is None:
        if bond.filing.document(document) is not None:
            return Refused([f"{document!r} already exists; pass the base draft() returned for it, so your change is "
                            "based on it"])
        fmt = format or DEFAULT_FORMAT
    else:
        started = _whole_version(bond, base)
        if isinstance(started, Problem):
            return Refused([started.message])
        base_version, fmt = started, details.version(bond, started).format
    mismatch = [f"{document!r} is {fmt.value}; a document keeps its format"] if format not in (None, fmt) else []
    codec = CODECS.get(fmt)
    if codec is None:
        return Refused(mismatch + [f"{fmt.value} documents can't be written yet (only "
                                   f"{', '.join(f.value for f in CODECS)})"])
    if mismatch:
        return Refused(mismatch)
    compiled = codec.compile(draft)
    if compiled.issues:
        return Refused(list(compiled.issues))
    citations = _citations(bond, compiled)
    if isinstance(citations, Refused):
        return citations
    try:
        plan = bond.plan_write(document, compiled.content, citations, format=fmt, base=base_version, policy=policy)
    except WriteError as e:  # every issue the write would be refused for
        return Refused(_explain(e.errors, compiled, codec, document))
    warnings = [_superseded_note(bond, citations, w) for w in plan.warnings]
    if dry_run:
        return Checked(warnings)
    try:
        result = bond.commit(plan)
    except WriteError as e:  # another write moved the document between planning and committing
        return Refused(_explain(e.errors, compiled, codec, document))
    whole = Ref(result.version, VersionLocator())
    return Written(_handle(bond, whole), result.advanced, bond.check_support(whole, limit=SUPPORT_LIMIT).status,
                   warnings)


def _superseded_note(bond: Bond, citations: Sequence[Citation], issue: Issue) -> str:
    """A CITES_SUPERSEDED warning in the writer's terms: which handle, and where to look now, by handle."""
    if issue.citation_index is None or issue.support_index is None:
        return issue.message
    support = citations[issue.citation_index].supports[issue.support_index]
    if not isinstance(support, Ref):
        return issue.message
    newer = _newer(bond, support.version)
    parts = [f"{document!r} is at {head} now" for document, head in newer.items()]
    return (f"{_handle(bond, support)} cites an older version: " + "; ".join(parts)
            + "; search the newer one for the same text, unless the older one is what you mean")


def edit(
    bond: Bond, document: str, edits: Sequence[Edit], base: str, *, policy: Policy, dry_run: bool = False,
) -> Outcome:
    """Apply find-and-replace edits, in order, to the draft of document's base version, and write the result.

    The edits apply to what draft() showed for that base. Every edit must apply and the result must pass the
    policy, or nothing is written.
    """
    started = _whole_version(bond, base)
    if isinstance(started, Problem):
        return Refused([started.message])
    text = _render(bond, document, started)
    if isinstance(text, Problem):
        return Refused([text.message])
    problems: list[str] = []
    for n, e in enumerate(edits, 1):
        applied = _apply(text, e, n)
        if isinstance(applied, Problem):
            problems.append(applied.message)
        else:
            text = applied
    if problems:
        return Refused(problems)
    return write(bond, document, text, base, policy=policy, dry_run=dry_run)


def _whole_version(bond: Bond, base: str) -> VersionId | Problem:
    """The version a base names, or why it names none: a base is the handle of a whole version."""
    ref = _resolve(bond, base)
    if isinstance(ref, Problem):
        return Problem(f"base {ref.message}")
    if ref.locator.addresses is not Addresses.WHOLE:
        return Problem(f"base {base!r} names part of a version; pass the base draft() returned, which names a whole one")
    return ref.version


# --- compiling a draft's entries into citations ----------------------------------------------------


def _citations(bond: Bond, draft: Compiled) -> list[Citation] | Refused:
    """The draft's citations, in its entries' order, or the issues that stop it compiling."""
    citations: list[Citation] = []
    issues: list[str] = []
    for entry in draft.entries:
        supports: list[Support] = []
        for token in entry.sources:
            support = _support(bond, token, draft.names)
            if isinstance(support, Problem):
                issues.append(f"{entry.label}: {support.message}")
            else:
                supports.append(support)
        citations.append(Citation(entry.kind, entry.claim, supports, dict(entry.attrs)))
    return Refused(issues) if issues else citations


def _support(bond: Bond, token: str, names: dict[str, Locator]) -> Support | Problem:
    if token.startswith("#"):
        locator = names.get(token[1:])
        if locator is None:
            return Problem(f"{token} isn't the id of a tag or anchor around text in this document; to rest on "
                           'part of a sentence, wrap it in <bond:anchor id="…">…</bond:anchor>')
        return SelfRef(locator)
    return _resolve(bond, token)


# --- issues in the writer's terms -------------------------------------------------------


def _explain(issues: Sequence[Issue], draft: Compiled, codec: Codec, document: str) -> list[str]:
    return [_explain_one(i, draft, codec, document) for i in issues]


def _explain_one(issue: Issue, draft: Compiled, codec: Codec, document: str) -> str:
    if issue.code is IssueCode.STALE_BASE:
        return f"{document!r} changed since the base you started from; draft it again and redo your change"
    if issue.code is IssueCode.UNCOVERED_CLAIM:
        where = f" at {at}" if issue.locator is not None and (at := codec.where(draft.content, issue.locator)) else ""
        return (f"{issue.message}{where} ({codec.how_to_cite}: a quote or paraphrase citing a handle from "
                'search(), or kind "unsourced" if you made it up; or remove it)')
    if issue.code is IssueCode.CIRCULAR_SUPPORT and issue.cycle:
        # The core names citations by position, which is the order of the draft's entries.
        path = [draft.entries[i] for i in issue.cycle + issue.cycle[:1] if i < len(draft.entries)]
        return (" rests on ".join(e.label for e in path) + ": a citation can't rest on itself, directly or "
                "through others; one inside another can't cite the one around it")
    entry = _entry_for(issue, draft)
    hint = f" ({issue.hint})" if issue.hint else ""
    if entry is None:
        return f"{issue.message}{hint}"
    if (j := issue.support_index) is not None:
        token = entry.sources[j] if j < len(entry.sources) else f"support {j}"
        return f"{entry.label}: from {token}: {issue.message}{hint}"
    return f"{entry.label}: {issue.message}{hint}"


def _entry_for(issue: Issue, draft: Compiled) -> Entry | None:
    if issue.citation_index is not None and issue.citation_index < len(draft.entries):
        return draft.entries[issue.citation_index]
    return None


# --- handles ------------------------------------------------------------------------------


def _handle(bond: Bond, ref: Ref) -> str:
    """A ref's handle. A JSON value is named by its pointer, which only its version can give: the stored
    locator holds no keys."""
    locator = ref.locator
    named = Pointer(details.pointer(bond, ref), locator.span) if isinstance(locator, JsonLocator) else locator
    return format_handle(_prefix(ref.version), named)


def _ref_handle(bond: Bond, ref: Ref) -> str:
    """A ref's handle, or for an unreachable version, which nothing here can resolve, its whole id."""
    if details.owner(bond, ref.version) is None:
        return ref.version
    return _handle(bond, ref)


def _ids(bond: Bond) -> list[VersionId]:
    """Every version bond can read: its own and those of the workspaces it links to, each id once."""
    return list(dict.fromkeys(v.id for b in [bond, *bond.reachable()] for v in details.versions(b)))


# How much of a version's id a handle shows: 48 bits, so two versions a workspace can read sharing a prefix is a
# one-in-many-billions event even at a million versions; resolving refuses an ambiguous one rather than guess.
HANDLE_PREFIX = 12


def _prefix(version: VersionId) -> str:
    """The prefix of a version's id a handle shows: always the same, whatever else is stored, so a handle given out
    once never changes."""
    return version[:HANDLE_PREFIX]


def _resolve(bond: Bond, token: str) -> Ref | Problem:
    """The span a handle names, or why it names none."""
    handle = parse_handle(token)
    if isinstance(handle, BadHandle):
        return Problem(f"{handle.message}; use search() to find evidence to cite")
    matches = [v for v in _ids(bond) if v.startswith(handle.prefix)]
    if not matches:
        return Problem(f"no version's id starts with {handle.prefix!r}, so {token!r} names nothing; search() again")
    if len(matches) > 1:
        return Problem(f"{handle.prefix!r} starts {len(matches)} versions' ids; use more of the id, as search() shows it")
    version = matches[0]
    if isinstance(handle.locator, Pointer):  # a JSON value: positioned in its version
        doc = details.json_doc(bond, version)
        if doc is None:
            fmt = details.version(bond, version).format.value
            return Problem(f"{token!r} doesn't fit its version: it names a JSON value, but the version is {fmt}")
        located = doc.locate(handle.locator.pointer, handle.locator.span)
        if located is None:
            return Problem(f"{token!r} doesn't fit its version: json pointer {handle.locator.pointer!r} does not "
                           "exist in it")
        ref = Ref(version, located)
    else:
        ref = Ref(version, handle.locator)
    if problem := details.locator_problem(bond, ref):
        return Problem(f"{token!r} doesn't fit its version: {problem}")
    return ref


def _apply(text: str, edit: Edit, n: int) -> str | Problem:
    """text with one edit applied, or why the edit can't apply."""
    if not edit.old:
        return Problem(f"edit {n}: old is empty; give the exact text to replace")
    starts = [m.start() for m in re.finditer(re.escape(edit.old), text)]
    if not starts:
        return Problem(f"edit {n}: {clip(edit.old)!r} isn't in the text; copy it exactly from what draft() returned")
    if len(starts) > 1 and not edit.replace_all:
        shown = "; ".join(f"line {text.count(chr(10), 0, s) + 1}: …{clip(text[max(0, s - 30):s + len(edit.old) + 30])}…"
                          for s in starts)
        return Problem(f"edit {n}: {clip(edit.old)!r} occurs {len(starts)} times ({shown}); include more of the "
                       "surrounding text so it occurs once, or set replace_all")
    return text.replace(edit.old, edit.new) if edit.replace_all else text.replace(edit.old, edit.new, 1)


# --- binding a policy ---------------------------------------------------------------------


@dataclass(frozen=True)
class Tools:
    """The tools with a workspace and a policy bound, for a host to hand to an agent.

    The agent writes in bond's workspace only. It reads, searches and cites that workspace and the workspaces it
    links to (shared policies, public data), which bond carries and its deployment decides; it can never write
    to those.
    """

    bond: Bond
    policy: Policy = AGENT_POLICY

    def search(self, phrases: str | Sequence[str], document: str | None = None, within: str | None = None,
               limit: int = 20, workspace: str | None = None) -> list[SearchHit]:
        return search(self.bond, phrases, document, within, limit, workspace)

    def documents(self, workspace: str | None = None) -> list[Listing]:
        return documents(self.bond if workspace is None else linked(self.bond, workspace))

    def read(self, document: str, workspace: str | None = None) -> Reading:
        return read(self.bond if workspace is None else linked(self.bond, workspace), document)

    def trace(self, handle: str, limit: int = 30) -> Chain:
        return trace(self.bond, handle, limit)

    def draft(self, document: str) -> Draft:
        return draft(self.bond, document)

    def write(self, document: str, draft: str, base: str | None = None, dry_run: bool = False,
              format: Format | None = None) -> Outcome:
        return write(self.bond, document, draft, base, policy=self.policy, dry_run=dry_run, format=format)

    def edit(self, document: str, edits: Sequence[Edit], base: str, dry_run: bool = False) -> Outcome:
        return edit(self.bond, document, edits, base, policy=self.policy, dry_run=dry_run)
