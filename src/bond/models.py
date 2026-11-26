"""Plain data types passed in and out of the core API."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from enum import Enum, StrEnum
from typing import NewType

from .jsonvalue import JsonObject
from .locators import Locator
from .parsers import Context


class Format(Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"  # comma-separated
    TSV = "tsv"  # tab-separated
    PDF = "pdf"


class CitationKind(Enum):
    TRANSCRIPTION = "transcription"
    QUOTE = "quote"
    PARAPHRASE = "paraphrase"
    UNSOURCED = "unsourced"
    SOURCE = "source"
    CLASSIFICATION = "classification"
    ASSESSMENT = "assessment"


# A version's id: a hash of its bytes, format and citations (see store.version_id).
# Not the hash of its bytes alone, which names its blob.
VersionId = NewType("VersionId", str)

# How a support inside the version being written is hashed and written: its id isn't known yet.
SELF = "self"


@dataclass(frozen=True)
class Ref:
    """A locator in one stored version: what Bond stores, returns and reads by."""

    version: VersionId
    locator: Locator

    def to_dict(self) -> JsonObject:
        return {"version": self.version, "locator": self.locator.to_dict()}


@dataclass(frozen=True)
class SelfRef:
    """A locator in the version being written, in one of its own citations' supports.

    That version's id hashes its citations, so no citation can name it by id; once stored, SelfRef is stored
    as a Ref to it.
    """

    locator: Locator


Support = Ref | SelfRef  # what a citation being written may rest on


@dataclass(frozen=True)
class Citation:
    kind: CitationKind
    claim: Locator  # part of the document being written
    supports: list[Support]  # what the claim rests on, in order
    attrs: dict[str, str] = field(default_factory=dict)  # kind-specific producer ids, validated by the kind


@dataclass(frozen=True)
class StoredCitation:
    """A citation as stored: what it was written as, plus its identity in the version that owns it."""

    id: str  # the hash of its kind, claim, supports and attrs, as written
    owner_version: VersionId
    kind: CitationKind
    claim: Locator
    supports: list[Ref]  # supports inside the owner point at owner_version
    attrs: dict[str, str]

    def as_written(self) -> Citation:
        """The citation as it was written and hashed: supports inside its owner are SelfRefs again."""
        return Citation(
            self.kind, self.claim,
            [SelfRef(s.locator) if s.version == self.owner_version else s for s in self.supports],
            self.attrs,
        )

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id, "owner_version": self.owner_version, "kind": self.kind.value,
            "claim": self.claim.to_dict(), "supports": [s.to_dict() for s in self.supports], "attrs": self.attrs,
        }


@dataclass(frozen=True)
class Version:
    """A stored version: evidence, immutable and nameless. Documents file it under names (see DocumentRef).

    Its bytes are read with Bond.text or Bond.blob, and its citations with Bond.citations.
    """

    id: VersionId
    content_hash: str  # sha256 of its bytes; versions with the same bytes share one blob
    format: Format
    created_at: str

    def to_dict(self) -> JsonObject:
        # The viewer's JSON names a version's id "version", as everywhere else in ui/API.md.
        return {"version": self.id, "content_hash": self.content_hash, "format": self.format.value,
                "created_at": self.created_at}


# A document's id: stable across renames, unlike its name.
DocumentId = NewType("DocumentId", str)


@dataclass(frozen=True)
class DocumentRef:
    """A document: a name with a history of versions, the last of which is its head.

    Documents are filing, not provenance: citations point at versions, never at
    documents, so renaming, retitling or advancing a document changes no provenance.
    The name is how agents and code address a document; the title, if set, is how
    people read it ("Fax OCR, case 001" for "fax_case_001.reducto").
    """

    id: DocumentId
    name: str
    head: VersionId
    created_at: str
    title: str | None = None

    def to_dict(self) -> JsonObject:
        return {"id": self.id, "name": self.name, "title": self.title, "head": self.head, "created_at": self.created_at}


@dataclass(frozen=True)
class Workspace:
    """A self-contained set of documents and their provenance: what an agent is opened on.

    Citations only point at versions in the same workspace; material from another one comes in by import.
    The id is Bond's; the name is a label, unique among the workspaces of one store.
    """

    id: str
    name: str
    created_at: str

    def to_dict(self) -> JsonObject:
        return {"id": self.id, "name": self.name, "created_at": self.created_at}


@dataclass(frozen=True)
class Imported:
    """Where a history entry's version came from, when it was imported from another workspace."""

    workspace: str  # the other workspace's id
    workspace_name: str  # its name when the import happened
    version: VersionId  # the version there, which keeps its id here

    def to_dict(self) -> JsonObject:
        return {"workspace": self.workspace, "workspace_name": self.workspace_name, "version": self.version}


