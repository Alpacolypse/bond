"""Adding files to a workspace as sources: what `bond add`, the app view and `bond view` share.

Adding is a person's act, never an agent's: no authoring tool calls this. Each file is copied in, so the
original can change or go without changing Bond, and its history records where it came from. Every file is
checked before any is added (Bond reads its format, names don't collide, a taken name is only reused when
asked), so a refusal adds none of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

from .api import Bond
from .formats import FORMATS, Format, from_suffix
from .models import BondError


def no_text_note(formats: Sequence[Format]) -> str | None:
    """What to know about adding sources in these formats: one with no text of its own (a PDF) can't be searched or
    cited until a document transcribes it."""
    unread = sorted({f.value for f in formats if not FORMATS[f].has_text})
    if not unread:
        return None
    return f"{' and '.join(unread).upper()} has no text of its own: it can't be searched or cited until a document " \
           "transcribes it."


@dataclass(frozen=True)
class NewSource:
    """A file to add: the document name it goes under, its bytes, and where it came from."""

    name: str
    data: bytes
    origin: str | None  # a path or URL, or None if it isn't known


@dataclass(frozen=True)
class Added:
    """A file added, or not, because the document already held exactly those bytes."""

    document: str
    added: bool


@dataclass(frozen=True)
class AddResult:
    added: list[Added]
    note: str | None  # something the person should know about what they added (a PDF has no text yet)


def readable(name: str) -> Format:
    """The format a file name's suffix says Bond reads it as; raises BondError naming the suffixes Bond reads."""
    fmt = from_suffix(PurePath(name).suffix)
    if fmt is None:
        suffixes = ", ".join(sorted(x for spec in FORMATS.values() for x in spec.suffixes))
        raise BondError(f"Bond can't read {PurePath(name).name} (it reads {suffixes} files)")
    return fmt


def add_sources(bond: Bond, files: Sequence[NewSource], update: bool = False) -> AddResult:
    """Add files to bond as sources, each under its name; all or none pass the checks first.

    A name already taken is refused unless update, which adds the file as that document's next version. A file
    the same as the document's current version adds nothing (Added.added is False).
    """
    if not files:
        raise BondError("no files to add")
    names = [f.name for f in files]
    formats = [readable(n) for n in names]
    if len(set(names)) < len(names):
        raise BondError("two of these files would have the same name; add one under another name")
    if not update and (taken := [n for n in names if bond.filing.document(n)]):
        raise BondError(f"{bond.workspace.name!r} already has a document named {', '.join(map(repr, taken))}; add "
                        "it under another name, or as that document's next version")
    added = []
    for f, fmt in zip(files, formats, strict=True):
        existing = bond.filing.document(f.name)
        result = bond.ingest(f.name, f.data, format=fmt, base=existing.head if existing else None, origin=f.origin)
        added.append(Added(f.name, result.advanced))
    return AddResult(added, no_text_note(formats))
