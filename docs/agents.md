# Agents

`bond.authoring` is how an LLM agent (or anything that would rather not handle offsets) uses Bond: it reads and writes text with inline citation tags, never offsets or version ids. `Tools` binds a workspace, a policy and the workspaces it may import from, for a host to hand to an agent; `bond mcp` serves the same tools over MCP.

## The authoring tools

```python
from bond import SupportStatus
from bond.authoring import Edit, Tools

tools = Tools(case, sources={"policies": policies})   # policy: AGENT_POLICY by default
tools.read("fax_case_001.reducto")                    # the whole document as it is, to read before citing

hits = tools.search("No significant damage to other rooms.", document="fax_case_001.reducto")
hit = next(h for h in hits if h.support is SupportStatus.SUPPORTED)
# hit.handle "d246983a/result/chunks/1/blocks/4/content@131-168" (the exact span), hit.kinds ["transcription"],
# hit.where "page 2 · /result/chunks/1/blocks/4/content", the text around it, and the handle of its passage.
# The same words also appear, unsourced, in Reducto's chunk-level text: those hits come back "unsupported".

out = tools.write("notes", f'<bond:cite kind="quote" from="{hit.handle}">No significant damage to other rooms.</bond:cite>')
# Written(base="…", advanced=True, support=SupportStatus.SUPPORTED): the base for the next change, and whether it all
# rests on sources; or Refused(issues=[…]), every issue in the agent's terms, with nothing written

tools.trace(hit.handle)            # support status and reasons, then what it rests on, hop by hop: the OCR's text, a region of page 2
draft = tools.draft("notes")       # the text with its citations as tags, and its base
tools.edit("notes", [Edit(old="…", new="…")], draft.base)     # find-and-replace on draft.text
```

| Tool | Does |
|---|---|
| `documents(workspace=None)` | The documents in this workspace, or in one it may import from. |
| `import_document(workspace, document)` | Bring a document in with its identity and citations; host-set `import_depth` (default 0) and `import_limit` (default 100) bound what comes along, and the result's `frontier` names what stayed behind. Importing again brings the newest version. A document of the same name the agent wrote itself is never replaced. |
| `read(document)` | A document exactly as it is, to read before citing. |
| `search(phrases, document=None, within=None, limit=20)` | Matches with exact handles, each with its `support` status, the `kinds` of citation over it, where it is, the text around it, and its passage's handle. Every match is returned; the agent prefers supported ones. |
| `trace(handle, limit=30)` | The span's support `status` and `issues`, then what it rests on, nearest first, down to a page region, a declared source or unsourced text: each span with its handle, its citations and its `undeclared` parts. |
| `draft(document)` | The document as a draft to edit: its text with its citations as tags, the evidence they point at, and its `base`. |
| `write(document, draft, base=None, dry_run=False, format=None)`, `edit(document, edits, base, dry_run=False)` | Write a whole draft, or find-and-replace on the draft `draft` returned. The outcome's `status` says what happened: `written`, with the next `base` and the `support` status of what was written; `refused`, with `issues` in the agent's terms and nothing written; or, with `dry_run`, `checked` if nothing would stop the write. |

## Citation markup

Citations are inline tags around the text they cite. Supports are handles from a search or draft, or `#id`: the text of another tag, or of a `<bond:anchor id="…">`, in the same draft. An anchor names part of a sentence without citing it, so a conclusion can rest on just that part:

```
<bond:cite kind="quote" from="d246983a/result/chunks/1/blocks/4/content@12-63"><bond:anchor id="drip">Active drip</bond:anchor> from the bathroom ceiling light fixture</bond:cite>.

<bond:cite kind="paraphrase" from="#drip">The leak is still active.</bond:cite>
```

A quote's text must match its support, so the full stop sits outside the tag: punctuation needs no citation.

