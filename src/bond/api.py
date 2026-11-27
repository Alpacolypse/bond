"""The core API of one workspace, in two layers.

Evidence: ingest, write_document (and check_write), search, declarations, check_support, citations, trace, cited_by
and verify, plus blob and text to read what a version holds.
Filing: document, documents and history. Writes go through both: each stores a version and files it in a document.
Between workspaces: import_document brings a document in from another workspace, import_version a version an
import left outside, and frontier lists what was left outside.

Lower-level reads (version records, where a span is, JSON pointers) are in bond.details, and whole-workspace
reads for the viewer in bond.http.viewer, both on top of these."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache, partial
from pathlib import Path
from typing import Concatenate

from . import kinds
from .formats import from_suffix, parse, sniff, spec
from .ids import citation_id, hash_bytes, version_id
from .locators import JsonLocator, Locator, VersionLocator, contains, overlaps
from .models import (
    BondError,
    Citation,
    CitationKind,
    CorruptVersion,
    Declarations,
    DocumentFreshness,
    DocumentRef,
    Format,
    Freshness,
    HistoryEntry,
    Hit,
    Imported,
    Issue,
    IssueCode,
    Ref,
    SelfRef,
    SpanFate,
    SpanStatus,
    StoredCitation,
    Support,
    SupportReport,
    Trace,
    TraceEdge,
    TranscriptionOf,
    UnknownDocument,
    UnknownVersion,
    Version,
    VersionId,
    Workspace,
    WriteError,
    WriteResult,
)
from .parsers import JsonDoc, Malformed, ParsedDoc
from .plans import FilingChange, PlannedVersion, WritePlan, checked
from .policies import STRICT, Policy
from .storage import Blobs, Conflict, NewVersion, filing_problem
from .storage.filing import Filing
from .storage.index import MemoryIndex
from .storage.records import Records
from .storage.store import Store
from .storage.workspace import open_workspace
from .support import check_support, partition

# Parsed documents kept in memory by default. Versions are immutable, so any eviction is safe.
DEFAULT_PARSED_CACHE_SIZE = 256



def _one_view[**P, R](method: Callable[Concatenate[Bond, P], R]) -> Callable[Concatenate[Bond, P], R]:
    """Run a query against one catch-up of the index, however many lookups it makes."""

    @functools.wraps(method)
    def wrapper(self: Bond, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.index.view():
            return method(self, *args, **kwargs)

    return wrapper

class Bond:
    """One provenance-enforcing workspace: its keys in a store, its bytes in blobs.

    A workspace is a closed world, like a case: writes cite versions in it.
    Imports preserve references to dependencies that have not yet been copied.
    Safe to share across threads, with any backends: Bond holds no connection
    or transaction of its own. See bond.storage for the backends.
    """

    def __init__(
        self, store: Store, blobs: Blobs, workspace: str | None = None, *, create: bool = True,
        parsed_cache_size: int = DEFAULT_PARSED_CACHE_SIZE,
    ):
        """The workspace named workspace in store (created if missing, unless create is False; with no name, the only
        one there is), its bytes in blobs, keeping up to parsed_cache_size parsed versions in memory.

        The store and blobs stay the caller's: close() leaves them open."""
        self.store = store
        self.blobs = blobs
        self.workspace: Workspace = open_workspace(store, workspace, create)  # the workspace this Bond reads and writes
        self.records = Records(store, self.workspace.id)  # its versions
        self.filing = Filing(store, self.workspace.id)  # its documents
        self.index = MemoryIndex(self.records, self.filing)  # reverse lookups, derived from both
        self._owned: list[object] = []  # what this Bond opened itself, and closes
        # Per instance, so the cache doesn't keep this Bond alive after it is closed.
        self._load = lru_cache(maxsize=parsed_cache_size)(self._load_version)

    @classmethod
    def local(
        cls, root: str | os.PathLike[str], parsed_cache_size: int = DEFAULT_PARSED_CACHE_SIZE,
        create: bool = True,
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
            bond = cls(store, LocalBlobs(root / "blobs"), name, create=create, parsed_cache_size=parsed_cache_size)
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
        if self.records.version(version):  # stored already: only filing it is left, so skip parsing it again
            return self._plan_filing((), FilingChange(document, version, base, None, origin, title), STRICT)
        return self._plan_written(data, fmt, document, whole, base, STRICT, title, origin)

    # --- reading versions -----------------------------------------------------

    def blob(self, version: VersionId) -> bytes:
        """A version's raw bytes, in any format; raises CorruptVersion if they are missing."""
        stored = self._version(version)
        data = self.blobs.get(stored.content_hash)
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
        """A stored version's record; raises UnknownVersion if there is none."""
        stored = self.records.version(version)
        if stored is None:
            raise UnknownVersion(version)
        return stored

    # --- search --------------------------------------------------------------

    @_one_view
    def search(
        self, phrase: str, *, documents: list[str] | None = None, within: Ref | None = None, limit: int = 50,
    ) -> list[Hit]:
        """Find a phrase in the current versions of documents and pin each match to a locator.

        Searches the heads of all documents, or of the given documents, or only inside one reference (within).
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
        heads = {d.head for d in self.filing.documents()}
        return [v for v in self.index.versions() if v.id in heads]

    def _search_version(self, version: Version, phrase: str) -> list[Hit]:
        matches = self._doc(version.id).search(phrase)
        if not matches:
            return []
        owned = self.records.citations(version.id)
        holding = self.index.holding(version.id)
        names = [d.name for d in holding]
        newer = {d.name: d.head for d in holding if d.head != version.id}
        return [
            Hit(Ref(version.id, m.locator), list(names), version.format, m.snippet, m.context,
                self._declarations(Ref(version.id, m.locator), owned), dict(newer))
            for m in matches
        ]

    def declarations(self, ref: Ref) -> Declarations:
        """Declarations overlapping a valid selection, with exact undeclared gaps.

        Citations retain their original subject spans. No recursive judgment is made. Text follows
        format coverage rules; PDF selections use geometric coverage rather than text extraction.
        """
        self._check_ref(ref)
        return self._declarations(ref, self.records.citations(ref.version))

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

        A caller that expects refusals, like an agent's tools, asks check_write first.
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

    def check_write(
        self,
        document: str,
        content: bytes | str,
        citations: Sequence[Citation],
        *,
        format: Format | None = None,
        base: VersionId | None = None,
        policy: Policy = STRICT,
        title: str | None = None,
    ) -> list[Issue]:
        """Every issue write_document would refuse the same write for, filing included; nothing is stored.

        Empty if the write would succeed now, though another write may yet move the document first.
        """
        data, fmt = _encoded(content, format)
        return self._issues(data, fmt, document, citations, base, policy, title)

    def _issues(
        self, content: bytes, format: Format, document: str, citations: Sequence[Citation], base: VersionId | None,
        policy: Policy, title: str | None,
    ) -> list[Issue]:
        issues = _label_issues(document, title) + self._check(content, format, citations, policy, frozenset())
        if policy.refuse_superseded:
            issues += self._superseded(citations)
        version = version_id(hash_bytes(content), format, list(citations))
        if conflict := filing_problem(self.filing.document(document), document, base, version):
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
        return checked(WritePlan(self.workspace.id, (PlannedVersion(new),),
                                 FilingChange(document, version, base, None, origin, title), policy,
                                 warnings=tuple(self._superseded(citations))))

    def _plan_filing(
        self, versions: tuple[PlannedVersion, ...], filing: FilingChange | None, policy: Policy,
    ) -> WritePlan:
        """A plan that files a version stored already (or brought with it), checked for its label and filing."""
        if filing is not None:
            issues = _label_issues(filing.document, filing.title)
            conflict = filing_problem(self.filing.document(filing.document), filing.document, filing.base,
                                      filing.version)
            if conflict:
                issues.append(_conflict_issue(conflict))
            if issues:
                raise WriteError(issues)
        return checked(WritePlan(self.workspace.id, versions, filing, policy))

    def commit(self, plan: WritePlan) -> WriteResult:
        """Do a planned write: store its versions (blobs, then records), file one (its history entry, then its
        title), and say what changed. Raises WriteError(stale_base) if the document moved since the plan was made,
        having stored no version it files. A plan that wasn't made by this process's checks (one loaded from JSON)
        is checked again first, and refused with every issue as a write would be. Committing again is harmless."""
        if plan.workspace != self.workspace.id:
            raise ValueError(f"this plan was made in another workspace ({plan.workspace}), not {self.workspace.id}")
        for pv in plan.versions:
            new = pv.new
            if hash_bytes(new.data) != new.content_hash or version_id(
                    new.content_hash, new.format, list(new.citations)) != new.id:
                raise BondError(f"the plan's version {new.id[:12]}… doesn't hash to its id")
        if not plan.checked:
            self._recheck(plan)
        filed_new = None
        stored = False
        for pv in plan.versions:
            self.blobs.put(pv.new.content_hash, pv.new.data)  # before any record names it
            if pv.imported:
                self.records.mark_imported(pv.new.id)
            if plan.filing is not None and pv.new.id == plan.filing.version:
                filed_new = pv.new  # stored with its filing, only if that goes through
            else:
                stored |= self.records.put(pv.new)
        if plan.filing is None:  # an import that files nothing: advanced says whether it stored anything
            return WriteResult(plan.version or VersionId(""), stored)
        f = plan.filing
        before = None if filed_new is None else partial(self.records.put, filed_new)
        filed = self.filing.file(f.version, f.document, f.base, imported=f.imported, origin=f.origin, before=before)
        if isinstance(filed, Conflict):  # another write moved the document since the plan was made
            raise WriteError([_conflict_issue(filed)])
        if f.title is not None and (filed.advanced or f.base == f.version or self._untitled_since_filed(f.document)):
            self.filing.retitle(f.document, f.title)
        return WriteResult(f.version, filed.advanced, plan.warnings)

    def _untitled_since_filed(self, document: str) -> bool:
        """For a plan committed again, its version already the head: whether no title came since it was filed, so
        its title is still to apply (as after a crash between filing and titling). A later title wins over a
        replay; a plan made to relabel the head (its base its version) applies its title whatever came before."""
        head = self.filing.get(document)
        return head is not None and self.filing.titled_after(head.ref.id) < head.seq

    def _recheck(self, plan: WritePlan) -> None:
        """Check a plan from outside as the write it plans would be checked, and that it is whole: each version
        once, and the version it files among them or stored here already. Its policy and imported marks are
        taken as written: ids show what a plan holds, not who may commit it, so a host loads only plans it
        staged itself."""
        issues: list[Issue] = []
        ids = [pv.new.id for pv in plan.versions]
        if len(set(ids)) != len(ids):
            raise BondError("the plan holds a version more than once")
        for pv in plan.versions:
            if not pv.imported:
                issues += self._check(pv.new.data, pv.new.format, pv.new.citations, plan.policy, frozenset())
                if plan.policy.refuse_superseded:
                    issues += self._superseded(pv.new.citations)
        if plan.filing is not None:
            if plan.filing.version not in ids and self.records.version(plan.filing.version) is None:
                raise BondError(f"the plan files version {plan.filing.version[:12]}…, which it doesn't hold and "
                                "isn't stored here")
            issues += _label_issues(plan.filing.document, plan.filing.title)
        if issues:
            raise WriteError(issues)

    def stage(self, plan: WritePlan) -> str:
        """Put a plan's bytes in this workspace's blobs, and return the plan as JSON, to keep and commit later."""
        for content_hash, data in plan.blobs().items():
            self.blobs.put(content_hash, data)
        return plan.to_json()

    def load_plan(self, text: str) -> WritePlan:
        """A plan stage returned, with its bytes read from this workspace's blobs; commit checks it again."""
        return WritePlan.from_json(text, self.blobs)

    def _check(
        self, content: bytes, format: Format, citations: Sequence[Citation], policy: Policy,
        unchecked: frozenset[VersionId],
    ) -> list[Issue]:
        """The issues a write of this content and these citations would have, except that supports into the
        versions unchecked (ones verify can't read: outside the workspace, or with bytes it reports itself) are
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
                case Ref() if self.records.version(support.version) is None:
                    errors.append(Issue(
                        IssueCode.UNKNOWN_VERSION, f"version {support.version!r} does not exist", citation_index=i,
                        support_index=j, locator=support.locator,
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
        """The document with this name, with its head; raises UnknownDocument if there is none.

        To ask whether there is one, filing.document(name) returns None instead.
        """
        doc = self.filing.document(name)
        if doc is None:
            raise UnknownDocument(name)
        return doc

    def documents(self) -> list[DocumentRef]:
        """Every document, oldest first."""
        return self.filing.documents()

    def history(self, name: str) -> list[HistoryEntry]:
        """A document's history, oldest first: the versions it pointed at, and the base each was written from."""
        return self.filing.history(self.document(name).id)

    # --- between workspaces ----------------------------------------------------

    def import_document(
        self, source: Bond, document: str, *, name: str | None = None, base: VersionId | None = None,
        depth: int | None = 0, limit: int | None = None,
    ) -> WriteResult:
        """Copy a document's head from source, preserving its bytes, citations and identity, and file it here
        under name (by default, the same name), from base as in write_document.

        depth counts dependency hops copied with it (None for all). limit bounds distinct versions visited,
        including versions already here. Dependencies left out remain an explicit outside-workspace frontier
        (frontier()); importing one of them later changes nothing here, since its id is already committed to.
        Import never promotes derived or unsourced text to a source declaration.
        """
        return self.commit(self.plan_import(source, document, name=name, base=base, depth=depth, limit=limit))

    def plan_import(
        self, source: Bond, document: str, *, name: str | None = None, base: VersionId | None = None,
        depth: int | None = 0, limit: int | None = None,
    ) -> WritePlan:
        """The plan import_document commits: the versions it copies, each checked against its id in source, and
        the filing. Raises WriteError for a filing that would be refused. Stores nothing."""
        _check_other(self, source)
        head = source.document(document).head
        imported = Imported(source.workspace.id, source.workspace.name, head)
        versions = self._bring(source, [head], depth, limit)
        return self._plan_filing(versions, FilingChange(document if name is None else name, head, base, imported),
                                 STRICT)

    def import_version(
        self, source: Bond, version: VersionId, *, depth: int | None = 0, limit: int | None = None,
    ) -> WriteResult:
        """Copy a version an earlier import left outside (see frontier()) from source, without filing it, and what
        it rests on as import_document does. advanced says whether anything was copied."""
        result = self.commit(self.plan_import_version(source, version, depth=depth, limit=limit))
        return WriteResult(version, result.advanced)

    def plan_import_version(
        self, source: Bond, version: VersionId, *, depth: int | None = 0, limit: int | None = None,
    ) -> WritePlan:
        """The plan import_version commits. Stores nothing."""
        _check_other(self, source)
        if self.records.version(version) is None and not self.index.supported_by(version):
            raise ValueError(f"nothing in this workspace rests on version {version[:12]}…; import a document "
                             "by name instead")
        return replace(self._plan_filing(self._bring(source, [version], depth, limit), None, STRICT), subject=version)

    def frontier(self) -> list[VersionId]:
        """Versions that something in this workspace rests on but that aren't in it: the frontier imports left."""
        return self.index.outside()

    def _bring(
        self, source: Bond, versions: list[VersionId], depth: int | None, limit: int | None,
    ) -> tuple[PlannedVersion, ...]:
        """The versions to copy from source: these, then what they rest on, depth hops down; those already here are
        kept, but what they rest on is still followed. Each before any that rests on it. Stores nothing."""
        if depth is not None and depth < 0:
            raise ValueError("depth must be non-negative or None")
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        brought: list[PlannedVersion] = []
        seen: set[VersionId] = set()
        level, hops = versions, 0
        while level:
            following: list[VersionId] = []
            for v in level:
                if v in seen:
                    continue
                if limit is not None and len(seen) >= limit:
                    return tuple(reversed(brought))
                seen.add(v)
                cited = self._copy(source, v, brought)
                following += [s.version for c in cited for s in c.supports if s.version != v]
            if depth is not None and hops >= depth:
                break
            level, hops = following, hops + 1
        return tuple(reversed(brought))

    def _copy(self, source: Bond, v: VersionId, brought: list[PlannedVersion]) -> list[StoredCitation]:
        """Add one version from source to brought unless it is here; its citations, none if source doesn't have it
        either (it stays on the frontier)."""
        if self.records.version(v) is not None:
            return self.records.citations(v)
        if source.records.version(v) is None:
            return []
        new = _checked_import(source, v)
        brought.append(PlannedVersion(new, imported=True))
        return source.records.citations(v)

    # --- citations / trace ---------------------------------------------------

    def citations(self, version: VersionId) -> list[StoredCitation]:
        """The citations a version was written with, in reading order of their claims (ties by id).

        A version's citations are a set; this order is only for showing them.
        """
        self._version(version)
        doc = self._doc(version)
        return sorted(self.records.citations(version), key=lambda c: (doc.position(c.claim), c.id))

    @_one_view
    def trace(self, ref: Ref, max_depth: int | None = None, limit: int | None = None) -> Trace:
        """Follow provenance from ref down to its sources, as a DAG.

        A node (V, L) has an edge for every citation owned by V whose claim
        overlaps L; the citation's supports are the next nodes. A chain ends at a
        SOURCE or UNSOURCED edge (no supports), or at a node with no edges
        (uncited), or at a version outside this workspace, which an import left
        behind: those are listed in the trace's frontier. Each distinct ref is one
        node, at its shortest distance from ref; max_depth bounds that distance,
        and limit the number of nodes (the nearest are kept). Raises ValueError if
        ref's locator isn't valid in its version.
        """
        self._check_ref(ref)
        owned: dict[VersionId, list[StoredCitation]] = {}
        outside: set[VersionId] = set()

        def down(node: Ref) -> list[tuple[StoredCitation, list[Ref]]]:
            if node.version not in owned:
                here = self.records.version(node.version) is not None
                owned[node.version] = self.records.citations(node.version) if here else []
                if not here:
                    outside.add(node.version)
            return [(c, c.supports) for c in _edges_at(owned[node.version], node.locator)]

        nodes, edges, complete = _walk(ref, down, max_depth, limit)
        # A node past max_depth was never stepped into; find out whether it is here all the same.
        outside |= {n.version for n in nodes if n.version not in owned and self.records.version(n.version) is None}
        newer: dict[VersionId, dict[str, VersionId]] = {}
        for v in dict.fromkeys(n.version for n in nodes):
            if moved := {d.name: d.head for d in self.index.holding(v) if d.head != v}:
                newer[v] = moved
        return Trace(ref, nodes, edges, complete, frozenset(outside), newer)

    @_one_view
    def cited_by(self, ref: Ref) -> list[StoredCitation]:
        """Citations, from any version, with a support in ref.version that overlaps ref.locator.

        The reverse of one hop of trace. A VersionLocator matches every support
        in the version. Raises ValueError if ref's locator isn't valid in its version.
        """
        self._check_ref(ref)
        return [
            c for c in self.index.supported_by(ref.version)
            if any(s.version == ref.version and overlaps(s.locator, ref.locator) for s in c.supports)
        ]

    @_one_view
    def transcriptions_of(self, version: VersionId) -> list[TranscriptionOf]:
        """Versions whose TRANSCRIPTION citations rest on version: its transcriptions, found by what they cite,
        never by their names. Each with its producer and where it is filed. Raises UnknownVersion."""
        self._version(version)
        found: dict[VersionId, str | None] = {}
        for c in self.cited_by(Ref(version, VersionLocator())):
            if c.kind is CitationKind.TRANSCRIPTION:
                found.setdefault(c.owner_version, c.attrs.get("transcriber"))
        return [TranscriptionOf(v, producer, [d.name for d in self.index.holding(v)])
                for v, producer in found.items()]

    @_one_view
    def freshness(self, ref: Ref, document: str | None = None, as_of: str | None = None) -> Freshness:
        """Whether ref's version is still current in each document that holds it (or only in document), computed
        now, never stored. For a version a document has moved past: its head, when it moved, and, for a span
        narrower than the whole version, whether the span's text is in the head (where, if so). as_of, a time
        as Bond records them, also asks whether the version was the document's head then. Raises ValueError if
        ref's locator isn't valid in its version.

        A source can be superseded a moment after any check, so this is a question to ask, not a guarantee.
        Bond never moves a citation to a newer version: a span's fate is a suggestion of where to look.

        Times are approximate. An entry's time is taken just before it is created, and writers' clocks differ, so
        within one document the order of entries is exact (it is their seq), but across documents, and against
        as_of, times can be off by as long as a write takes, or by clock skew: as_of near another write's time
        can't say which came first.
        """
        self._check_ref(ref)
        v = ref.version
        holding = [d for d in self.index.holding(v) if document is None or d.name == document]
        found: list[DocumentFreshness] = []
        imported = self.records.imported(v)
        for d in holding:
            # The head, when v was last held and what followed, all from one read of the history, so a write
            # meanwhile (a revert to v, say) can't make them disagree.
            history = self.filing.history(d.id)
            held = [i for i, e in enumerate(history) if e.version == v]
            if not held:
                continue
            imported |= any(history[i].imported is not None for i in held)
            head = history[-1].version
            current = head == v
            superseded_at = None if current else history[held[-1] + 1].at
            current_as_of = None
            if as_of is not None:
                then = [e for e in history if e.at <= as_of]
                current_as_of = bool(then) and then[-1].version == v
            span = None if current or isinstance(ref.locator, VersionLocator) else self._span_fate(ref, head)
            found.append(DocumentFreshness(d.name, current, head, superseded_at, current_as_of, span))
        return Freshness(ref, tuple(found), imported)

    def _span_fate(self, ref: Ref, head: VersionId) -> SpanFate:
        """What became of a span's text in head: there at the same place, elsewhere, or not at all.

        The same place is the same locator, except in JSON, where a position names a member only within its
        version: there it is the value at the same JSON Pointer. Elsewhere is the first place the text is found,
        a candidate to look at, not the same field."""
        old_version = self._version(ref.version)
        old = self._doc(ref.version).text(ref.locator) if spec(old_version.format).has_text else None
        head_version = self._version(head)
        if not old or not old.strip() or not spec(head_version.format).has_text:
            return SpanFate(SpanStatus.UNKNOWN, None)
        doc = self._doc(head)
        same: Locator | None = ref.locator
        if isinstance(ref.locator, JsonLocator):
            old_doc, new_doc = self._doc(ref.version), doc
            same = None
            if isinstance(old_doc, JsonDoc) and isinstance(new_doc, JsonDoc):
                same = new_doc.locate(old_doc.pointer(ref.locator), ref.locator.span)
        if same is not None and doc.check(same) is None and doc.text(same) == old:
            return SpanFate(SpanStatus.UNCHANGED, Ref(head, same))
        if matches := doc.search(old):
            return SpanFate(SpanStatus.MOVED, Ref(head, matches[0].locator))
        return SpanFate(SpanStatus.GONE, None)

    @_one_view
    def _superseded(self, citations: Sequence[Citation]) -> list[Issue]:
        """A CITES_SUPERSEDED issue for each support, in a version stored here and valid in it, that no document
        holding it has as its head any more."""
        found: list[Issue] = []
        for i, c in enumerate(citations):
            for j, s in enumerate(c.supports):
                if not isinstance(s, Ref) or self.records.version(s.version) is None or self._locator_problem(s):
                    continue
                fresh = self.freshness(s)
                if fresh.superseded:
                    found.append(Issue(IssueCode.CITES_SUPERSEDED, superseded_message(fresh), citation_index=i,
                                       support_index=j, locator=c.claim,
                                       hint="search the newer version and cite it, if what you cite still holds"))
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

        A version outside this workspace, which an import left behind, can't be
        checked here: it is reported once, as OUTSIDE_WORKSPACE, and the
        citations resting on it are checked as far as they can be without it,
        as are those resting on a version whose own bytes are missing or damaged,
        which that version reports. So an empty list still means everything
        beneath version was checked. Raises UnknownVersion if version itself
        isn't in this workspace.
        """
        if self.records.as_stored(version) is None:
            raise UnknownVersion(version)
        issues: list[Issue] = []
        seen: set[VersionId] = set()
        outside: dict[VersionId, None] = {}
        stack = [version]
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            record = self.records.as_stored(v)  # checked against v itself, whatever version it names
            if record is None:
                outside[v] = None
                continue
            stored, cited = record.version, record.citations
            beneath = [s.version for c in cited for s in c.supports if s.version != v]
            unchecked = frozenset(s for s in set(beneath) if not self._intact(s))
            issues += [replace(i, version=v) for i in self._verify_one(stored, cited, policy, unchecked)]
            stack += beneath
        return issues + [
            Issue(IssueCode.OUTSIDE_WORKSPACE, "this version isn't in the workspace, so what rests on it can't be "
                  "checked here", hint="bring it in with import_version(source, version) from the workspace it "
                  "was imported from", version=v)
            for v in outside
        ]

    def _verify_one(
        self, version: Version, cited: list[StoredCitation], policy: Policy, unchecked: frozenset[VersionId],
    ) -> list[Issue]:
        """Issues with one version, not with the versions it cites."""
        v = version.id
        issues = []
        written = [c.as_written() for c in cited]
        for c, w in zip(cited, written, strict=True):
            if citation_id(w) != c.id:
                issues.append(Issue(IssueCode.ID_MISMATCH, f"citation {c.id[:12]}… does not hash to its id"))
        if version_id(version.content_hash, version.format, written) != v:
            issues.append(Issue(IssueCode.ID_MISMATCH, "its content hash, format and citations do not hash to its id"))
        content = self.blobs.get(version.content_hash)
        if content is None:
            return issues + [Issue(IssueCode.BLOB_MISSING, "its blob is missing")]
        if hash_bytes(content) != version.content_hash:
            issues.append(Issue(IssueCode.BLOB_MISMATCH, "its blob does not match its content hash"))
        return issues + self._check(content, version.format, written, policy, unchecked)

    def _intact(self, version: VersionId) -> bool:
        """Whether what rests on a version can be checked against it: it is here, and its bytes are there, hash to
        its content hash and are well-formed."""
        record = self.records.as_stored(version)
        if record is None or record.stored_id != version:
            return False
        stored = record.version
        data = self.blobs.get(stored.content_hash)
        return (data is not None and hash_bytes(data) == stored.content_hash
                and not isinstance(self._load(version), Malformed))

    # --- helpers -------------------------------------------------------------

    def _load_version(self, version: VersionId) -> ParsedDoc | Malformed:
        """A stored version, parsed, or what is wrong with its bytes. Read through the self._load cache, which can't
        go stale: versions are immutable."""
        stored = self._version(version)
        data = self.blobs.get(stored.content_hash)
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
        return None if self.bond.records.version(version) is None else self.bond._doc(version)

    def citations(self, version: VersionId) -> list[StoredCitation]:
        return self.bond.citations(version)


def _encoded(content: bytes | str, format: Format | None) -> tuple[bytes, Format]:
    """Content as stored, UTF-8 for text, and its format, sniffed if not given."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return data, format or sniff(data)


def superseded_message(fresh: Freshness) -> str:
    """What a CITES_SUPERSEDED issue says of a superseded version, by version id."""
    parts = []
    for d in fresh.documents:
        fate = {None: "", SpanStatus.UNCHANGED: "; the cited text is unchanged there",
                SpanStatus.MOVED: "; the cited text is there, elsewhere", SpanStatus.GONE: "; the cited text is gone there",
                SpanStatus.UNKNOWN: ""}[None if d.span is None else d.span.status]
        parts.append(f"{d.document!r} has moved past it to {d.head[:12]}…{fate}")
    return f"cites version {fresh.ref.version[:12]}…, which no document holds as its head now: " + "; ".join(parts)


def _check_other(here: Bond, source: Bond) -> None:
    if source.workspace.id == here.workspace.id:
        raise ValueError("an import brings material in from another workspace; this is the same one")


def _checked_import(source: Bond, v: VersionId) -> NewVersion:
    """Validate the pinned artifact's identity without relying on a parsed-content cache."""
    stored = source._version(v)
    cited = source.records.citations(v)
    data = source.blob(v)
    written = [c.as_written() for c in cited]
    if (hash_bytes(data) != stored.content_hash
            or any(citation_id(w) != c.id for c, w in zip(cited, written, strict=True))
            or version_id(stored.content_hash, stored.format, written) != v):
        raise BondError(f"version {v[:12]}… in workspace {source.workspace.name!r} doesn't hash to its id; "
                        "this version was not imported (bond.verify there says what changed)")
    return NewVersion(v, stored.content_hash, stored.format, data, written)


def _walk(
    root: Ref,
    step: Callable[[Ref], list[tuple[StoredCitation, list[Ref]]]],
    max_depth: int | None,
    limit: int | None,
) -> tuple[list[Ref], list[TraceEdge], bool]:
    """Nodes, edges and whether nothing was left out, walking breadth first from root, where step gives a node's
    citations and the next nodes each leads to; so each node is reached at its shortest distance."""
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    complete = True
    seen: set[Ref] = set()
    nodes: list[Ref] = []
    edges: list[TraceEdge] = []
    level, depth = [root], 0
    while level:
        next_level: list[Ref] = []
        for node in level:
            if node in seen:
                continue
            if limit is not None and len(nodes) >= limit:
                return nodes, edges, False
            seen.add(node)
            nodes.append(node)
            if max_depth is not None and depth >= max_depth:
                if step(node):
                    complete = False
                continue
            for c, following in step(node):
                edges.append(TraceEdge(node, c))
                next_level += following
        level = [r for r in next_level if r not in seen]
        depth += 1
    return nodes, edges, complete


def _edges_at(citations: list[StoredCitation], locator: Locator) -> list[StoredCitation]:
    """The citations, all owned by one version, that a trace node at locator has edges for."""
    return [c for c in citations if overlaps(c.claim, locator)]


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