@dataclass(frozen=True)
class HistoryEntry:
    """One step of a document's history: from this entry on, the document pointed at version."""

    seq: int  # from 1
    version: VersionId
    base: VersionId | None  # the head the writer started from; None for a document's first entry
    at: str
    imported: Imported | None = None  # set when the entry came in by import from another workspace
    origin: str | None = None  # where an ingested file came from (a path or URL), as its ingester said; filing only

    def to_dict(self) -> JsonObject:
        return {"seq": self.seq, "version": self.version, "base": self.base, "at": self.at,
                "imported": self.imported.to_dict() if self.imported else None, "origin": self.origin}


class SupportStatus(StrEnum):
    """Reserved outcomes of Bond's built-in structural support check, computed rather than stored.

    Application-specific assessments should use their own enums, without redefining these meanings.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class SupportReason(StrEnum):
    UNDECLARED = "undeclared"
    UNSOURCED = "unsourced"
    MISSING_VERSION = "missing_version"
    LIMIT = "limit"
    DEPTH_LIMIT = "depth_limit"
    CYCLE = "cycle"
    INVALID_REFERENCE = "invalid_reference"
    NO_REQUIRED_CONTENT = "no_required_content"


@dataclass(frozen=True)
class Declarations:
    """Declarations overlapping a selection, and exact gaps under format coverage rules.

    Citations retain their full subject spans. This report makes no recursive support judgment.
    PDF gaps describe uncovered geometry, not extracted text or detected factual assertions.
    """

    ref: Ref
    citations: tuple[StoredCitation, ...]
    undeclared: tuple[Ref, ...]

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "citations": [c.to_dict() for c in self.citations],
                "undeclared": [r.to_dict() for r in self.undeclared]}


@dataclass(frozen=True)
class SupportRegion:
    ref: Ref
    status: SupportStatus

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "status": self.status.value}


@dataclass(frozen=True)
class SupportIssue:
    ref: Ref
    reason: SupportReason
    path: tuple[Ref, ...]  # one ordered explanatory route, not necessarily the shortest

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "reason": self.reason.value,
                "path": [r.to_dict() for r in self.path]}


@dataclass(frozen=True)
class SupportReport:
    """Structural support to declared sources, not entailment, authority, or an integrity recheck.

    Regions give disjoint coverage outcomes for the queried content; the exact subdivision may change.
    Unknown regions take precedence in the overall status. Results are recomputed for each query against
    the workspace's currently available evidence; no assessment is stored on a version.
    """

    ref: Ref
    status: SupportStatus
    regions: tuple[SupportRegion, ...]
    issues: tuple[SupportIssue, ...]

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "status": self.status.value,
                "regions": [r.to_dict() for r in self.regions], "issues": [i.to_dict() for i in self.issues]}


@dataclass(frozen=True)
class Hit:
    ref: Ref  # the matched text itself
    documents: list[str]  # names of the documents whose history holds the version
    format: Format
    snippet: str
    context: Context  # display aids: line and headings, keys, or row and column
    declarations: Declarations
    # Documents that hold the version but have moved past it, each with its head now. Empty when the version is
    # every holding document's head: what search finds in heads, unless searched within an older version.
    newer: dict[str, VersionId] = field(default_factory=dict)

    @property
    def kinds(self) -> frozenset[CitationKind]:
        """All kinds overlapping the match; use declarations for coverage."""
        return frozenset(c.kind for c in self.declarations.citations)

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "documents": self.documents, "format": self.format.value,
                "snippet": self.snippet, "context": self.context.to_dict(),
                "kinds": sorted(k.value for k in self.kinds), "declarations": self.declarations.to_dict(),
                "newer": dict(self.newer)}


@dataclass(frozen=True)
class WriteResult:
    version: VersionId  # the version written
    advanced: bool  # whether anything changed: False if the document already pointed at version
    warnings: tuple[Issue, ...] = ()  # what the write didn't refuse but a writer should know (cites_superseded)


class SpanStatus(StrEnum):
    """What became of a span of a version in a document's head now."""

    UNCHANGED = "unchanged"  # the head has the same text at the same place (or is the version itself)
    MOVED = "moved"  # the head has the same text elsewhere
    GONE = "gone"  # the head doesn't have the text
    UNKNOWN = "unknown"  # can't be told: the span has no text (a region of a page), or the head can't be read


