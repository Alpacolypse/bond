"""The core API of one workspace, in two layers.

Evidence: ingest, write_document (or plan_write, then commit), search, declarations, check_support, citations, trace, cited_by
and verify, plus blob and text to read what a version holds.
Filing: document, documents and history. Writes go through both: each stores a version and files it in a document.
Between workspaces: a workspace may link to others (a shared policy library, say), as its deployment decides.
Its citations can rest on versions in those, and in what they link to in turn; reading a version, checking a
citation, tracing and verifying all look there too. A link gives read access only: writes stay in this workspace.

Lower-level reads (version records, where a span is, JSON pointers) are in bond.details, and whole-workspace
reads for the viewer in bond.http.viewer, both on top of these."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache, partial
from pathlib import Path
from typing import Concatenate

from . import kinds
from .formats import from_suffix, parse, sniff, spec
from .ids import citation_id, hash_bytes, version_id
from .locators import VersionLocator, contains, overlaps
from .models import (
    BondError,
    Citation,
    CitationKind,
    CorruptVersion,
    Declarations,
    DocumentRef,
    Format,
    HistoryEntry,
    Hit,
    Issue,
    IssueCode,
    Ref,
    SelfRef,
    StoredCitation,
    Support,
    SupportReport,
    Trace,
    TranscriptionOf,
    UnknownDocument,
    UnknownVersion,
    Version,
    VersionId,
    Workspace,
    WriteError,
    WriteResult,
)
from .parsers import Malformed, ParsedDoc
from .plans import FilingChange, WritePlan
from .policies import STRICT, Policy
from .storage import Blobs, Conflict, NewVersion, filing_problem
from .storage.filing import Filing
from .storage.index import MemoryIndex
from .storage.records import Records
from .storage.store import Store
from .storage.workspace import open_workspace
from .support import check_support, partition, walk

# Parsed documents kept in memory by default. Versions are immutable, so any eviction is safe.
DEFAULT_PARSED_CACHE_SIZE = 256



def _one_view[**P, R](method: Callable[Concatenate[Bond, P], R]) -> Callable[Concatenate[Bond, P], R]:
    """Run a query against one catch-up of the index, however many lookups it makes."""

    @functools.wraps(method)
    def wrapper(self: Bond, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._index.view():
            return method(self, *args, **kwargs)

    return wrapper

class Bond:
    """One provenance-enforcing workspace: its keys in a store, its bytes in blobs.

    A workspace is a world of its own, like a case: writes cite versions in it, or in the workspaces it links to
    (links), which a deployment decides and may change at any time. Safe to share across threads, with any
    backends: Bond holds no connection or transaction of its own. See bond.storage for the backends.
    """

    def __init__(
        self, store: Store, blobs: Blobs, workspace: str | None = None, *, create: bool = True,
        links: Links = (), parsed_cache_size: int = DEFAULT_PARSED_CACHE_SIZE,
    ):
        """The workspace named workspace in store (created if missing, unless create is False; with no name, the only
        one there is), its bytes in blobs, keeping up to parsed_cache_size parsed versions in memory.

        links are the workspaces this one links to (see set_links). The store and blobs stay the caller's: close()
        leaves them open."""
        self.store = store
        self.blobs = blobs
        self.workspace: Workspace = open_workspace(store, workspace, create)  # the workspace this Bond reads and writes
        self._records = Records(store, self.workspace.id)  # its versions
        self._filing = Filing(store, self.workspace.id)  # its documents
        self._index = MemoryIndex(self._records, self._filing)  # reverse lookups, derived from both
        self._owned: list[object] = []  # what this Bond opened itself, and closes
        self.set_links(links)
        # Per instance, so the cache doesn't keep this Bond alive after it is closed.
        self._load = lru_cache(maxsize=parsed_cache_size)(self._load_version)

    @classmethod
    def local(
        cls, root: str | os.PathLike[str], parsed_cache_size: int = DEFAULT_PARSED_CACHE_SIZE,
        create: bool = True, links: Links = (),
    ) -> Bond:
        """Open a workspace in a local directory: a SQLite store and a directory of blobs, closed with the Bond.

        It is created if it doesn't exist, named after the directory, unless create is False.
        """
        from .storage.local import LocalBlobs
        from .storage.sqlite import SQLiteStore

        root = Path(root)
        # A new directory's workspace is named after it; an existing one opens the workspace it holds.
        name = None if (root / "bond.db").exists() else root.resolve().name
        store = SQLiteStore(root / "bond.db", create=create)
        try:
            bond = cls(store, LocalBlobs(root / "blobs"), name, create=create, links=links,
                       parsed_cache_size=parsed_cache_size)
        except BaseException:
            store.close()
            raise
        bond._owned.append(store)
        return bond

    def close(self) -> None:
        """Stop this Bond, and close what it opened itself (Bond.local's store); a store, pool or client its host
        passed in stays open."""
        for owned in self._owned:
            close = getattr(owned, "close", None)
            if callable(close):
                close()
        self._owned.clear()

    def __enter__(self) -> Bond:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- links ---------------------------------------------------------------

    def set_links(self, links: Links) -> None:
        """Link this workspace to others: its citations may rest on their versions, and on those of the workspaces
        they link to, and so on. links is a list of opened workspaces, or a function giving them, asked on every
        read, for a deployment whose links change while it runs. A link gives read access, nothing more."""
        if isinstance(links, Iterable):
            fixed = tuple(links)
            self._links: Callable[[], Iterable[Bond]] = lambda: fixed
        else:
            self._links = links

    def links(self) -> list[Bond]:
        """The workspaces this one links to directly, as its deployment says now."""
        return [b for b in self._links() if b.workspace.id != self.workspace.id]

    def reachable(self) -> list[Bond]:
        """Every other workspace this one can read: those it links to, those they link to, and so on, nearest first.
        Each once, however often it is reached; links that loop back are followed no further."""
        seen = {self.workspace.id}
        found: list[Bond] = []
        level = self.links()
        while level:
            following: list[Bond] = []
            for b in level:
                if b.workspace.id in seen:
                    continue
                seen.add(b.workspace.id)
                found.append(b)
                following += b.links()
            level = following
        return found

    def _owner(self, version: VersionId) -> Bond | None:
        """The workspace a version is read from: this one if it holds it, else the nearest reachable one that
        does; None if none does."""
        if self._records.as_stored(version) is not None:
            return self
        return next((b for b in self.reachable() if b._records.as_stored(version) is not None), None)

    def _held(self, version: VersionId) -> Bond:
        """The workspace a version is read from; raises UnknownVersion if none reachable holds it."""
        owner = self._owner(version)
        if owner is None:
            raise UnknownVersion(version)
        return owner

    def _citations_of(self, version: VersionId) -> list[StoredCitation]:
        """The citations a version was written with, wherever it is held; none if it is unreachable."""
        owner = self._owner(version)
        return [] if owner is None else owner._records.citations(version)

    # --- ingest --------------------------------------------------------------

    def ingest(
        self, document: str, source: bytes | str | os.PathLike[str], *,
        format: Format | None = None, base: VersionId | None = None, title: str | None = None,
        origin: str | None = None,
    ) -> WriteResult:
        """Store a file that is a source throughout, and file it in document.

        source is the file's bytes, or a path to read them from. A shortcut for write_document with one
        SOURCE citation over the whole version, so the same bytes in the same format are always the same
        version, and ingesting them again where they already are is a no-op. The format comes from a path's
        suffix, or is sniffed from the bytes, if not given; base and title work as in write_document. Raises
        WriteError (INVALID_CONTENT) for malformed content.

        origin, if given, says where the file came from (a path or URL, as the caller knows it), and is
        recorded on the history entry that files the version, for people to trace it back. It is filing, not
        identity: it changes no version id, and verify never reads it. Ingesting bytes again where they already
        are adds no entry, so it doesn't record a new origin either.
        """
        return self.commit(self.plan_ingest(document, source, format=format, base=base, title=title, origin=origin))

    def plan_ingest(
        self, document: str, source: bytes | str | os.PathLike[str], *,
        format: Format | None = None, base: VersionId | None = None, title: str | None = None,
        origin: str | None = None,
    ) -> WritePlan:
        """The plan ingest commits; raises WriteError as ingest does. Stores nothing."""
        if isinstance(source, bytes):
            data, fmt = source, format or sniff(source)
        else:
            path = Path(source)
            data = path.read_bytes()
            fmt = format or from_suffix(path.suffix) or sniff(data)
        whole = [Citation(CitationKind.SOURCE, VersionLocator(), [])]
        version = version_id(hash_bytes(data), fmt, whole)
        if self._records.version(version):  # stored already: only filing it is left, so skip parsing it again
            return self._plan_filing(None, FilingChange(document, version, base, origin, title))
        return self._plan_written(data, fmt, document, whole, base, STRICT, title, origin)

    # --- reading versions -----------------------------------------------------

    def blob(self, version: VersionId) -> bytes:
        """A version's raw bytes, in any format; raises CorruptVersion if they are missing."""
        owner = self._held(version)
        data = owner.blobs.get(owner._version(version).content_hash)
        if data is None:
            raise CorruptVersion(version, "its blob is missing")
        return data

    def text(self, ref: Ref) -> str | None:
        """The text a reference addresses (a VersionLocator for the whole), or None if it addresses none, like
        a PDF region. Raises ValueError if the locator isn't valid in its version.
        """
        self._check_ref(ref)
        return self._doc(ref.version).text(ref.locator)

    def _version(self, version: VersionId) -> Version:
        """A version's record, here or in a reachable workspace; raises UnknownVersion if there is none."""
        stored = self._records.version(version)
        if stored is None:
            stored = self._held(version)._records.version(version)
        if stored is None:
            raise UnknownVersion(version)
        return stored

    # --- search --------------------------------------------------------------

    @_one_view
    def search(
        self, phrase: str, *, documents: list[str] | None = None, within: Ref | None = None, limit: int = 50,
    ) -> list[Hit]:
        """Find a phrase in the current versions of documents and pin each match to a locator.

        Searches the heads of all documents in this workspace, or of the given documents, or only inside one
        reference (within), which may be in a workspace this one links to; search a linked workspace's documents
        on its own Bond.
        Matching is case-insensitive and forgives whitespace, quote characters, ligatures and hyphenated line
        breaks. Matches are not collapsed. Each hit carries its direct declarations; check_support() says
        whether a match's support reaches declared sources.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if documents is not None and within is not None:
            raise ValueError("search takes documents or within, not both: within is inside one version already")
        hits: list[Hit] = []
        for version in self._search_scope(documents, within):
            if not spec(version.format).has_text:
                continue
            for hit in self._search_version(version, phrase):
                if within is not None and not contains(within.locator, hit.ref.locator):
                    continue
                hits.append(hit)
                if len(hits) >= limit:
                    return hits
        return hits

    def _search_scope(self, documents: list[str] | None, within: Ref | None) -> list[Version]:
        if within is not None:
            return [self._version(within.version)]
        if documents is not None:
            return [self._version(v) for v in dict.fromkeys(self.document(d).head for d in documents)]
        heads = {d.head for d in self._filing.documents()}
        return [v for v in self._index.versions() if v.id in heads]

    def _search_version(self, version: Version, phrase: str) -> list[Hit]:
        matches = self._doc(version.id).search(phrase)
        if not matches:
            return []
        owner = self._held(version.id)
        owned = owner._records.citations(version.id)
        holding = owner._index.holding(version.id)
        names = [d.name for d in holding]
        newer = {d.name: d.head for d in holding if d.head != version.id}
        return [
            Hit(Ref(version.id, m.locator), owner.workspace.name, list(names), version.format, m.snippet, m.context,
                self._declarations(Ref(version.id, m.locator), owned), dict(newer))
            for m in matches
        ]

    def declarations(self, ref: Ref) -> Declarations:
        """Declarations overlapping a valid selection, with exact undeclared gaps.

        Citations retain their original subject spans. No recursive judgment is made. Text follows
        format coverage rules; PDF selections use geometric coverage rather than text extraction.
        """
        self._check_ref(ref)
        return self._declarations(ref, self._citations_of(ref.version))

    def _declarations(self, ref: Ref, citations: list[StoredCitation]) -> Declarations:
        selected = tuple(c for c in citations if overlaps(c.claim, ref.locator))
        doc = self._doc(ref.version)
        gaps = [part for part in partition(doc, ref.locator, [c.claim for c in selected])
                if not any(contains(c.claim, part) for c in selected)]
        return Declarations(ref, selected, tuple(Ref(ref.version, gap) for gap in gaps))

    def check_support(self, ref: Ref, *, max_depth: int | None = None, limit: int = 1000) -> SupportReport:
        """Check every selected declaration's ancestry to declared sources, with region-level outcomes.

        All dependencies must pass. Missing versions and budgets yield UNKNOWN, never support. The limit
        counts distinct references, including the root. No semantic or source-authority judgment or
        integrity recheck is performed; verify() is the separate integrity operation. Outcomes are
        recomputed on every call, with shared ancestry memoized only within that call.
        """
        self._check_ref(ref)
        return check_support(_Graph(self), ref, max_depth, limit)

    # --- write_document ------------------------------------------------------

    def write_document(
        self,
        document: str,
        content: bytes | str,
        citations: Sequence[Citation],
        *,
        format: Format | None = None,
        base: VersionId | None = None,
        policy: Policy = STRICT,
        title: str | None = None,
    ) -> WriteResult:
        """Store a version with its citations and file it in document, or raise WriteError with every problem.

        Text is stored as UTF-8. The format is sniffed if not given (Markdown or JSON: name CSV and TSV).

        Integrity is always checked: the content is well-formed, every locator is valid in its version, every
        supporting version exists, each citation passes its kind's validator, and supports within the version
        form no loop. Then the policy decides the rest. By default (STRICT) every word must be accounted for:
        inside the claim of a citation that rests on evidence, a SOURCE declaration of origin, or an UNSOURCED
        declaration of no supporting evidence. Only syntax and punctuation are exempt. Under ALLOW_GAPS,
        uncovered spans are stored as gaps instead; declarations() lists them as undeclared.

        A support SelfRef(locator) points into the version being written.
        Supports within the version must not form a loop.

        Filing: leave base out to create document; to write its next version,
        pass its head as base, which fails with STALE_BASE if another write moved
        it first. Writing the version document already points at is a no-op.
        title, if given, labels the document for people to read it by.

        A caller that expects refusals, like an agent's tools, plans first: plan_write raises the same WriteError
        and stores nothing, and commit does the write it planned.
        """
        return self.commit(self.plan_write(document, content, citations, format=format, base=base, policy=policy,
                                           title=title))

    def plan_write(
        self,
        document: str,
        content: bytes | str,
        citations: Sequence[Citation],
        *,
        format: Format | None = None,
        base: VersionId | None = None,
        policy: Policy = STRICT,
        title: str | None = None,
    ) -> WritePlan:
        """The plan write_document commits: checked now, against the workspace as it is, or WriteError with every
        issue. Stores nothing; commit(plan) does the write. See bond.plans."""
        data, fmt = _encoded(content, format)
        return self._plan_written(data, fmt, document, citations, base, policy, title, None)

    def _issues(
        self, content: bytes, format: Format, document: str, citations: Sequence[Citation], base: VersionId | None,
        policy: Policy, title: str | None,
    ) -> list[Issue]:
        issues = _label_issues(document, title) + self._check(content, format, citations, policy, frozenset())
        version = version_id(hash_bytes(content), format, list(citations))
        if conflict := filing_problem(self._filing.document(document), document, base, version):
            issues.append(_conflict_issue(conflict))
        return issues

    def _plan_written(
        self, content: bytes, format: Format, document: str, citations: Sequence[Citation], base: VersionId | None,
        policy: Policy, title: str | None, origin: str | None,
    ) -> WritePlan:
        if issues := self._issues(content, format, document, citations, base, policy, title):
            raise WriteError(issues)
        content_hash = hash_bytes(content)
        version = version_id(content_hash, format, list(citations))
        new = NewVersion(version, content_hash, format, content, list(citations))
        return WritePlan(self.workspace.id, new, FilingChange(document, version, base, origin, title),
                         tuple(self._superseded(citations)), _SEAL)

    def _plan_filing(self, new: NewVersion | None, filing: FilingChange) -> WritePlan:
        """A plan that files a version stored already, checked for its label and filing."""
        issues = _label_issues(filing.document, filing.title)
        conflict = filing_problem(self._filing.document(filing.document), filing.document, filing.base, filing.version)
        if conflict:
            issues.append(_conflict_issue(conflict))
        if issues:
            raise WriteError(issues)
        return WritePlan(self.workspace.id, new, filing, _seal=_SEAL)

    def commit(self, plan: WritePlan) -> WriteResult:
        """Do a planned write: store its version (blob, then record), file it (its history entry, then its title),
        and say what changed. Raises WriteError(stale_base) if the document moved since the plan was made, having
        stored no version. Committing again is harmless.

        Only a plan plan_write or plan_ingest made is committed: commit doesn't check it again, so it refuses one
        made any other way (ValueError). A plan is kept only in memory: to commit later or elsewhere, keep what
        was planned (document, content, citations, base) and plan it again there."""
        if plan._seal is not _SEAL:
            raise ValueError("this plan wasn't made by Bond's checks (plan_write or plan_ingest); plan it here")
        if plan.workspace != self.workspace.id:
            raise ValueError(f"this plan was made in another workspace ({plan.workspace}), not {self.workspace.id}")
        new = plan.new
        if new is not None and new.id != plan.filing.version:
            raise BondError(f"the plan files version {plan.filing.version[:12]}…, but holds {new.id[:12]}…")
        if new is not None and (hash_bytes(new.data) != new.content_hash
                                or version_id(new.content_hash, new.format, list(new.citations)) != new.id):
            raise BondError(f"the plan's version {new.id[:12]}… doesn't hash to its id")
        before = None
        if new is not None:
            self.blobs.put(new.content_hash, new.data)  # before any record names it
            before = partial(self._records.put, new)  # stored with its filing, only if that goes through
        f = plan.filing
        filed = self._filing.file(f.version, f.document, f.base, origin=f.origin, before=before)
        if isinstance(filed, Conflict):  # another write moved the document since the plan was made
            raise WriteError([_conflict_issue(filed)])
        if f.title is not None and (filed.advanced or f.base == f.version or self._untitled_since_filed(f.document)):
            self._filing.retitle(f.document, f.title)
        return WriteResult(f.version, filed.advanced, plan.warnings)

    def _untitled_since_filed(self, document: str) -> bool:
        """For a plan committed again, its version already the head: whether no title came since it was filed, so
        its title is still to apply (as after a crash between filing and titling). A later title wins over a
        replay; a plan made to relabel the head (its base its version) applies its title whatever came before."""
        head = self._filing.get(document)
        return head is not None and self._filing.titled_after(head.ref.id) < head.seq

    def _check(
        self, content: bytes, format: Format, citations: Sequence[Citation], policy: Policy,
        unchecked: frozenset[VersionId],
    ) -> list[Issue]:
        """The issues a write of this content and these citations would have, except that supports into the
        versions unchecked (ones verify can't read: unreachable, or with bytes it reports itself) are
        taken as written: their citations still cover their claims, but nothing that needs what they rest on,
        like a quote's text, can be checked here."""
        issues: list[Issue] = []
        parsed = parse(format, content)
        doc: ParsedDoc | None = None
        if isinstance(parsed, Malformed):
            issues.append(Issue(IssueCode.INVALID_CONTENT, parsed.reason))
        else:
            doc = parsed

        # A citation whose locators are invalid is left out of the loop check and covers nothing.
        located: list[tuple[int, Citation]] = []
        for i, c in enumerate(citations):
            locator_issues = self._check_locators(i, c, doc, unchecked)
            issues += locator_issues
            if locator_issues:
                continue
            if doc is not None and not any(isinstance(s, Ref) and s.version in unchecked for s in c.supports):
                issues += self._check_kind(i, c, doc)
            located.append((i, c))
        issues += _circular_support_errors(located)

        if doc is not None:
            issues += policy.issues(doc, located)
        return issues

    def _check_locators(
        self, i: int, c: Citation, doc: ParsedDoc | None, unchecked: frozenset[VersionId],
    ) -> list[Issue]:
        """Problems with where citation i's claim and supports point; supports into unchecked are not looked at."""
        errors = []
        if doc is not None and (problem := doc.check(c.claim)):
            errors.append(Issue(IssueCode.BAD_LOCATOR, f"claim: {problem}", citation_index=i, locator=c.claim))
        for j, support in enumerate(c.supports):
            match support:
                case SelfRef():
                    problem = doc.check(support.locator) if doc is not None else None
                case Ref() if support.version in unchecked:
                    continue
                case Ref() if self._owner(support.version) is None:
                    errors.append(Issue(
                        IssueCode.UNKNOWN_VERSION, f"version {support.version!r} isn't in this workspace or any it "
                        "links to", citation_index=i, support_index=j, locator=support.locator,
                        hint="find versions with search() or document(name).head; use SelfRef for this version",
                    ))
                    continue
                case Ref():
                    problem = self._locator_problem(support)
            if problem:
                errors.append(Issue(IssueCode.BAD_LOCATOR, problem, citation_index=i, support_index=j,
                                    locator=support.locator))
        return errors

    def _check_kind(self, i: int, c: Citation, doc: ParsedDoc) -> list[Issue]:
        """Problems citation i's kind finds with it."""
        def text_at(support: Support) -> str | None:
            return (doc if isinstance(support, SelfRef) else self._doc(support.version)).text(support.locator)

        return kinds.validate_citation(c, i, text_at)

    # --- filing --------------------------------------------------------------

    def document(self, name: str) -> DocumentRef:
        """The document with this name, with its head; raises UnknownDocument if there is none (find doesn't)."""
        doc = self._filing.document(name)
        if doc is None:
            raise UnknownDocument(name)
        return doc

    def find(self, name: str) -> DocumentRef | None:
        """The document with this name, with its head; None if there is none."""
        return self._filing.document(name)

    def retitle(self, name: str, title: str | None) -> DocumentRef:
        """Give a document a new title for people to read it by (None for none), changing no version or provenance.
        The latest title wins. Raises UnknownDocument; ValueError for a blank title."""
        if title is not None and not title.strip():
            raise ValueError("a document's title must be a non-empty string, or None")
        return self._filing.retitle(name, title)

    def documents(self) -> list[DocumentRef]:
        """Every document, oldest first."""
        return self._filing.documents()

    def history(self, name: str) -> list[HistoryEntry]:
        """A document's history, oldest first: the versions it pointed at, and the base each was written from."""
        return self._filing.history(self.document(name).id)

    # --- citations / trace ---------------------------------------------------

    def citations(self, version: VersionId) -> list[StoredCitation]:
        """The citations a version was written with, in reading order of their claims (ties by id).

        A version's citations are a set; this order is only for showing them.
        """
        self._version(version)
        doc = self._doc(version)
        return sorted(self._citations_of(version), key=lambda c: (doc.position(c.claim), c.id))

    @_one_view
    def trace(self, ref: Ref, max_depth: int | None = None, limit: int = 1000) -> Trace:
        """Follow provenance from ref down to its sources, as a DAG, and say whether it is supported: one walk.

        A node (V, L) has an edge for every citation owned by V whose claim
        covers part of L; the citation's supports are the next nodes, which may be in
        workspaces this one links to. A chain ends at a SOURCE or UNSOURCED edge
        (no supports), or at a node with no edges (uncited), or at a version no
        reachable workspace holds: those are listed as unreachable. Each distinct ref is one
        node, at its shortest distance from ref; max_depth bounds that distance,
        and limit the number of nodes, the root included (the nearest are kept).
        The trace's support is check_support's report of the same walk. Raises
        ValueError if ref's locator isn't valid in its version.
        """
        self._check_ref(ref)
        walked = walk(_Graph(self), ref, max_depth, limit)
        nodes, edges, complete = walked.nodes, walked.edges, walked.complete
        owners = {v: self._owner(v) for v in dict.fromkeys(n.version for n in nodes)}
        unreachable = frozenset(v for v, owner in owners.items() if owner is None)
        newer: dict[VersionId, dict[str, VersionId]] = {}
        for v, held in owners.items():
            if held is not None and (moved := {d.name: d.head for d in held._index.holding(v) if d.head != v}):
                newer[v] = moved
        return Trace(ref, nodes, edges, complete, unreachable, newer, walked.report)

    @_one_view
    def cited_by(self, ref: Ref) -> list[StoredCitation]:
        """Citations, from any version in this workspace, with a support in ref.version that overlaps ref.locator.

        The reverse of one hop of trace, within this workspace: what cites a version of a linked workspace is
        asked here, never there (a library can't list the cases that cite it). A VersionLocator matches every support
        in the version. Raises ValueError if ref's locator isn't valid in its version.
        """
        self._check_ref(ref)
        return [
            c for c in self._index.supported_by(ref.version)
            if any(s.version == ref.version and overlaps(s.locator, ref.locator) for s in c.supports)
        ]

    @_one_view
    def transcriptions_of(self, version: VersionId) -> list[TranscriptionOf]:
        """Versions whose TRANSCRIPTION citations rest on version: its transcriptions, found by what they cite,
        never by their names, in the workspace that holds version. Each with its producer and where it is filed.
        Raises UnknownVersion."""
        if (owner := self._held(version)) is not self:
            return owner.transcriptions_of(version)
        found: dict[VersionId, str | None] = {}
        for c in self.cited_by(Ref(version, VersionLocator())):
            if c.kind is CitationKind.TRANSCRIPTION:
                found.setdefault(c.owner_version, c.producer)
        transcriptions = []
        for v, producer in found.items():
            holding = self._index.holding(v)
            transcriptions.append(TranscriptionOf(v, producer, [d.name for d in holding],
                                                  [d.name for d in holding if d.head == v]))
        return transcriptions

    @_one_view
    def _superseded(self, citations: Sequence[Citation]) -> list[Issue]:
        """A CITES_SUPERSEDED warning for each support, in a version this workspace can read and valid in it, that
        is filed and that no document holding it has as its head any more: there may be newer evidence to look at.

        Only ever a warning. Citing an older version is often right (the text a decision was made on), and only
        the writer knows which it meant; the citation stays exactly true either way, since versions never change.
        """
        found: list[Issue] = []
        for i, c in enumerate(citations):
            for j, s in enumerate(c.supports):
                if not isinstance(s, Ref) or (owner := self._owner(s.version)) is None or self._locator_problem(s):
                    continue
                holding = owner._index.holding(s.version)
                if holding and all(d.head != s.version for d in holding):
                    moved = "; ".join(f"{d.name!r} has moved past it to {d.head[:12]}…" for d in holding)
                    found.append(Issue(
                        IssueCode.CITES_SUPERSEDED,
                        f"cites version {s.version[:12]}…, which no document holds as its head now: {moved}",
                        citation_index=i, support_index=j, locator=c.claim,
                        hint="search the newer version and cite it instead, unless the older one is what you mean"))
        return found

    # --- verify --------------------------------------------------------------

    def verify(self, version: VersionId, policy: Policy = STRICT) -> list[Issue]:
        """Check a version and every version its provenance rests on; returns the issues found, each with its version.

        For each version: its blob is there and hashes to its content hash, and
        its content hash, format and citations hash to its id. A version's id
        commits to the ids of the versions it cites, so an expected id anchors
        integrity checks of the available chain. Then each version is checked again as it was
        written, with check() under policy: hashes show nothing changed since it
        was stored, and check shows it met the rules, whatever wrote it.

        Rules are today's and policy is the one supplied now; the historical policy is not stored.
        Neither source authority nor semantic support is evaluated. Document names, histories and timestamps are not covered.

        Versions in workspaces this one links to are checked as well. A version
        no reachable workspace holds can't be checked here: it is reported once,
        as UNREACHABLE, and the
        citations resting on it are checked as far as they can be without it,
        as are those resting on a version whose own bytes are missing or damaged,
        which that version reports. So an empty list still means everything
        beneath version was checked. Raises UnknownVersion if version itself
        isn't in this workspace or one it links to.
        """
        if self._owner(version) is None:
            raise UnknownVersion(version)
        issues: list[Issue] = []
        seen: set[VersionId] = set()
        unreachable: dict[VersionId, None] = {}
        stack = [version]
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            owner = self._owner(v)
            record = None if owner is None else owner._records.as_stored(v)  # checked against v, whatever it names
            if owner is None or record is None:
                unreachable[v] = None
                continue
            stored, cited = record.version, record.citations
            beneath = [s.version for c in cited for s in c.supports if s.version != v]
            unchecked = frozenset(s for s in set(beneath) if not self._intact(s))
            issues += [replace(i, version=v) for i in self._verify_one(owner, stored, cited, policy, unchecked)]
            stack += beneath
        return issues + [
            Issue(IssueCode.UNREACHABLE, "this version is in neither this workspace nor any it links to, so what "
                  "rests on it can't be checked here", hint="link this workspace to the one that holds it", version=v)
            for v in unreachable
        ]

    def _verify_one(
        self, owner: Bond, version: Version, cited: list[StoredCitation], policy: Policy,
        unchecked: frozenset[VersionId],
    ) -> list[Issue]:
        """Issues with one version, held by owner, not with the versions it cites."""
        v = version.id
        issues = []
        written = [c.as_written() for c in cited]
        for c, w in zip(cited, written, strict=True):
            if citation_id(w) != c.id:
                issues.append(Issue(IssueCode.ID_MISMATCH, f"citation {c.id[:12]}… does not hash to its id"))
        if version_id(version.content_hash, version.format, written) != v:
            issues.append(Issue(IssueCode.ID_MISMATCH, "its content hash, format and citations do not hash to its id"))
        content = owner.blobs.get(version.content_hash)
        if content is None:
            return issues + [Issue(IssueCode.BLOB_MISSING, "its blob is missing")]
        if hash_bytes(content) != version.content_hash:
            issues.append(Issue(IssueCode.BLOB_MISMATCH, "its blob does not match its content hash"))
        return issues + self._check(content, version.format, written, policy, unchecked)

    def _intact(self, version: VersionId) -> bool:
        """Whether what rests on a version can be checked against it: it is reachable, and its bytes are there,
        hash to its content hash and are well-formed."""
        owner = self._owner(version)
        record = None if owner is None else owner._records.as_stored(version)
        if owner is None or record is None or record.stored_id != version:
            return False
        stored = record.version
        data = owner.blobs.get(stored.content_hash)
        return (data is not None and hash_bytes(data) == stored.content_hash
                and not isinstance(self._load(version), Malformed))

    # --- helpers -------------------------------------------------------------

    def _load_version(self, version: VersionId) -> ParsedDoc | Malformed:
        """A stored version, parsed, or what is wrong with its bytes. Read through the self._load cache, which can't
        go stale: versions are immutable."""
        owner = self._held(version)
        stored = owner._version(version)
        data = owner.blobs.get(stored.content_hash)
        return Malformed("its blob is missing") if data is None else parse(stored.format, data)

    def _doc(self, version: VersionId) -> ParsedDoc:
        """A stored version, parsed; raises CorruptVersion if its bytes are missing or malformed."""
        loaded = self._load(version)
        if isinstance(loaded, Malformed):
            raise CorruptVersion(version, loaded.reason)
        return loaded

    def _check_ref(self, ref: Ref) -> None:
        """Raise UnknownVersion if ref's version doesn't exist, or ValueError if its locator isn't valid in it."""
        self._version(ref.version)
        if problem := self._locator_problem(ref):
            raise ValueError(problem)

    def _locator_problem(self, ref: Ref) -> str | None:
        """Why ref's locator isn't valid in its version, which must exist; None if it is."""
        return self._doc(ref.version).check(ref.locator)


@dataclass(frozen=True)
class _Graph:
    """A workspace as a support check reads it (support.Graph)."""

    bond: Bond

    def parsed(self, version: VersionId) -> ParsedDoc | None:
        return None if self.bond._owner(version) is None else self.bond._doc(version)

    def citations(self, version: VersionId) -> list[StoredCitation]:
        return self.bond.citations(version)


# What commit checks a plan was made by Bond's checks: only this module sets it (see bond.plans).
_SEAL = object()

# The workspaces a workspace links to: opened workspaces, or a function giving them now (see Bond.set_links).
Links = Iterable[Bond] | Callable[[], Iterable[Bond]]


def _encoded(content: bytes | str, format: Format | None) -> tuple[bytes, Format]:
    """Content as stored, UTF-8 for text, and its format, sniffed if not given."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return data, format or sniff(data)


def _conflict_issue(conflict: Conflict) -> Issue:
    return Issue(conflict.code, conflict.message, hint=conflict.hint)


def _label_issues(document: str, title: str | None) -> list[Issue]:
    """Problems with the name a write files under, and the title it gives the document."""
    issues = []
    if not document.strip():
        issues.append(Issue(IssueCode.INVALID_NAME, "a document's name must be a non-empty string"))
    if title is not None and not title.strip():
        issues.append(Issue(IssueCode.INVALID_NAME, "a document's title must be a non-empty string, or None"))
    return issues


def _circular_support_errors(citations: list[tuple[int, Citation]]) -> list[Issue]:
    errors = []
    for cycle in _support_cycles(citations):
        path = " -> ".join(str(i) for i in cycle + [cycle[0]])
        errors.append(Issue(
            IssueCode.CIRCULAR_SUPPORT, f"citations {path} support each other in a loop", citation_index=cycle[0],
            cycle=tuple(cycle), hint="a claim cannot rest, directly or through other claims in this document, on itself",
        ))
    return errors


def _support_cycles(citations: list[tuple[int, Citation]]) -> list[list[int]]:
    """Loops among citations, by index, through supports into the version being written.

    Citation i rests on citation j if one of i's self-supports overlaps j's
    claim; this is exactly the edge trace() will follow.
    """
    rests_on = _self_support_graph(citations)
    cycles: list[list[int]] = []
    done: set[int] = set()
    # Depth first, without recursion: a chain of self-supports may be longer than Python's stack.
    for start in rests_on:
        if start in done:
            continue
        on_path, next_of = [start], [iter(rests_on[start])]
        while next_of:
            j = next(next_of[-1], None)
            if j is None:
                done.add(on_path.pop())
                next_of.pop()
            elif j in on_path:
                cycles.append(on_path[on_path.index(j):])
            elif j not in done:
                on_path.append(j)
                next_of.append(iter(rests_on[j]))
    return cycles


def _self_support_graph(citations: list[tuple[int, Citation]]) -> dict[int, list[int]]:
    """For each citation, the citations of the same version it rests on."""
    return {
        i: [j for s in c.supports if isinstance(s, SelfRef) for j, o in citations if overlaps(o.claim, s.locator)]
        for i, c in citations
    }