**JSON** is written the same way: a value cited whole is wrapped as `{"$bond": {"cite": {"kind": "quote", "from": "…"}}, "value": …}`. Everything Bond adds is inside `$bond`, mirroring the tags: `cite` (one citation or a list) and `anchor` (an id that names the value for `#id`). Part of a string is tagged as above, in single quotes. JSON is stored in one layout (two-space indent, non-ASCII as is, trailing newline), whatever the draft's.

**CSV and TSV** are written as the table itself, with tags inside its cells: a wrapping tag cites part of a cell, and a self-closing tag cites its cell, or with `on='row'` or `on='column'` its row or column. Citations live in cells, so they move with them when rows and columns are moved, inserted or deleted.

```
"<bond:cite kind='unsourced' on='row'/>code",description
"<bond:cite kind='quote' from='2c1394cc:r4:c5'/>R300","<bond:cite kind='quote' from='2c1394cc:r4:c6@0-7'>Ceiling</bond:cite> repair"
```

## Rules the layer keeps

- **The core still enforces everything.** The tools compile tags into citations and write through `write_document`; tags are never stored. `AGENT_POLICY` requires every word covered and allows only `quote`, `paraphrase` and `unsourced`, so an agent can't mark its own text `source`, nor adopt an imported document as one.
- **Edits carry citations forward.** An agent edits the draft it was given, so untouched sentences keep their tags. An edit's `old` must occur exactly once (or set `replace_all`); a batch of edits is written all or nothing.
- **The layer stores nothing and decides nothing.** It is a translator built only on the core, so every write must meet the core's structural checks and selected policy. This does not guarantee correct evidence selection or semantic support. Nothing in it is specific to agents beyond the default policy: a human editor can use the same form.
- **Rendering and compiling are inverses.** Compiling what `draft` rendered gives back the same bytes and citations, so the same version id. Tag ids (`#c1`, `#c2`) are names within one draft and never stored; `draft` places anchors (`a1`, `a2`, …) wherever a stored citation rests on a span no citation claims.
- **A citation never silently moves.** Nothing re-points a citation at newer evidence.
- **Changes start from an explicit base.** `draft` returns the base (the version drafted from, as a handle), and each successful write returns the next one. A document that exists can't be changed without one, and if it changed since, the write is refused as stale.
- **`<bond:` and `$bond` are reserved.** Other HTML in Markdown is ordinary content. Tags and anchors nest but can't partly overlap, so a version whose citations' claims partly overlap (nothing writes one today) can be viewed, searched and cited, but not read for editing.

## Handles

Handles are readable and self-contained: a version's id prefix (the shortest unique one, at least 8 characters, as git abbreviates commits) and the locator written out. Nothing is stored to resolve one, and a prefix that names no version, or more than one, is refused. A version outside the workspace is named by its whole id, which can't be resolved until it is imported.

| Addresses | Handle |
|---|---|
| A whole version | `88dd9008` |
| A range of text | `88dd9008@305-393` |
| A range in a JSON value (pointer percent-encoded, so a handle never holds a space or a quote) | `c3a863cb/result/chunks/1/blocks/4/content@6-33` |
| A PDF region | `9f1e2a7c:p2:0.114,0.294,0.601,0.352` |
| A table's cell, part of a cell, row or column | `4b7e0d21:r3:c2`, `4b7e0d21:r3:c2@0-5`, `4b7e0d21:r3`, `4b7e0d21:c2` |

Pages, rows and columns in handles count from 1.

## Over MCP

```bash
bond mcp --workspace smith --workspace jones                       # workspaces in the Bond home, with their links
bond mcp --workspace ./cases/case-001 --import-from ./policies [--import-depth 0] [--import-limit 100]
bond mcp --postgres postgresql://… --name case-001 --s3-bucket evidence --import-from policies
bond mcp --grants desktop                                          # the workspaces a person granted (grant mode)
```

`bond mcp` (with the `mcp` extra) serves the tools over stdio, with its workspaces, `AGENT_POLICY` and the workspaces each may import from bound when it starts; the agent can't reach any other workspace.

