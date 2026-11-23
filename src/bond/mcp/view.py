"""The app view: Bond as an MCP App (io.modelcontextprotocol/ui), the document in the chat.

One HTML view, ui://bond/workspace, built from the viewer's components into a single self-contained file
(ui/'s `npm run build` writes it to bond/static/app/index.html). show, write and edit (bond.mcp.server) render it:
it shows the document with its citations, and a click on a claim follows the trace down to the span, cell or page
region it rests on, and it has a form for adding sources. It also holds the workspace picker, whose tools a
deployment adds (bond.local.grants) through extend.

The view fetches what it shows through app-only tools, so model tool results stay small (hosts drop results over
~150k characters before they reach a view):

- view_api(workspace, url): any GET of the viewer's JSON API (ui/API.md), answered by bond.http's handlers, as
  `bond view` answers it, in process.
- view_head(workspace, document): the version a document now points at, to open the viewer at.
- view_blob(workspace, version, offset): a version's bytes, in chunks.
- upload_part(upload, offset, data) and add_sources(workspace, files, update): files the person picked, sent in
  parts and then added as sources (bond.sources).

Those are a person's acts. The host keeps app-only tools from the model; a client that doesn't render apps
doesn't, so for it they are neither listed nor callable (AppOnly). And the acts that change anything (adding
sources, granting, revoking, creating) need a secret the server writes into the view's HTML, which hosts render
but don't give their models: a host that leaked app-only tools to its model still wouldn't let it act as the
person.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import re
import secrets
import tempfile
import weakref
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode

from mcp.server.apps import Apps, client_supports_apps
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, ListToolsResult, TextContent, ToolAnnotations

from .. import details
from ..api import Bond
from ..formats import spec
from ..models import BondError, VersionId
from ..sources import Added, NewSource, add_sources
from .server import Served, ServedTool

VIEW_URI = "ui://bond/workspace"
VIEW_FILE = Path(str(resources.files("bond") / "static" / "app" / "index.html"))
BLOB_CHUNK = 96 * 1024  # bytes per view_blob call: 128k characters of base64, under the hosts' ~150k
UPLOAD_LIMIT = 200 * 1024 * 1024  # bytes in one upload
UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
READING = ToolAnnotations(read_only_hint=True)
ACTING = ToolAnnotations(read_only_hint=False, destructive_hint=False)

AppTool = Callable[..., Awaitable[Any]]


def packaged_view() -> str | None:
    """The built view's HTML, or None if this copy of Bond doesn't have it (a source checkout, unbuilt)."""
    return VIEW_FILE.read_text(encoding="utf-8") if VIEW_FILE.is_file() else None


@dataclass(frozen=True)
class UploadedFile:
    """A file the person picked: its name, which becomes the document's, and the upload its parts were sent to."""

    name: str
    upload: str


@dataclass(frozen=True)
class UploadProgress:
    upload: str
    size: int  # bytes received so far: the offset of the next part


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Any  # the JSON the viewer's API answered (ui/API.md), or {"detail": ...}


@dataclass(frozen=True)
class Head:
    version: str  # the whole id: the viewer's API names versions by it


@dataclass(frozen=True)
class BlobChunk:
    data: str  # base64
    offset: int
    next: int | None  # the offset to ask for next, or None at the end
    size: int
    media_type: str


@dataclass(frozen=True)
class AddedSources:
    added: list[Added]
    note: str | None


@dataclass(frozen=True)
class ViewTools:
    """What a deployment gets to add its own app-only tools to the view (bond.local.grants adds the picker's).

    tool(name, annotations) registers one: called by the view, kept from the model, refused to a client that doesn't
    render apps, with a BondError shown as the tool's error. A tool that acts as the person takes a `person`
    argument and passes it to as_person first.
    """

    tool: Callable[[str, ToolAnnotations], Callable[[AppTool], AppTool]]
    as_person: Callable[[str], None]