@dataclass(frozen=True)
class SpanFate:
    """A span's text in a newer version: whether it is there, and where. A suggestion: Bond never moves a
    citation."""

    status: SpanStatus
    at: Ref | None  # where it is in the head, when unchanged or moved

    def to_dict(self) -> JsonObject:
        return {"status": self.status.value, "at": None if self.at is None else self.at.to_dict()}


@dataclass(frozen=True)
class DocumentFreshness:
    """A version as one document that held it sees it now."""

    document: str
    current: bool  # the version is the document's head
    head: VersionId  # the document's head now
    superseded_at: str | None  # when the document last moved off the version; None while it is the head
    current_as_of: bool | None  # whether it was the head at the time asked about; None if none was asked
    span: SpanFate | None  # what became of the span in the head; None when current, or for a whole version

    def to_dict(self) -> JsonObject:
        return {"document": self.document, "current": self.current, "head": self.head,
                "superseded_at": self.superseded_at, "current_as_of": self.current_as_of,
                "span": None if self.span is None else self.span.to_dict()}


@dataclass(frozen=True)
class Freshness:
    """Whether a span's version is still current where it is filed: computed when asked, never stored.

    Freshness is relative to a document: one version can be one document's head and superseded in another.
    A version filed nowhere here (one an import brought without filing) has no documents to be current in.
    """

    ref: Ref
    documents: tuple[DocumentFreshness, ...]
    imported: bool  # brought from another workspace: whether it is current there isn't known here

    @property
    def superseded(self) -> bool:
        """Filed somewhere, and no longer the head of any document that held it."""
        return bool(self.documents) and not any(d.current for d in self.documents)

    def to_dict(self) -> JsonObject:
        return {"ref": self.ref.to_dict(), "documents": [d.to_dict() for d in self.documents],
                "imported": self.imported, "superseded": self.superseded}


@dataclass(frozen=True)
class TranscriptionOf:
    """A version that transcribes another: its producer, and the documents it is filed in."""

    version: VersionId
    producer: str | None  # the transcriber its citations name
    documents: list[str]

    def to_dict(self) -> JsonObject:
        return {"version": self.version, "producer": self.producer, "documents": self.documents}


@dataclass(frozen=True)
class TraceEdge:
    """A citation linking a node of a trace to the next ones."""

    node: Ref  # the ref the citation's claim overlaps
    citation: StoredCitation  # its supports are the next nodes


@dataclass(frozen=True)
class Trace:
    """Provenance from root down to its sources, as a DAG: each distinct ref is one node."""

    root: Ref
    nodes: list[Ref]  # nearest first
    edges: list[TraceEdge]
    complete: bool  # False if node/depth limits omitted traversal; the frontier is reported separately
    frontier: frozenset[VersionId] = frozenset()  # versions of nodes that aren't in this workspace
    # Versions of nodes that documents here have moved past: version -> {document: its head now}.
    newer: dict[VersionId, dict[str, VersionId]] = field(default_factory=dict)

    def edges_at(self, node: Ref) -> list[TraceEdge]:
        """The edges out of one node: none for an uncited node, one past max_depth, or one outside the workspace."""
        return [e for e in self.edges if e.node == node]