- `--workspace` repeats. A name opens a workspace in the Bond home, which must exist, and imports from the workspaces it links to (see [Running Bond locally](local.md)); a directory is created as a workspace if it holds none.
- `--import-from` adds an import source to every workspace served. `--import-depth all` copies every hop.
- With Postgres it serves one workspace, `--name`, and `--import-from` names other workspaces in the same database.
- `--grants NAME` serves the workspaces a person granted that configuration; see [Grants](local.md#grants).

The server keeps no current workspace, since it may restart or serve several chats, so every tool takes the workspace as its first argument:

| Tool over MCP | Does |
|---|---|
| `documents(workspace, from_workspace=None)` | The documents in `workspace`, or in `from_workspace`, one of its links. |
| `import_document(workspace, from_workspace, document)` | Bring a document into `workspace` from one of its links, as `Tools.import_document` does. Always offered; refused, naming the links, when `from_workspace` isn't one. |
| `read`, `search`, `trace`, `draft`, `write`, `edit` | As above, with `workspace` first: `read(workspace, document)`, `search(workspace, phrases, …)` and so on. |
| `show(workspace, document)` | Present a document in the app view; offered only to clients that render apps. |
| `workspaces()` | In grant mode: list the granted workspaces and show the person a picker to grant another. |

Citing stays within a workspace: a handle found in one names nothing in another, and a linked workspace's documents are cited only once imported. The server's instructions tell the agent how to cite, to name the workspace on every call, that a PDF's text is cited through a transcription, and which workspaces it can work in, with their links, when the server was created. A write's issues come back as its result, for the agent to fix and write again; a call that can't be carried out (a workspace it can't work in, a document that doesn't exist, a handle that names nothing) is a tool error with the reason, naming the workspaces or links it could have used.

### Building a server in Python

```python
from bond.mcp.server import Served, authoring_tools, create_server
from bond.mcp.view import packaged_view
from bond.workspaces import Fixed

workspaces = Fixed({"case-001": case}, {"case-001": {"policies": policies}})  # workspaces, and what each may import from
server = create_server(workspaces, view=packaged_view())
```

`create_server` takes any `bond.workspaces.Workspaces`. The workspaces are looked up on every call and never copied, so ones offered as access is granted are served from then on. `Served(workspaces, policy=..., import_depth=..., import_limit=...)` binds a policy other than `AGENT_POLICY`, or other budgets. `authoring_tools(Served(workspaces))` lists the tools, so a host can serve them its own way; `create_server` without a view serves just them.

For grant mode, `bond.local.grants.create_server(GrantedWorkspaces(home, name), view=...)` builds the server `bond mcp --grants` runs (`GrantedWorkspaces` is the home's `Workspaces`: what was granted to `name`, with the home's links). A deployment adds model tools, instructions and app-only tools the same way grant mode does (`tools`, `more`, `extend`).

### The app view

Given the built view (the one packaged in `bond/static/app`), the server is also an MCP App: `write` and `edit` render `ui://bond/workspace` in hosts that support apps (Claude Desktop and claude.ai, ChatGPT, Cursor, Goose; VS Code experimentally). It shows the document with its citations, and a click on a claim follows its trace to the span, cell or page region it rests on. `show` returns only the document's name and support status for the view to render; elsewhere `read` is the whole document already. This works over any workspaces, Postgres included.

The view fetches what it shows through app-only tools, so tool results stay small:

- `view_api(workspace, url)` answers any GET of the [viewer's API](../ui/API.md) with the same handlers as `bond view`.
- `view_head(workspace, document)` names a document's current version.
- `view_blob(workspace, version, offset)` returns a version's bytes in chunks.

A person's acts are app-only too: `add_sources`, naming files first sent in parts with `upload_part`, and in grant mode `view_workspaces`, `grant_workspace(name, granted)` and `new_workspace`.

A host that renders apps keeps app-only tools from its model; for a client that doesn't, Bond neither lists them nor answers a call to one. The acts that change anything (adding sources, granting, revoking, creating) also need a secret the server writes into the view's HTML at each start, which hosts render but don't give their models. So a host that leaked app-only tools to its model still wouldn't let it act as the person.
