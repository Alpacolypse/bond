"""Lower-level reads over one workspace, for tools built on the core: version records, the documents a version is
filed in, where a span is and the text around it, a version's claim units, and JSON Pointers.

The viewer and the authoring layer use these to show evidence to people and agents; most callers of Bond never
need them.
"""

from __future__ import annotations

from .api import Bond
from .locators import JsonLocator, Locator, Span, overlaps
from .models import CitationKind, DocumentRef, Format, Ref, StoredCitation, Version, VersionId
from .parsers import JsonDoc


def version(bond: Bond, version: VersionId) -> Version:
    """A version's record (its format, content hash and creation time), here or in a workspace this one links to;
    raises UnknownVersion if none."""
    return bond._version(version)


def versions(bond: Bond) -> list[Version]:
    """Every version stored in the workspace itself, oldest first."""
    return bond.index.versions()


def owner(bond: Bond, version: VersionId) -> Bond | None:
    """The workspace a version is read from: bond itself, or the nearest it links to (directly or not) that holds
    it; None if none does."""
    return bond._owner(version)


def held_by(bond: Bond, version: VersionId) -> Bond:
    """The workspace a version is read from (see owner); raises UnknownVersion if none reachable holds it."""
    return bond._held(version)


def filed_in(bond: Bond, version: VersionId) -> list[DocumentRef]:
    """The documents whose history holds a version, oldest first, in the workspace it is read from (see owner).
    Compare each one's head to see if it's current."""
    return bond._held(version).index.holding(version)


def locator_problem(bond: Bond, ref: Ref) -> str | None:
    """Why a reference names nothing here: its version is in neither the workspace nor any it links to, or its
    locator isn't valid in it. None if it names something."""
    if bond._owner(ref.version) is None:
        return f"version {ref.version[:12]}… isn't in this workspace or any it links to"
    return bond._locator_problem(ref)


def where(bond: Bond, ref: Ref) -> str:
    """Where a reference is, as a reader would find it: a heading path and line, a JSON path, a page.

    Computed by the format's parser; raises ValueError if the locator isn't valid.
    """
    bond._check_ref(ref)
    return bond._doc(ref.version).where(ref.locator)


def excerpt(bond: Bond, ref: Ref, pad: int = 40) -> tuple[str, str, str]:
    """(text before, text at, text after) a reference, up to pad characters each side; empty if it has no text."""
    bond._check_ref(ref)
    return bond._doc(ref.version).excerpt(ref.locator, pad)


def claim_units(bond: Bond, version: VersionId) -> list[Ref]:
    """A version's claim units in document order: the blocks whose words coverage requires citations for.

    Computed by the format's parser; empty for PDFs.
    """
    return [Ref(version, unit) for unit in bond._doc(version).claims()]


def json_doc(bond: Bond, version: VersionId) -> JsonDoc | None:
    """A stored JSON version, parsed, to translate between JSON Pointers and locators in it; None for another
    format. Raises UnknownVersion if there is no such version."""
    if bond._version(version).format is not Format.JSON:
        return None
    doc = bond._doc(version)
    return doc if isinstance(doc, JsonDoc) else None


def locate_pointer(bond: Bond, version: VersionId, pointer: str, span: Span | None = None) -> Ref:
    """The reference to the value a JSON Pointer names in a JSON version, optionally a range in it.

    Bond stores JSON locators as positional paths, which hold no keys; this is how a caller who names a value
    by pointer gets one. Raises ValueError if the version isn't JSON or the pointer names nothing in it.
    """
    doc = json_doc(bond, version)
    if doc is None:
        raise ValueError(f"version {version[:12]}… is {bond._version(version).format.value}, not json")
    located = doc.locate(pointer, span)
    if located is None:
        raise ValueError(f"json pointer {pointer!r} does not exist in version {version[:12]}…")
    return Ref(version, located)


def pointer(bond: Bond, ref: Ref) -> str:
    """The JSON Pointer of the value a JSON reference names; the inverse of locate_pointer.

    Raises ValueError if the reference isn't a JSON locator naming a value in its version.
    """
    doc = json_doc(bond, ref.version)
    if doc is None or not isinstance(ref.locator, JsonLocator):
        raise ValueError(f"a {ref.locator.type.value} locator has no JSON Pointer")
    return doc.pointer(ref.locator)


def covering_kinds(citations: list[StoredCitation], locator: Locator) -> frozenset[CitationKind]:
    """Kinds of the citations, all owned by one version, whose claims overlap a locator in it."""
    return frozenset(c.kind for c in citations if overlaps(c.claim, locator))
