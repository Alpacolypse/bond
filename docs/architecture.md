# Architecture

Bond is a core, two APIs over it, and deployments that decide where workspaces live and who reaches them.

- The **core** (`bond`) keeps provenance. It is also the API for software: a program opens workspaces and calls `Bond` directly, as importers do. It stores no decision about where workspaces live or who may reach them.
- **`bond.mcp`** is the API for agents: the authoring tools over MCP, and the document in the chat (the app view, `show`, adding sources). **`bond.http`** is the read-only JSON API the viewer uses.
- A **deployment** decides which workspaces an API offers and which each links to (whose versions its citations may rest on). It hands the first over as a `bond.workspaces.Workspaces`: `names()` and `open(name)`, read on every call; each `Bond` it opens carries its links (`Bond.set_links`, a list or a function read on every call). `Fixed` holds workspaces a program opened itself (directories, Postgres). **`bond.local`** is one deployment: one person's computer, where workspaces live in a home and the person decides who reaches them (see [Running Bond locally](local.md)).

## Layering rules

The core imports neither API nor `bond.local`, `bond.http` imports neither `bond.mcp` nor `bond.local`, and `bond.mcp` never imports `bond.local`. `tests/test_layers.py` checks this. Only `bond.cli` ties them together.

So a deployment with its own stores and access control takes the core and the APIs it wants, implements `Workspaces`, and leaves `bond.local` out.

Changes must also keep the [invariants](concepts.md#invariants).

## Source layout

```
src/bond/
  api.py          the Bond class: the core API
  models.py       plain data types passed in and out of the core API
  details.py      lower-level reads for tools built on the core: version records, places, JSON Pointers, reverse lookups
  formats.py      the format registry: each format's media type, suffixes, sniffing, parser and whether it has text
  locators.py     locator types (and what each addresses), validation, containment and overlap
  kinds.py        citation kinds and their validators
  policies.py     policies: what a version must meet beyond integrity (STRICT, ALLOW_GAPS, ...)
  parsers.py      Markdown, JSON, CSV/TSV and PDF parsed documents
  support.py      the recursive support check
  normalize.py    quote normalization with raw-offset mapping
  ids.py          the hashes that name blobs, citations and versions
  jsonvalue.py    JSON as a type
  storage/        the Store and Blobs protocols (store.py, __init__.py); what Bond keeps in a store: versions
                  (records.py), documents (filing.py), reverse lookups (index.py), workspaces (workspace.py),
                  stored JSON (values.py);
                  SQLite, Postgres (sql.py) and memory stores; local and S3 blobs
  workspaces.py   the Workspaces protocol: what a deployment offers an API, and Fixed
  sources.py      adding files to a workspace as sources (bond add, the app view)
  transcription.py  the text of a source with none of its own, as a document citing where it was read: the
                  Transcription and Transcriber protocols, record, transcribe
  importers/      each producer's Transcription: a PDF's text layer (pypdf, run by Bond), Reducto's parse
  authoring/      read and write as text with inline citations: codecs, markup, handles and the tools
  mcp/            the API for agents: the authoring tools over MCP (server), and the document in the chat
                  (view: the app view, show, adding sources), over any Workspaces
  http/           the read-only JSON API for the viewer (server, ui/API.md), from whole-workspace reads (viewer)
  local/          one person's machine: the Bond home (home.py; home.db: links, grants), grant mode
                  (grants.py, GrantedWorkspaces) and its picker, the Claude Desktop extension (desktop.py)
  cli.py          the bond command: view, mcp, and the person's commands (new, add, transcribe, link, grant)
ui/               the viewer and app view (React, Vite); built into src/bond/static/, not committed
packaging/mcpb/   the Claude Desktop extension: manifest, entry point, build script
fixtures/         synthetic faxes to a legal aid office, their OCR output, an expense log and intake policies (all invented)
examples/         case_001.py: the end-to-end example; workspaces.py: a policies workspace and a workspace per case;
                  seed.py: the viewer's demo Bond home
tests/
```