# --- errors ------------------------------------------------------------------


class BondError(Exception):
    pass


class UnknownVersion(BondError):
    def __str__(self) -> str:
        return f"unknown version {self.args[0]!r}"


class UnknownDocument(BondError):
    def __str__(self) -> str:
        return f"unknown document {self.args[0]!r}"


class CorruptVersion(BondError):
    """A stored version's bytes can't be read as it: they are missing, or aren't well-formed in its format.

    verify() says what is wrong with every version, without raising.
    """

    def __init__(self, version: VersionId, reason: str):
        super().__init__(f"version {version[:12]}… can't be read: {reason}")
        self.version = version


class IssueCode(Enum):
    # Integrity: always checked, at write and by verify.
    INVALID_CONTENT = "invalid_content"  # content is not well-formed in its format
    BAD_LOCATOR = "bad_locator"  # malformed, or outside its document
    UNKNOWN_VERSION = "unknown_version"  # support points at a version that doesn't exist
    # Only from verify: a version rests on one that isn't in this workspace, so that part can't be checked here.
    # Not a failure: the id still commits to it, and importing it (import_version) lets verify check it.
    OUTSIDE_WORKSPACE = "outside_workspace"
    INVALID_CITATION = "invalid_citation"  # kind validator rejected it; message says why
    QUOTE_MISMATCH = "quote_mismatch"  # QUOTE claim text differs from its support
    CIRCULAR_SUPPORT = "circular_support"  # citations in the document support each other in a loop
    # Policy: what the policy a version is checked against requires.
    UNCOVERED_CLAIM = "uncovered_claim"  # words no citation covers
    KIND_NOT_ALLOWED = "kind_not_allowed"  # a citation of a kind the policy doesn't allow
    UNKNOWN_PRODUCER = "unknown_producer"  # a citation names a producer the policy doesn't know
    # Filing: only at write.
    INVALID_NAME = "invalid_name"  # a document's name or title is blank
    DOCUMENT_EXISTS = "document_exists"  # no base given, but the document already exists
    UNKNOWN_DOCUMENT = "unknown_document"  # a base given, but no document has that name
    STALE_BASE = "stale_base"  # the document has moved past the base the writer started from
    # Freshness: a warning at write, or a refusal under a policy that refuses it.
    CITES_SUPERSEDED = "cites_superseded"  # a support's version is no longer the head of any document that held it
    # Stored data: only by verify.
    BLOB_MISSING = "blob_missing"  # a version's bytes are gone from the blob directory
    BLOB_MISMATCH = "blob_mismatch"  # a version's bytes don't hash to its content hash
    ID_MISMATCH = "id_mismatch"  # a version's content hash, format and citations don't hash to its id


@dataclass(frozen=True)
class Issue:
    """A problem found by a write, a check or verify: what is wrong, and where."""

    code: IssueCode
    message: str
    _: KW_ONLY
    citation_index: int | None = None  # the citation it is about, by its place in the citations written
    support_index: int | None = None  # for a problem with one of that citation's supports, which one
    cycle: tuple[int, ...] = ()  # for CIRCULAR_SUPPORT, the citations in the loop, in the order each rests on the next
    locator: Locator | None = None
    hint: str | None = None
    version: VersionId | None = None  # set by verify, which checks many versions; None for the one being written

    def __str__(self) -> str:
        version = f"{self.version[:12]}…: " if self.version else ""
        support = f", support {self.support_index}" if self.support_index is not None else ""
        where = f" (citation {self.citation_index}{support})" if self.citation_index is not None else ""
        hint = f" Hint: {self.hint}" if self.hint else ""
        return f"{version}[{self.code.value}]{where} {self.message}{hint}"


class WriteError(BondError):
    def __init__(self, errors: list[Issue]):
        self.errors = errors
        super().__init__(f"{len(errors)} error(s):\n" + "\n".join(f"  {e}" for e in errors))

    @property
    def codes(self) -> list[IssueCode]:
        return [e.code for e in self.errors]
