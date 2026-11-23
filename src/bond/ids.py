"""Identity: the hashes that name blobs, citations and versions.

A version's id is a hash over its content hash, format and citations, and a
citation's over everything it rests on, so an id commits to the version's
provenance as well as its bytes. These are provenance rules, so they live in the
core, never in a storage backend: a backend stores what it is given, and verify
recomputes every id from what it returns.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .jsonvalue import JsonObject, JsonValue
from .models import SELF, Citation, Format, Ref, Support, VersionId

# Prefixed to the hashed forms of a version and of a citation, so neither hash can be mistaken for another.
_VERSION_TAG = "bond.version.v2\n"
_CITATION_TAG = "bond.citation.v1\n"


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def citation_id(c: Citation) -> str:
    """A citation's id: sha256 over its kind, claim, attrs and supports, as written.

    Its supports are a set: their order doesn't change the id. A support inside the version being written
    is hashed with version "self"; one in another version by that version's id, so a citation's id commits
    to everything it rests on.
    """
    hashed: JsonObject = {"kind": c.kind.value, "claim": c.claim.to_dict(), "attrs": c.attrs,
                          "supports": [[version, locator] for version, locator in supports(c)]}
    return hashlib.sha256((_CITATION_TAG + dumps(hashed)).encode()).hexdigest()


def version_id(content_hash: str, fmt: Format, citations: Sequence[Citation]) -> VersionId:
    """A version's id: sha256 over its content hash, its format and the set of its citations' ids.

    Order means nothing: the same citations in any order, or one listed twice, are the same version.
    Supports in other versions are hashed by their ids, so an id commits to all the provenance beneath
    it. Names and timestamps are labels, not provenance, and are left out.
    """
    hashed: JsonObject = {"content": content_hash, "format": fmt.value,
                          "citations": sorted({citation_id(c) for c in citations})}
    return VersionId(hashlib.sha256((_VERSION_TAG + dumps(hashed)).encode()).hexdigest())


def canonical(citations: Sequence[Citation]) -> list[tuple[str, Citation]]:
    """A version's citations as stored: each once, with its id, in id order."""
    return sorted({citation_id(c): c for c in citations}.items())


def supports(c: Citation) -> list[tuple[str, JsonObject]]:
    """A citation's supports in their canonical order, as hashed and stored: (version or "self", locator dict)."""
    return sorted(((_support_version(s), s.locator.to_dict()) for s in c.supports), key=lambda s: dumps(list(s)))


def _support_version(s: Support) -> str:
    """A support's version as hashed: inside the version being written, "self"."""
    return s.version if isinstance(s, Ref) else SELF


def dumps(v: JsonValue) -> str:
    """The canonical JSON form hashed and stored: sorted keys, no spaces."""
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
