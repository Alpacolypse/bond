"""Bond for agents: the authoring tools over MCP, over whatever workspaces a deployment offers.

Run with: bond mcp (see bond.cli). Needs the `mcp` extra. It speaks MCP over stdio, so a client (Claude Code,
Claude Desktop, an agent harness) starts it.

The server only carries calls. It serves the workspaces it is given (bond.workspaces.Workspaces), looked up on
every call, with the policy it binds them to (AGENT_POLICY: every word covered, no `source`). The agent names the
workspace on every call and the server remembers none: it may restart, or serve several chats at once, so a call
means the same whenever it is made. The agent can't change what a workspace is bound to, nor reach a workspace it
wasn't offered except to read and cite one the workspace links to. Issues from a write come
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
from ..authoring import AGENT_POLICY, Chain, Edit, Listing, Outcome, Reading, SearchHit, Tools
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
search(workspace, phrase, within=<passage handle>). Copy handles from search results; never make one up. \
A span whose document has moved on to a newer version names it in "newer"; search that for the same text and \
cite it instead, unless the older version is what you mean.
   trace(workspace, handle) gives the same support status with the reasons it falls short, and what the span \
rests on, hop by hop, down to its sources, each with its own handle. Neither evaluates whether a claim is true.
3. write(workspace, document, draft) creates a document. To change one, read(workspace, document, \
citations=true) gives it with its citations written in as tags, and its base; then edit(workspace, document, \
edits, base) changes that text, or write(workspace, document, draft, base) replaces it. Each successful write \
returns the next base, and the support status of what you wrote, with warnings for anything it cites that has a \
newer version. If someone else changed the document since your base, read it again with citations=true.

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

LINKS = """
A workspace may link to others (a shared policy library, say): you can read, search and cite their documents from \
the workspace, but you write only in the workspace itself. search(workspace, phrases) covers the workspace and \
every workspace it links to, and each hit names the workspace it is in; documents(workspace, \
from_workspace=...) lists a linked workspace's documents, read(workspace, document, from_workspace=...) reads \
one, and search(workspace, phrases, from_workspace=...) searches only it. Cite a linked document's handles as \
they are.
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
    """The workspaces a server offers the agent, and the policy every write must meet. The deployment that made
    workspaces owns every Bond in it, and closes them."""

    workspaces: Workspaces
    policy: Policy = AGENT_POLICY

    def open(self, workspace: str) -> Bond:
        """The workspace the agent named, or a ToolError saying which it can work in."""
        try:
            return self.workspaces.open(workspace)
        except KeyError:
            names = ", ".join(map(repr, self.workspaces.names())) or "none yet"
            raise authoring.ToolError(f"{workspace!r} isn't a workspace you can work in (you can in: {names})") from None

    def tools(self, workspace: str) -> Tools:
        """The authoring tools for the workspace the agent named, bound to the policy."""
        return Tools(self.open(workspace), self.policy)


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
    """The authoring tools over workspaces (bound to AGENT_POLICY unless given as Served), with the app view if view, its HTML, is given (bond.mcp.view.packaged_view()).

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
        found = bond.filing.document(document)
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
    """What the agent is told when it connects: how to cite, how links work, and the workspaces there now."""
    return INSTRUCTIONS + LINKS + _available(served)


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

    # Tools are async, so calls run one at a time on the event loop, as an agent makes them. Bond is
    # safe to share across threads, so this keeps calls in order rather than keeping Bond safe.

    @tool(reading)
    async def documents(workspace: str, from_workspace: str | None = None) -> list[Listing]:
        """Every document in `workspace`, oldest first, with the format of its current version (a PDF has no text
        of its own: its text is cited through a transcription); or, with `from_workspace`, the documents of a
        workspace `workspace` links to, which you can read, search and cite from `workspace`."""
        return tools(workspace).documents(from_workspace)

    @tool(reading)
    async def read(workspace: str, document: str, from_workspace: str | None = None,
                   citations: bool = False) -> Reading:
        """A document's current version and its base (to pass to write or edit); with `from_workspace`, a
        document of a workspace `workspace` links to.

        By default exactly as it is, to read in full before citing it: nothing in it is a handle, so to cite part
        of it, search for that text. With `citations`, its citations are written in as tags, with the evidence
        their handles point at: the text edit() changes, so read this way before editing. A source with no text
        of its own (a PDF) names the documents that transcribe it instead.
        """
        return tools(workspace).read(document, from_workspace, citations)

    @tool(reading)
    async def search(workspace: str, phrases: list[str], document: str | None = None, within: str | None = None,
                     limit: int = 20, from_workspace: str | None = None) -> list[SearchHit]:
        """Matching text with exact handles, and whether each match's support reaches declared sources.

        Searches every document's current version in `workspace` and in every workspace it links to, or only in
        `from_workspace` (one it links to), or only `document` (in `from_workspace` if given, else in
        `workspace`), or only inside the span of a handle (`within`), e.g. to find a phrase inside a passage
        found earlier. Up to `limit` matches per phrase and workspace; each says which phrase it matched, the
        workspace it is in, where it is, the text around it (the match marked ▸like this◂), the handle of the
        passage it sits in, its support status and the kinds of citation over it.
        """
        return tools(workspace).search(phrases, document, within, limit, from_workspace)

    @tool(reading)
    async def trace(workspace: str, handle: str, limit: int = 30) -> Chain:
        """Whether the span a handle names rests on declared sources, and what it rests on, hop by hop.

        status is supported, partially_supported, unsupported, unknown or not_applicable, with issues saying
        where support falls short. Spans come nearest first, up to `limit`: the span itself, then each
        citation it rests on (its kind, its producer, if any, and the handles of what it rests on
        in turn), down to a region of a page, a declared source, or unsourced text, with the parts of each
        span no citation covers. Every span has its own handle, so evidence along the way can be cited
        directly, wherever it is. When truncated, trace a handle that was cut off; versions listed as
        unreachable are in no workspace `workspace` links to now.
        """
        return tools(workspace).trace(handle, limit)



    @tool(writing)
    async def write(
        workspace: str, document: str, draft: str, base: str | None = None, dry_run: bool = False,
        format: Writable | None = None,
    ) -> Outcome:
        """Write a whole draft, citations written in, as the document's next version in `workspace`.

        Leave `base` out only to create a new document, in `format` (markdown unless given); to change one,
        pass the base from read() or from your last write. The outcome's status says what happened: "written",
        with the base for the next change and the support status of what was written; "refused", with issues
        saying what to fix, and nothing written; or with dry_run, "checked" if nothing would stop the write.
        """
        fmt = Format(format) if format is not None else None
        return tools(workspace).write(document, draft, base, dry_run, fmt)

    @tool(writing)
    async def edit(workspace: str, document: str, edits: list[Edit], base: str, dry_run: bool = False) -> Outcome:
        """Find-and-replace edits on the text read(citations=true) showed for `base`, applied in order, then written.

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
    lines = []
    for name in names:
        links = [b.workspace.name for b in served.workspaces.open(name).links()]
        lines.append(f"- {name!r}" + (f", links to: {', '.join(map(repr, links))}" if links else ""))
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
