"""Bond: immutable documents with explicit, inspectable provenance.

The names here are what a caller constructs or catches. Result types are in bond.models, lower-level reads in
bond.details, and the agent-facing tools in bond.authoring.
"""

from .api import Bond
from .locators import CsvLocator, JsonLocator, Locator, PdfLocator, TextLocator, VersionLocator
from .models import (
    BondError,
    Citation,
    CitationKind,
    CorruptVersion,
    Format,
    IssueCode,
    Ref,
    SelfRef,
    SpanStatus,
    SupportStatus,
    UnknownDocument,
    UnknownVersion,
    VersionId,
    WriteError,
)
from .policies import ALLOW_GAPS, STRICT, Policy

__all__ = [
    "ALLOW_GAPS",
    "STRICT",
    "Bond",
    "BondError",
    "Citation",
    "CitationKind",
    "CorruptVersion",
    "CsvLocator",
    "Format",
    "IssueCode",
    "JsonLocator",
    "Locator",
    "PdfLocator",
    "Policy",
    "Ref",
    "SelfRef",
    "SpanStatus",
    "SupportStatus",
    "TextLocator",
    "UnknownDocument",
    "UnknownVersion",
    "VersionId",
    "VersionLocator",
    "WriteError",
]
