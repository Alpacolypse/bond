"""Bond for agents: the authoring tools over MCP, over whatever workspaces a deployment offers.

Run with: bond mcp (see bond.cli). Needs the `mcp` extra. It speaks MCP over stdio, so a client (Claude Code,
Claude Desktop, an agent harness) starts it.

The server only carries calls. It serves the workspaces it is given (bond.workspaces.Workspaces), looked up on
every call, with what it binds them to: a policy (AGENT_POLICY: every word covered, no `source`) and import
budgets. The agent names the workspace on every call and the server remembers none: it may restart, or serve
several chats at once, so a call means the same whenever it is made. The agent can't change what a workspace is
bound to, nor reach a workspace it wasn't offered except to import from one linked to it. Issues from a write come
back as the tool's result, for the agent to fix and retry; a call that can't be carried out at all (a workspace or
document that doesn't exist, a handle that names nothing) is a tool error with the reason. Anything else that goes
wrong is a crash, logged here and not explained to the agent.

Given the view's HTML (bond.mcp.view), the server is also an MCP App: write and edit render the document in the
chat, and `show(workspace, document)` lets the agent present one. A deployment adds tools, instructions and
app-only tools of its own (bond.local.grants adds grant mode and its picker).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from types import FunctionType
from typing import TYPE_CHECKING, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .. import authoring
from ..api import Bond
from ..authoring import AGENT_POLICY, Chain, Draft, Edit, ImportOutcome, Listing, Outcome, Reading, SearchHit, Tools
from ..authoring.tools import SUPPORT_LIMIT
from ..locators import VersionLocator
from ..models import Format, Ref, SupportStatus
from ..policies import Policy
from ..workspaces import Workspaces

if TYPE_CHECKING:
    from .view import ViewTools

INSTRUCTIONS = """\
You are working in Bond workspaces: each a set of documents (one case, say) where every word says where it \
came from. In a workspace you can read its documents and write new ones, but a write is refused unless every \
word in it is accounted for: quoted or paraphrased from material you found, or explicitly unsourced. Refused \
writes come back with every issue at once, in your terms; fix them and write again (dry_run=true checks \
without writing).

Bond checks references, citation structure, quote text and coverage. It does not judge whether a source is \
authoritative or whether a paraphrase follows from what it cites, so a successful write is not a claim that \
the content is true.

Every tool takes the workspace to work in as its first argument: pass it on every call. You can only cite \
within one workspace: a handle found in one names nothing in another.

How to work:
1. documents(workspace) lists what is in a workspace, with each document's format. read(workspace, document) \
shows one exactly as it is: read it in full before citing it. A source with no text of its own (a PDF) can't be \
searched or cited until a transcription of it exists; reading one names its transcriptions, and you cite their text.
2. To cite, search(workspace, phrases, document=...) for exactly the text you need. Each hit has a handle \
naming that span, the handle of the passage it sits in, and support: "supported" when everything in it rests, \
hop by hop, on declared sources; otherwise partially_supported, unsupported, unknown (missing dependencies or \
budget) or not_applicable (nothing that needs support). Prefer supported evidence. To cite part of a passage, \
search(workspace, phrase, within=<passage handle>). Copy handles from search results; never make one up.
   trace(workspace, handle) gives the same support status with the reasons it falls short, and what the span \
rests on, hop by hop, down to its sources, each with its own handle. Neither evaluates whether a claim is true.
3. write(workspace, document, draft) creates a document. To change one, draft(workspace, document) gives its \
draft (its citations as tags) and its base, then edit(workspace, document, edits, base) or write(workspace, \
document, draft, base). Each successful write returns the next base, and the support status of what you \
wrote. If someone else changed the document since your base, call draft(workspace, document) again.

Markdown drafts cite with inline tags around the text they cover:
  <bond:cite kind="quote" from="HANDLE">text exactly as the handle's span has it</bond:cite>
  <bond:cite kind="paraphrase" from="HANDLE HANDLE">your words for one or more spans</bond:cite>
  <bond:cite kind="unsourced">Findings</bond:cite>
