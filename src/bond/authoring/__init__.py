"""Authoring: read and write documents with their citations written in, on top of the core.

A document is read as a draft with its citations written in (<bond:cite> tags in text; $bond wrappers and
tags in JSON; tags in CSV and TSV cells), and written back the same way; evidence is cited by handles that
name exact spans. Tools binds a workspace, a policy and the workspaces it may import from, for a host to hand
to an agent; every write goes through the core under that policy, so the core still enforces everything.
See docs/agents.md.
"""

from .tools import (
    AGENT_KINDS,
    AGENT_POLICY,
    Chain,
    Checked,
    Draft,
    Edit,
    Evidence,
    Grounds,
    ImportOutcome,
    Link,
    Listing,
    Outcome,
    Reading,
    Refused,
    SearchHit,
    Shortfall,
    ToolError,
    Tools,
    Written,
)

__all__ = [
    "AGENT_KINDS",
    "AGENT_POLICY",
    "Chain",
    "Checked",
    "Draft",
    "Edit",
    "Evidence",
    "Grounds",
    "ImportOutcome",
    "Link",
    "Listing",
    "Outcome",
    "Reading",
    "Refused",
    "SearchHit",
    "Shortfall",
    "ToolError",
    "Tools",
    "Written",
]