def attach_view(
    html: str, viewed: Sequence[ServedTool], *, served: Served,
    extend: Callable[[ViewTools], None] | None = None,
) -> tuple[Apps, AppOnly]:
    """The Apps extension: the model tools in viewed bound to the view, the app-only tools (with any extend adds),
    and the view itself; and the middleware that keeps the app-only tools, and show, from clients that don't render
    apps."""
    apps = Apps()

    def opened(workspace: str) -> Bond:
        return served.open(workspace)

    # The person's acts need a secret that only the view holds: the host renders ui:// resources but doesn't hand
    # them to its model, so even a host that let its model see app-only tools wouldn't let it act as the person.
    # It's made each time the server starts; a view loaded before a restart has to be shown again.
    person = secrets.token_urlsafe(24)

    def as_person(given: str) -> None:
        if not secrets.compare_digest(given, person):
            raise BondError("only the person using Bond's view can do this; if the view was opened before Bond "
                            "restarted, show it again")

    for tool in viewed:
        apps.tool(resource_uri=VIEW_URI, name=tool.name, annotations=tool.annotations)(tool.fn)
    app_only: list[str] = [t.name for t in viewed if t.name == "show"]  # nothing to show without the view

    def app_tool(name: str, annotations: ToolAnnotations) -> Callable[[AppTool], AppTool]:
        def register(fn: AppTool) -> AppTool:
            app_only.append(name)
            return apps.tool(resource_uri=VIEW_URI, visibility=["app"], annotations=annotations, name=name)(
                _shown(fn))
        return register

    api: list[Any] = []  # the viewer's API over the served workspaces, made on first use

    @app_tool("view_api", READING)
    async def view_api(workspace: str, url: str) -> ApiResponse:
        """For Bond's view: a GET of the viewer's JSON API over workspace."""
        path, _, query = url.partition("?")
        if not path.startswith("/api/") or path.endswith("/blob"):
            raise BondError(f"{url!r} isn't a JSON route of the viewer's API")
        opened(workspace)  # one the agent can work in, or the reason it isn't
        if not api:
            from ..http.server import create_app

            api.append(create_app(served.workspaces, static_dir=None))
        params = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "workspace"]
        status, body = await _asgi_get(api[0], f"{path}?{urlencode([*params, ('workspace', workspace)])}")
        return ApiResponse(status, body)

    @app_tool("view_head", READING)
    async def view_head(workspace: str, document: str) -> Head:
        """For Bond's view: the version a document now points at, to open the viewer at."""
        found = opened(workspace).catalog.get_document(document)
        if found is None:
            raise BondError(f"there's no document named {document!r} in {workspace!r}")
        return Head(found.head)

    @app_tool("view_blob", READING)
    async def view_blob(workspace: str, version: str, offset: int = 0) -> BlobChunk:
        """For Bond's view: a version's bytes, a chunk at a time from offset."""
        bond = opened(workspace)
        v = VersionId(version)
        data = bond.blob(v)
        if not 0 <= offset <= len(data):
            raise BondError(f"offset {offset} is outside the version's {len(data)} bytes")
        end = min(offset + BLOB_CHUNK, len(data))
        return BlobChunk(base64.b64encode(data[offset:end]).decode("ascii"), offset,
                         end if end < len(data) else None, len(data),
                         spec(details.version(bond, v).format).media_type)

    # A big file comes in parts, as view_blob sends one out: hosts drop tool calls over ~150k characters. Parts
    # wait in a spool directory this process owns; an upload a restart interrupts is simply sent again.
    spool = tempfile.TemporaryDirectory(prefix="bond-uploads-")
    weakref.finalize(apps, spool.cleanup)

    def spooled(upload: str) -> Path:
        if not UPLOAD_ID.fullmatch(upload):
            raise BondError(f"{upload[:80]!r} can't name an upload (8 to 64 letters, digits, - or _)")
        return Path(spool.name) / upload

    @app_tool("upload_part", ACTING)
    async def upload_part(upload: str, offset: int, data: str, person: str) -> UploadProgress:
        """For Bond's view: the next part of a big file, at offset; the parts are added as one by add_sources."""
        as_person(person)
        path = spooled(upload)
        size = path.stat().st_size if path.is_file() else 0
        if offset != size:
            raise BondError(f"upload {upload} has {size} bytes, so its next part goes at offset {size}, not {offset}")
        try:
            part = base64.b64decode(data, validate=True)
        except binascii.Error as e:
            raise BondError(f"a part didn't arrive whole: {e}") from None
        if size + len(part) > UPLOAD_LIMIT:
            path.unlink(missing_ok=True)
            raise BondError(f"a file can be at most {UPLOAD_LIMIT // (1024 * 1024)} MiB")
        with path.open("ab") as f:
            f.write(part)
        return UploadProgress(upload, size + len(part))

    def received(f: UploadedFile) -> bytes:
        path = spooled(f.upload)
        if not path.is_file():
            raise BondError(f"{f.name}: upload {f.upload} has no parts here (sent to a server since restarted?)")
        return path.read_bytes()

    @app_tool("add_sources", ACTING)
    async def add_sources_(workspace: str, files: list[UploadedFile], person: str, update: bool = False,
                           ) -> AddedSources:
        """For Bond's view: files the person picked, added to workspace as sources."""
        as_person(person)
        result = add_sources(opened(workspace), [NewSource(f.name, received(f), None) for f in files],
                             update=update)
        for f in files:
            spooled(f.upload).unlink(missing_ok=True)
        return AddedSources(result.added, result.note)

    if extend is not None:
        extend(ViewTools(app_tool, as_person))

    page = html.replace("<head>", f'<head><meta name="bond-person" content="{person}">', 1)
    apps.add_html_resource(VIEW_URI, page, name="Bond", title="Bond",
                           description="A cited document, its sources, and the workspaces it lives in")
    return apps, AppOnly(frozenset(app_only))


def _shown[**P, T](tool: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """An app-only tool, with a BondError shown to the person as the tool's error."""

    @functools.wraps(tool)
    async def shown(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await tool(*args, **kwargs)
        except BondError as e:
            raise ToolError(str(e)) from e

    return shown


class AppOnly:
    """Middleware keeping app-only tools from a client that doesn't render apps: they aren't listed for it, and a
    call to one is refused. A host that renders apps keeps them from its model itself (the spec requires it)."""

    def __init__(self, names: frozenset[str]):
        self.names = names

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method == "tools/call" and (ctx.params or {}).get("name") in self.names and not client_supports_apps(ctx):
            return CallToolResult(content=[TextContent(type="text", text=(
                "that's only offered in a host that shows Bond's view"))], is_error=True)
        result = await call_next(ctx)
        if ctx.method != "tools/list" or client_supports_apps(ctx):
            return result
        # The handler's result arrives as the model or, once serialized, as its dict.
        if isinstance(result, ListToolsResult):
            return result.model_copy(update={"tools": [t for t in result.tools if t.name not in self.names]})
        if isinstance(result, dict) and isinstance(listed := result.get("tools"), list):
            return {**result, "tools": [t for t in listed if not (isinstance(t, dict) and t.get("name") in self.names)]}
        return result


async def _asgi_get(app: Any, url: str) -> tuple[int, Any]:
    """GET url from an ASGI app in process: the viewer's API answers the view as it answers a browser."""
    path, _, query = url.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": unquote(path), "raw_path": path.encode(), "query_string": query.encode(), "headers": [],
             "client": ("127.0.0.1", 0), "server": ("bond", 80), "root_path": ""}
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    status, body = 500, bytearray()

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status, json.loads(body) if body else None
