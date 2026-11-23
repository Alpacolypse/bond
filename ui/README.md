# Bond UI

A browser viewer for Bond stores, one workspace at a time: a full-window graph of its documents (the whole store when it
is small, the neighborhood of an open document when it isn't, picked from a filterable list), a drawer with
per-format viewers that highlight each claim by how it is sourced, and click-through navigation
from a claim to the exact region it cites, all the way down to a box on a PDF page or a cell of a table. It reads
(the one write is adding sources, below) and talks to `bond.http.server` (run by `bond view`) through the contract in
[API.md](API.md). The same viewer is also Bond's [MCP app](#mcp-app), shown in Claude and other MCP hosts.

## Run

```bash
# from the repository root: build a demo Bond home and serve its API on :8000
uv run python examples/seed.py --home .bond-demo
uv run bond view --home .bond-demo --no-browser

cd ui
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000; add ?browse to browse by document
```

Without a server, open <http://localhost:5173/?mock> (or set `VITE_MOCK=1`): the API is answered
in the browser from a snapshot of the case 001 example, including its claims CSV (see [Mock](#mock)).

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server with `/api` proxied to `http://localhost:8000` |
| `npm run build` | Type-check, then build into the Python package, which the wheel ships: the viewer into `src/bond/static/viewer` (served by `bond view`), and the [MCP app](#mcp-app) into the single file `src/bond/static/app/index.html` |
| `npm run build:app` | Build only the MCP app |
| `npm test` | Vitest unit tests for the pure utilities, edge geometry, navigation, the Markdown renderer, uploads and the MCP backend |
| `npx tsc --noEmit` | Type-check only |

## Using it

- **Workspace**: the picker beside Bond switches between the stores offered by the server. Each store has
  its own graph, documents, and citations. Switching closes the drawer and resets navigation and cached reads;
  the selected workspace stays in the URL for reloads and browser back/forward. The demo has `case-001`,
  `case-003`, and `policies`; each client lives in their own case, with the shared policy imported locally.

- **Graph**: fills the window. One node per document, labelled by its title (the `title=` of a write), or its name
  if it has none; color is format, a thin outer ring marks an
  ingested source, size is citation count. Each curved arrow points from the citing document to the
  cited one (a pair citing each other gets two arrows bending apart); width grows with the number of
  citations. Hover a node to dim everything but it and its neighbours; labels show for those, and
  for every node in a small store (40 or fewer) or once you zoom in. Scroll to zoom, drag to pan or to move nodes.
- **Layouts**: the switch under the wordmark picks one. **rings** (the default) puts the sources at
  the centre and every document on the ring just outside everything it cites, so every arrow points
  inward and a document's ring is the length of its longest chain of citations down to a source.
  **columns** lays out the same levels left to right, like a pipeline. **free** is a force-directed
  layout. In rings and columns a dragged node springs back to its place.
- **Drawer**: click a node to open it in a drawer from the right (drag its left edge to resize).
  The graph keeps that node ringed, dims what isn't connected to it, and glides it into the middle
  of the visible area. **Esc**, the close button or a click on empty graph space closes it; the
  chevrons walk the navigation history.
- **Viewers**: claims are highlighted by sourcing: green **cited** (rests on another region), blue
  **declared source** (authority is not evaluated), amber **unsourced** (no supporting evidence declared), red
  wavy **undeclared** (text no citation covers). These colors show direct declarations; they do not summarize
  recursive support or factual correctness. Click a highlight to see the citations
  covering it, then choose a support to go there: the drawer switches to it, scrolls to and flashes
  the target, and the graph moves to the new node and briefly traces the edge just followed in
  amber. Markdown has a **source** toggle for the raw text. JSON is a collapsible tree. PDFs show a
  box for every region another document cites; click a box to see and follow the citing claims.
- **Add sources** (the MCP app only): pick files and they are added to the workspace as sources (`add_sources`,
  each file sent first in parts with `upload_part`), and the graph or document list reloads. If a name is taken,
  **Add as new versions** adds them as the next versions of those documents. `bond view` only reads: people add
  sources there with `bond add`.

## Architecture

```
src/
  api/        types.ts (API.md as TypeScript), client.ts (createApi over an HTTP or mock Backend,
              cached per URL since versions are immutable; main.tsx picks the backend and
              ApiContext provides the client), locators.ts (JSON Pointer, overlap, display,
              target resolution), useAsync.ts
  graph/      GraphView (react-force-graph-2d: focus, camera and trail), model.ts (the API graph as
              simulation data), layout.ts (levels and places for rings and columns), draw.ts
              (node, label, edge, arrow and guide painting), geometry.ts (curved, trimmed edges),
              LayoutSwitch, Legend
  drawer/     Drawer: the sliding panel's header, secondary row, resizing and Esc
  nav/        NavContext (history, open/closed, where each visit came from), marks.ts (highlight
              segmentation), CitationPopover, chips (shared small components)
  viewers/    DocumentViewer (loads a version, resolves the target, scroll + flash),
              MarkdownViewer, JsonViewer, TableViewer (CSV and TSV), PdfViewer (+ pdfBoxes.ts),
              MarkedText
  util/       offsets.ts (code point <-> UTF-16), csv.ts (cells as bond reads them), json.ts,
              useElementSize
  upload/     sources.ts (the add result), AddSources (the button and its status, in the MCP app's viewer)
  app/        the MCP app (app.html): host.ts (the ext-apps App and host context as a store), mcp.ts
              (tool calls, the MCP Backend, add_sources, base64), mode.ts (what a tool result shows),
              McpView (cards, the picker, the full viewer), polyfills.ts, app.css
  testing/    builders for API objects in tests
  mocks/      in-browser API for ?mock (development only)
```

**Tables.** A CSV or TSV version is drawn as a table with rows and columns numbered from 1, as in
handles; every row is data, so the first is drawn like the rest. `util/csv.ts` reads cells the way
bond's parser does (Python's csv module), and a test checks it against every cell of the mock
store, whose claim units carry bond's own text. A cell is highlighted by the citations of the cell, a
range in it, its row, its column or the whole version; a target row or column is tinted and outlined.

**Offsets.** Bond addresses text by Unicode code point; JavaScript strings index UTF-16 code units.
`util/offsets.ts` converts once per string, and `nav/marks.ts` turns citation claims and claim units
(code point ranges) into a partition of the string into UTF-16 *marks*, each with its covering
citations, its sourcing (derived from their kinds: cited > source > unsourced, and uncited only for
a claim unit nothing overlaps) and whether it is the navigation target. Sourcing is a display
category of the UI; the API returns kinds.

**Markdown** is parsed to mdast (`remark-parse` + `remark-gfm`) and rendered by our own small
renderer. mdast positions are UTF-16 offsets into the same source, so each text node renders just
the marks inside its own source range: a highlight that crosses emphasis, links, code or table cells
is split into pieces and stays exact. Nodes whose value differs from their source (escapes, entities)
are marked as a whole.

**JSON** leaves are highlighted by every citation whose pointer is the leaf or an ancestor (a range
applies only at the leaf itself), reusing the same mark builder over the leaf's displayed value.

**Navigation** targets are refs. A `citation` locator is resolved to that citation's claim in its
owner; a `version` locator opens the document at the top. Each history entry remembers the document
shown just before it (none if the drawer was closed): that is the edge the graph traces. Closing the
drawer keeps the history, so the drawer can slide out with its content and the chevrons still work
after reopening.

**Graph drawing.** Edges are drawn by hand rather than with force-graph's arrows, whose size is in
graph units. `graph/geometry.ts` bends each edge exactly like force-graph's `linkCurvature` (so the
hover particles ride the same curve) and trims it to the node boundaries, including the source ring
and the selection ring, so the arrowhead's tip touches the cited node. Line widths and arrowheads
are sized in screen pixels, so they read the same at every zoom, and arrowheads are painted after
nodes and labels (`onRenderFramePost`) so a label's halo never hides one. The camera centres the
selected node at `x + drawerWidth / 2 / zoom`, the middle of the part of the window the drawer
leaves visible; with nothing open it fits the whole graph, or in rings the outermost ring (on load,
on resize, on close and when the layout changes). Each frame, labels are placed greedily below,
above, right or left of their node, whichever side clears the labels and nodes placed before it.

**Layouts.** `graph/layout.ts` gives each document a level: 0 if it cites nothing, else one more
than the highest level it cites (self-citations and citations closing a cycle are ignored). That is
longest-path layering, as in the Sugiyama method for layered graphs; the shortest path would let
arrows point outward or along a ring. Within a level, documents are ordered by the barycenter
heuristic: a few sweeps outward (each faces the mean angle, or height, of what it cites) and inward
(of what cites it), then spaced evenly. In rings, that order is then relaxed: every document
pushes the others away, each edge pulls its ends together, and an edge and a document it passes
close to push apart, with each document only turning around its own ring. Clusters fan out over the
whole circle, documents stacked on one spoke stagger (so edges that skip a ring no longer run beside
the ones that don't), and lines go around documents rather than through them. Rings are as wide as
their documents need, and at least a fixed gap apart. Both layouts are deterministic. The simulation still runs, with link, charge and
centre forces off and one force pulling every node to its place, so nodes glide between layouts and
back after a drag. Every layout starts from the ring positions, so even the free one starts untangled.
force-graph's own `dagMode="radialin"` was not used: it layers from the citing end, so a source cited
only by a late document lands on an outer ring.

### Libraries

| Library | Why |
|---|---|
| React 19 + Vite + TypeScript (strict) | As requested; Vite's dev proxy and static build fit the Python server |
| `react-force-graph-2d` | Canvas force layout with custom node and link drawing, particles and camera control in one small API; Cytoscape is heavier and its styling model is more than we need |
| `pdfjs-dist` | The standard PDF renderer; used directly (no wrapper) because we only need page canvases plus our own overlay. Lazy-loaded, so it costs nothing until a PDF is opened |
| `unified` + `remark-parse` + `remark-gfm` | mdast with source offsets, so highlights can be split per text node; GFM for tables, which are claim units in Bond |
| `vitest` | Unit tests with the Vite config |

## MCP app

Bond's MCP server (`bond mcp`) serves `src/bond/static/app/index.html` as the `ui://` resource of its `show`, `write`,
`edit` and `workspaces` tools: hosts that support MCP Apps (`io.modelcontextprotocol/ui`) render it in a sandboxed
iframe and send it the tool's input and result. It is `app.html` + `src/app/`, built by `vite.app.config.ts` with
`vite-plugin-singlefile` into one self-contained file (every script and style inlined, about 2.4 MB, 0.75 MB
gzipped), because the iframe has no network access. It talks to the host with
[`@modelcontextprotocol/ext-apps`](https://github.com/modelcontextprotocol/ext-apps)' `App`.

- **A document** (after `show`, or a `write`/`edit` that went through): a card with the document's name, its support
  status and how many notes it has. **Open** asks the host for full screen and shows the whole viewer (`App`, the
  same component as `bond view`) with that document's current version open in the drawer: its head is found with
  `/api/documents?q=<name>`, and App's `open` prop navigates to it once. **Done**, or leaving full screen from the
  host, goes back to the card. A refused write (or a `dry_run` check) lists its issues instead.
- **The workspace picker** (after `workspaces`): every workspace in the Bond home, with **Grant**/**Revoke** when the
  person may grant, and **New workspace** when they may create one. A grant is told to the model with
  `updateModelContext`.
- **Add sources**, on the card, on each granted workspace in the picker and in the full viewer's top bar: the files are
  read as base64 and sent with the `add_sources` tool.

**The MCP Backend** (`src/app/mcp.ts`) implements the viewer's `Backend` over app-only tools that only the view can
call: `getJson(url)` calls `view_api(workspace, url)`, which answers `{status, body}` from the same handlers as
`bond view` (a status of 400 or more is raised as an `ApiError` with its `detail`); a version's bytes are read with
`view_blob(workspace, version, offset)` chunk by chunk until `next` is null, and handed to pdf.js as bytes
(`Backend.blobData`; a sandbox's CSP may forbid fetching even a `blob:` URL). pdf.js's worker runs on the main thread
(`globalThis.pdfjsWorker`), since the sandbox may refuse workers. The full viewer keeps its light styles; the cards
follow the host's theme and style variables.

## Mock

`src/mocks/server.ts` answers the routes the UI uses (`/graph`, `/versions/{v}`, `/citations`,
`/annotations`, `/cited-by` and the PDF blob) from `src/mocks/store.json`, following API.md and the
rules in `bond.http.viewer` and `bond.api` (overlap). It is only reachable through a dynamic import
behind `?mock`, so it is a separate chunk that the normal app never loads.

The snapshot is generated from the real library, so it can't drift from Bond's semantics:

```bash
uv run python ui/scripts/make_mock.py   # from the repository root
```

It contains case 001 (fax PDF, its Reducto OCR output, the policy, the summary) plus a reviewed copy of
the summary (`summary_case_001_reviewed`) with an astral character in its heading, a SOURCE region,
a self-citing conclusion and an uncited paragraph, so every sourcing is exercised.

## Known limitations

- One version per name is assumed: documents are labelled by their title (or `name` if they have
  none) alone, so a store holding several versions of a name shows several nodes with the same label, and there is no version
  switcher.
- Search, "cited by" lists and provenance traces exist in the API but not in the UI (the side panel
  was removed to keep the viewer simple).
- No deep links: the navigation state is not in the URL, so a view can't be bookmarked or shared.
- Markdown nodes whose rendered value differs from their source (escapes, entities, indented code)
  are highlighted as a whole rather than character-exactly; the source view is always exact.
- Images, HTML and front matter in Markdown are shown as placeholders or not at all.
- PDF pages are all rendered up front at the drawer width (fine for faxes, slow for very long PDFs),
  there is no zoom, and resizing the drawer re-renders them.
- The graph is fitted to the window, so a store with only a handful of documents is shown large.
- Large stores: the graph and the per-leaf JSON highlighting are not virtualized, and the layout is
  recomputed on every load (node positions are not remembered).
- The chosen layout is not remembered across reloads. In rings, a long edge (a digest citing
  documents on every ring) crosses the middle, and where a document and the one it cites line up
  on neighbouring rings, their labels may still crowd at low zoom.
