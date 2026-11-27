"""A read-only JSON API over the workspaces a deployment offers, for the viewer in ui/. The contract is ui/API.md.

`bond view` (bond.cli) serves it on localhost, with the built UI that ships in the package; bond.mcp's view answers
it in process. Needs the `view` extra (fastapi, uvicorn).
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .. import details
from ..api import Bond
from ..formats import spec
from ..locators import LocatorError, VersionLocator
from ..models import Ref, UnknownDocument, UnknownVersion, VersionId
from ..parsers import Malformed, load_json
from ..workspaces import Workspaces
from . import viewer
from .viewer import GRAPH_LIMIT

# The built UI, packaged with bond: ui/'s `npm run build` writes it here.
STATIC = Path(str(resources.files("bond") / "static" / "viewer"))


def _workspace(request: Request, workspace: str | None = None) -> Bond:
    """The workspace a request names, by the name it is offered under; without one, the first offered."""
    offered: Workspaces = request.app.state.workspaces
    if workspace is None:
        names = offered.names()
        if not names:
            raise HTTPException(404, "no workspaces are offered")
        workspace = names[0]
    try:
        return offered.open(workspace)
    except KeyError:
        raise HTTPException(404, f"unknown workspace {workspace!r}") from None


SelectedBond = Annotated[Bond, Depends(_workspace)]


def create_app(
    workspaces: Workspaces, static_dir: str | os.PathLike[str] | None = STATIC, graph_limit: int = GRAPH_LIMIT,
) -> FastAPI:
    """The API over the workspaces a deployment offers, plus the built UI from static_dir.

    workspaces is read on every request, so what it offers may change while the app runs; whoever made it owns
    every Bond in it, and closes them once the app has stopped. Every data request selects a workspace by the name
    it is offered under (?workspace=), defaulting to the first offered. Versions in the workspaces a workspace
    links to are read through it, so the viewer can follow a citation into a policy library. /api/graph refuses
    a store of more than graph_limit versions; the UI browses such a store with /api/documents and
    /api/neighborhood instead.
    """
    app = FastAPI(title="Bond viewer API")
    app.state.workspaces = workspaces

    @app.get("/api/workspaces")
    def list_workspaces() -> list[viewer.WorkspaceView]:
        listed: list[viewer.WorkspaceView] = []
        for name in workspaces.names():
            try:
                w = workspaces.open(name).workspace
            except KeyError:  # no longer offered since names() was read
                continue
            listed.append({"id": w.id, "name": name, "created_at": w.created_at})
        return listed

    app.add_middleware(
        CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["GET"],
    )

    @app.exception_handler(UnknownVersion)
    @app.exception_handler(UnknownDocument)
    async def unknown(_: Request, e: UnknownVersion | UnknownDocument) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(ValueError)  # a malformed parameter, or a locator that is invalid: includes LocatorError
    async def bad_request(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def bad_params(_: Request, e: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        return JSONResponse({"detail": problems}, status_code=400)

    # Handlers are plain functions, so FastAPI runs them in its thread pool and a
    # slow read doesn't hold up the others: Bond is safe to share across threads.

    @app.get("/api/overview")
    def overview(bond: SelectedBond) -> viewer.Overview:
        return viewer.overview(bond, graph_limit)

    @app.get("/api/graph")
    def graph(bond: SelectedBond) -> viewer.Graph:
        count = len(details.versions(bond))
        if count > graph_limit:
            raise HTTPException(400, f"the store has {count} versions, more than the {graph_limit} drawn whole; "
                                     "browse it with /api/documents and /api/neighborhood")
        return viewer.graph(bond)

    @app.get("/api/neighborhood")
    def neighborhood(bond: SelectedBond, version: str, depth: int = 2, limit: int = 200) -> viewer.Neighborhood:
        if not 0 <= depth <= 5:
            raise HTTPException(400, "depth must be between 0 and 5")
        if not 1 <= limit <= 1000:
            raise HTTPException(400, "limit must be between 1 and 1000")
        return viewer.neighborhood(bond, VersionId(version), depth, limit)

    @app.get("/api/documents")
    def documents(bond: SelectedBond, q: str = "", offset: int = 0, limit: int = 50) -> viewer.DocumentsPage:
        if offset < 0:
            raise HTTPException(400, "offset must be non-negative")
        if not 1 <= limit <= 500:
            raise HTTPException(400, "limit must be between 1 and 500")
        return viewer.documents(bond, q, offset, limit)

    @app.get("/api/versions")
    def versions(bond: SelectedBond) -> list[viewer.VersionSummary]:
        summaries = viewer.Summaries(bond)
        return [summaries.summary(v) for v in details.versions(bond)]

    @app.get("/api/versions/{version}")
    def version(bond: SelectedBond, version: str) -> viewer.VersionDetail:
        return viewer.Summaries(bond).detail(details.version(bond, VersionId(version)))

    @app.get("/api/versions/{version}/blob")
    def blob(bond: SelectedBond, version: str) -> Response:
        v = VersionId(version)
        return Response(bond.blob(v), media_type=spec(details.version(bond, v).format).media_type)

    @app.get("/api/versions/{version}/citations")
    def citations(bond: SelectedBond, version: str) -> list[viewer.CitationView]:
        return viewer.citations(bond, bond.citations(VersionId(version)))

    @app.get("/api/versions/{version}/annotations")
    def annotations(bond: SelectedBond, version: str) -> viewer.Annotations:
        return viewer.claim_annotations(bond, VersionId(version))

    @app.get("/api/cited-by")
    def cited_by(bond: SelectedBond, version: str, locator: str | None = None) -> list[viewer.CitationView]:
        return viewer.citations(bond, bond.cited_by(_ref(bond, version, locator)))

    @app.get("/api/trace")
    def trace(bond: SelectedBond, version: str, locator: str | None = None, max_depth: int | None = None) -> viewer.TraceView:
        if max_depth is not None and max_depth < 0:
            raise HTTPException(400, "max_depth must be non-negative")
        return viewer.trace(bond, _ref(bond, version, locator), max_depth)

    @app.get("/api/search")
    def search(bond: SelectedBond, q: str, limit: int = 50, documents: str | None = None) -> list[viewer.SearchHitView]:
        if not q:
            raise HTTPException(400, "q must not be empty")
        if limit < 1:
            raise HTTPException(400, "limit must be at least 1")
        names = None if documents is None else _split(documents)
        return viewer.search_hits(bond, bond.search(q, documents=names, limit=limit))

    @app.get("/api/declarations")
    def declarations(bond: SelectedBond, version: str, locator: str | None = None) -> viewer.DeclarationsView:
        return viewer.declarations(bond, _ref(bond, version, locator))

    @app.get("/api/check-support")
    def check_support(bond: SelectedBond, version: str, locator: str | None = None, max_depth: int | None = None,
                      limit: int = 1000) -> viewer.SupportReportView:
        return viewer.check_support(bond, _ref(bond, version, locator), max_depth=max_depth, limit=limit)

    @app.get("/api/text")
    def text(bond: SelectedBond, version: str, locator: str | None = None) -> viewer.TextView:
        return {"text": bond.text(_ref(bond, version, locator))}

    @app.get("/api/{path:path}", include_in_schema=False)
    def no_route(path: str) -> None:
        raise HTTPException(404, f"no route /api/{path}")

    if static_dir is not None:
        dist = Path(static_dir)

        @app.get("/{path:path}", include_in_schema=False)
        def ui(path: str) -> Response:
            if not (dist / "index.html").is_file():
                return HTMLResponse(_UNBUILT, status_code=503)
            file = (dist / path).resolve()
            if path and file.is_file() and file.is_relative_to(dist.resolve()):
                return FileResponse(file)
            return FileResponse(dist / "index.html")  # client-side routes

    return app


_UNBUILT = """<!doctype html><meta charset="utf-8"><title>Bond</title>
<body style="font-family: system-ui; padding: 48px; max-width: 640px">
<h2>The viewer's UI isn't built</h2>
<p>This copy of Bond comes from a source checkout, which doesn't include the built UI. Build it with
<code>npm install</code> and <code>npm run build</code> in <code>ui/</code>, or run <code>npm run dev</code>
there to serve the UI on :5173 against this API.</p>
<p>The API is up: <a href="/api/overview">/api/overview</a>.</p>"""


def _ref(bond: Bond, version: str, raw: str | None) -> Ref:
    """A ref from a version id and a locator in its URL-encoded JSON form, as the viewer shows locators (a JSON
    value by its pointer); the whole version if the locator is absent."""
    v = VersionId(version)
    if raw is None:
        return Ref(v, VersionLocator())
    parsed = load_json(raw)
    if isinstance(parsed, Malformed):
        raise LocatorError(f"locator is not valid JSON: {parsed.reason}")
    if not isinstance(parsed, dict):
        raise LocatorError(f"locator must be a JSON object, got {raw!r}")
    details.version(bond, v)  # an unknown version is a 404, before its locator is read
    return Ref(v, viewer.located(bond, v, parsed))


def _split(raw: str) -> list[str]:
    """The non-empty items of a comma-separated query parameter."""
    return [s for s in raw.split(",") if s]