- quote: verbatim (whitespace, curly quotes and line-break hyphens are forgiven). paraphrase: the same \
facts in other words; from lists one or more handles, separated by spaces. unsourced: no supporting \
evidence is declared (e.g. a heading or label); keep it for text that needs none. Tags take no other attributes.
- <bond:anchor id="x">part of a sentence</bond:anchor> names text without citing it; from="#x" rests a \
claim on it, as from="#id" rests on another tag given id="id". Tags nest but never partly overlap.
- Every word needs a tag, headings and list items included; punctuation and Markdown syntax don't.

JSON drafts wrap a value cited whole: {"$bond": {"cite": {"kind": "quote", "from": "HANDLE"}}, "value": ...}; \
part of a string is tagged as above, with single-quoted attributes. CSV and TSV drafts are the table, with \
tags in its cells: a wrapping tag cites part of a cell, a self-closing tag cites its cell, or with on='row' \
or on='column' its row or column.
"""

IMPORTING = """
A workspace may have links: other workspaces a person linked to it so documents can be imported from them. \
documents(workspace, from_workspace=...) lists the documents of a workspace linked to workspace, and \
import_document(workspace, from_workspace, document) brings one into workspace. A linked workspace's documents \
can't be cited where they are: import one, then search it in workspace. Imports keep each document's identity \
and citations; the host bounds how much of what it rests on comes along, and the result's frontier lists what \
stayed behind. You cannot declare an imported document a source.
"""

Writable = Literal["markdown", "json", "csv", "tsv"]


@dataclass(frozen=True)
class ServedTool:
    """One authoring tool as a server offers it: the function the SDK calls, its name and its annotations."""

    fn: Callable[..., Awaitable[Any]]
    name: str
    annotations: ToolAnnotations


@dataclass(frozen=True)
class Served:
    """The workspaces a server offers the agent, and what it binds every one to: the policy its writes must meet,
    and how much of what an imported document rests on comes along (import_depth hops, None for all; import_limit
    versions at most). The deployment that made workspaces owns every Bond in it, and closes them."""

    workspaces: Workspaces
    policy: Policy = AGENT_POLICY
    import_depth: int | None = 0
    import_limit: int = 100

    def __post_init__(self) -> None:
        if self.import_depth is not None and self.import_depth < 0:
            raise ValueError("import_depth must be non-negative or None")
        if self.import_limit < 1:
            raise ValueError("import_limit must be at least 1")

    def open(self, workspace: str) -> Bond:
        """The workspace the agent named, or a ToolError saying which it can work in."""
        try:
            return self.workspaces.open(workspace)
        except KeyError:
            names = ", ".join(map(repr, self.workspaces.names())) or "none yet"
            raise authoring.ToolError(f"{workspace!r} isn't a workspace you can work in (you can in: {names})") from None

    def tools(self, workspace: str) -> Tools:
        """The authoring tools for the workspace the agent named, bound to its links, policy and budgets."""
        bond = self.open(workspace)
        return Tools(bond, self.policy, sources=self.workspaces.links(workspace), import_depth=self.import_depth,
                     import_limit=self.import_limit)


@dataclass(frozen=True)
class Shown:
    """A document shown to the person: its name and whether all of it rests on declared sources."""

    document: str
    support: SupportStatus
    status: Literal["shown"] = "shown"


VIEWED = frozenset({"write", "edit"})  # authoring tools whose result the view shows


def create_server(
    workspaces: Workspaces | Served, *, view: str | None = None, tools: Sequence[ServedTool] = (), more: str = "",
    extend: Callable[[ViewTools], None] | None = None,
) -> MCPServer:
    """The authoring tools over workspaces (bound to AGENT_POLICY and the default import budgets unless given as
    Served), with the app view if view, its HTML, is given (bond.mcp.view.packaged_view()).

    workspaces is looked up on every call, so a workspace offered after the server started is served from then on.
    Only the instructions are fixed at creation, listing what it offered then. A deployment adds model tools (which
    render the view too), more instructions, and with extend app-only tools of its own.
    """
    served = workspaces if isinstance(workspaces, Served) else Served(workspaces)
    text = instructions(served) + more
    authoring_ = authoring_tools(served)
    if view is None:
        server = MCPServer("bond", instructions=text)
        for tool in [*authoring_, *tools]:
            server.add_tool(tool.fn, name=tool.name, annotations=tool.annotations)
        return server

    from .view import READING, attach_view

    async def show(workspace: str, document: str) -> Shown:
        """Show a document to the person you're working with, in the chat, where they can follow any claim to
        what it rests on. Use it to present a document; read() is for reading one yourself."""
        bond = served.open(workspace)
        found = bond.catalog.get_document(document)
        if found is None:
            raise authoring.ToolError(f"there's no document named {document!r} in {workspace!r}")
        return Shown(document, bond.check_support(Ref(found.head, VersionLocator()), limit=SUPPORT_LIMIT).status)

    viewed = [t for t in authoring_ if t.name in VIEWED] + [ServedTool(reported(show), "show", READING), *tools]
    apps, guard = attach_view(view, viewed, served=served, extend=extend)
    server = MCPServer("bond", instructions=text, extensions=[apps])
    for tool in authoring_:
        if tool.name not in VIEWED:
            server.add_tool(tool.fn, name=tool.name, annotations=tool.annotations)
    server.middleware.append(guard)
    return server


def instructions(served: Served) -> str:
    """What the agent is told when it connects: how to cite, how to import, and the workspaces there now."""
    return INSTRUCTIONS + IMPORTING + _available(served)


def authoring_tools(served: Served) -> list[ServedTool]:
    """The authoring tools over workspaces, each taking the workspace first, with ToolErrors as MCP tool errors."""
    listed: list[ServedTool] = []

    def tool(annotations: ToolAnnotations) -> Callable[[FunctionType], FunctionType]:
        def register(fn: FunctionType) -> FunctionType:
            listed.append(ServedTool(reported(fn), fn.__name__, annotations))
            return fn
        return register

    reading = ToolAnnotations(read_only_hint=True)
    # A write appends a version; it never overwrites one, and the same write again changes nothing.
    writing = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)

    def tools(workspace: str) -> Tools:
        return served.tools(workspace)

    def linked(workspace: str, from_workspace: str) -> Tools:
        """The tools for workspace, once from_workspace is known to be one of its links."""
        bound = tools(workspace)
        if from_workspace not in bound.sources:
            names = ", ".join(map(repr, bound.sources)) or "none"
            raise authoring.ToolError(f"{workspace!r} doesn't link to {from_workspace!r}, so you can't import from it "
                                      f"into {workspace!r} (you can from: {names})")
        return bound

    # Tools are async, so calls run one at a time on the event loop, as an agent makes them. Bond is
    # safe to share across threads, so this keeps calls in order rather than keeping Bond safe.

    @tool(reading)
    async def documents(workspace: str, from_workspace: str | None = None) -> list[Listing]:
        """Every document in `workspace`, oldest first, with the format of its current version (a PDF has no text
        of its own: its text is cited through a transcription); or, with `from_workspace`, the documents of that workspace, one
        linked to `workspace`, which you can cite only after bringing them in with import_document."""
        if from_workspace is None:
            return tools(workspace).documents()
        return linked(workspace, from_workspace).documents(from_workspace)

    # Offered whatever the workspaces' links are now: a workspace with links may be granted while the server runs.
    @tool(writing)
    async def import_document(workspace: str, from_workspace: str, document: str) -> ImportOutcome:
        """Bring a document into `workspace` from `from_workspace`, one of its links, under the same name, so you
        can read, search and cite it in `workspace`. Identity and citations are preserved; frontier lists what it
        rests on that stayed behind. Importing again brings the newest version."""
        return linked(workspace, from_workspace).import_document(from_workspace, document)

    @tool(reading)
    async def read(workspace: str, document: str) -> Reading:
        """A document's current version exactly as it is, to read in full before citing it.

        Nothing in it is a handle: to cite part of it, search for that text. A source with no text of its own (a
        PDF) names the documents that transcribe it instead.
        """
        return tools(workspace).read(document)

    @tool(reading)
    async def search(workspace: str, phrases: list[str], document: str | None = None, within: str | None = None,
                     limit: int = 20) -> list[SearchHit]:
        """Matching text in `workspace` with exact handles, and whether each match's support reaches declared
        sources.

        Searches every document's current version, or only `document`, or only inside the span of a handle
        (`within`), e.g. to find a phrase inside a passage found earlier. Up to `limit` matches per phrase;
        each says which phrase it matched, where it is, the text around it (the match marked ▸like this◂),
        the handle of the passage it sits in, its support status and the kinds of citation over it.
        """
        return tools(workspace).search(phrases, document, within, limit)

    @tool(reading)
    async def trace(workspace: str, handle: str, limit: int = 30) -> Chain:
        """Whether the span a handle names rests on declared sources, and what it rests on, hop by hop.

        status is supported, partially_supported, unsupported, unknown or not_applicable, with issues saying
        where support falls short. Spans come nearest first, up to `limit`: the span itself, then each
        citation it rests on (its kind, its producer, if any, and the handles of what it rests on
        in turn), down to a region of a page, a declared source, or unsourced text, with the parts of each
        span no citation covers. Every span has its own handle, so evidence along the way can be cited
        directly. When truncated, trace a handle that was cut off; versions listed in frontier stayed
        in the workspace they were imported from.
        """
        return tools(workspace).trace(handle, limit)

    @tool(reading)
    async def draft(workspace: str, document: str) -> Draft:
        """A document as a draft to edit: its text with its citations written in as tags, the evidence its
        handles point at, and the base to pass to write or edit."""
        return tools(workspace).draft(document)


    @tool(writing)
    async def write(
        workspace: str, document: str, draft: str, base: str | None = None, dry_run: bool = False,
        format: Writable | None = None,
    ) -> Outcome:
        """Write a whole draft, citations written in, as the document's next version in `workspace`.

        Leave `base` out only to create a new document, in `format` (markdown unless given); to change one,
        pass the base from draft() or from your last write. The outcome's status says what happened: "written",
        with the base for the next change and the support status of what was written; "refused", with issues
        saying what to fix, and nothing written; or with dry_run, "checked" if nothing would stop the write.
        """
        fmt = Format(format) if format is not None else None
        return tools(workspace).write(document, draft, base, dry_run, fmt)

    @tool(writing)
    async def edit(workspace: str, document: str, edits: list[Edit], base: str, dry_run: bool = False) -> Outcome:
        """Find-and-replace edits on the draft draft() showed for `base`, applied in order, then written.

        Each edit's `old` must occur exactly once in the text (tags included), unless replace_all. Every
        edit must apply and the result must pass, or nothing is written.
        """
        return tools(workspace).edit(document, edits, base, dry_run)

    return listed


def _available(served: Served) -> str:
    """The workspaces there when the server is created, each with its links, so the agent starts knowing what
    to name; a call naming one it can't work in says which it can then."""
    names = served.workspaces.names()
    if not names:
        return ("\nNo workspaces are available to you yet. A call naming one you can't work in lists those you "
                "can.\n")
    lines = [f"- {name!r}" + (f", linked: {', '.join(map(repr, links))}" if (links := served.workspaces.links(name))
                              else "") for name in names]
    return "\nWorkspaces you can work in:\n" + "\n".join(lines) + "\n"


def reported[**P, T](tool: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """The tool, with its ToolErrors raised as MCP tool errors: the SDK shows the agent only those messages.

    A ToolError is something the agent asked for that can't be done, and its message says why. Any other
    error is the store or Bond failing, which the agent can't fix: the SDK logs it and reports a crash.
    """

    @functools.wraps(tool)
    async def reported(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await tool(*args, **kwargs)
        except authoring.ToolError as e:
            raise ToolError(e) from e

    return reported
