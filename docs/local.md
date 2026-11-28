# Running Bond locally

`bond.local` is one deployment of Bond: one person's computer, where workspaces live in a Bond home and the person decides which agents reach them. Everything here is driven by the `bond` command.

## The Bond home

Workspaces live in a **Bond home**: one store (`bond.db`) and one blob directory (`blobs/`) holding every workspace by name, as a hosted deployment's database does, so a policy several cases cite is stored once:

| Platform | Default home |
|---|---|
| macOS | `~/Library/Application Support/Bond` |
| Windows | `%LOCALAPPDATA%\Bond` |
| Linux | `$XDG_DATA_HOME/bond` (or `~/.local/share/bond`) |

`BOND_HOME`, or `--home`, moves it. Keep it outside the folders people keep their files in, and outside synced ones (iCloud Drive, OneDrive, Dropbox), where a SQLite store can be corrupted. Links and grants are rows in the home's `home.db`. Wherever a command takes a workspace, it is a name in the home (`smith`); a program that wants a workspace in a directory of its own opens it with `Bond.local(path)`.

## Commands

| Command | What it does |
|---|---|
| `bond new NAME` | Create a workspace in the home. |
| `bond add WORKSPACE FILE... [--as NAME] [--update]` | Add files as sources. Each is copied in, so the original can change or go without changing Bond; its document is named after the file, and its history records where it came from (`HistoryEntry.origin`, the file's absolute path). A name already taken is refused unless `--as` gives another or `--update` adds the file as that document's next version; a file the same as the current version adds nothing. Every file is checked first (it exists, Bond reads its format, names don't collide), so a refusal adds none of them. |
| `bond transcribe WORKSPACE DOCUMENT [--with PRODUCER] [--output FILE [--of VERSION]]` | Write the transcription of a source with no text of its own (a PDF) as `DOCUMENT.PRODUCER`. By default Bond runs a producer that reads the source's format: for a PDF, `pypdf` reads its own text layer, and a scan or fax, which has none, is refused. `--with reducto --output parse.json` records a parse Reducto returned to you instead: Bond runs no vendor. Add `--of VERSION` when the document may have moved on since you sent its bytes, so the parse cites the version it read. Transcribing the same source again changes nothing. See [Transcriptions](python-api.md#transcriptions). |
| `bond link WORKSPACE --to OTHER [--remove]` | Let `WORKSPACE`'s documents cite `OTHER`'s where they are (a case citing a policy library), and what `OTHER` links to in turn. A link gives read access only: an agent in `WORKSPACE` can read, search and cite `OTHER`, never write there. A link that would loop (`OTHER` already reaches `WORKSPACE`) is refused. Removing one leaves what already cites `OTHER` unreachable from `WORKSPACE`, until it is linked again. See [Workspaces and links](python-api.md#workspaces-and-links). |
| `bond grant WORKSPACE --to NAME [--remove]` | Let the agent of the server configuration `NAME` (`bond mcp --grants NAME`) work in `WORKSPACE`, or stop it: for hosts with no view to grant from. |
| `bond workspaces` | List the workspaces in the home, with what each links to and who it's granted to. |
| `bond mcp --workspace A [--workspace B ...]` | Serve the agent those workspaces, each linked to what it links to in the home. Each must already be in the home (`bond new`). See [Over MCP](agents.md#over-mcp). |
| `bond mcp --grants NAME` | Serve the workspaces a person granted the configuration `NAME` (grant mode, below). |
| `bond view` | Browse workspaces in the viewer (below). |

A PDF is added as a source like anything else, but it holds no text: it can't be searched or cited until a document transcribes it. `bond add` says so when it adds one.

## Grants

In grant mode the agent starts with no workspaces. It calls `workspaces()`, which shows the person a picker in the chat (the [app view](agents.md#the-app-view)); their click grants one, and from the next call the agent works there.

A grant belongs to a server configuration, named by `--grants` (the Claude Desktop extension's is `desktop`), not to a process or a chat: the host may restart the server or share one process between chats, and the server can tell neither apart. So a grant survives restarts and covers every chat that configuration serves until it's revoked, with `bond grant … --remove` or in the picker.

## Only people add sources

No tool the model can call adds a source, writes a transcription, creates a workspace, grants one or changes a link, and `bond view` only reads. Bond's protocol prevents an agent's mistakes: uncited claims, made-up handles, stale edits, its own text declared a source or a producer's reading.

It doesn't stop an agent that has a shell running as you, which could run `bond add`, record made-up output with `bond transcribe --output`, or write to a store directly. That boundary is the host's. In Claude Code, deny it in `.claude/settings.json` (Linux paths shown; use your home's):

```json
{
  "permissions": {
    "deny": [
      "Bash(bond new:*)", "Bash(bond add:*)", "Bash(bond transcribe:*)", "Bash(bond link:*)", "Bash(bond grant:*)",
      "Bash(uv run bond new:*)", "Bash(uv run bond add:*)", "Bash(uv run bond transcribe:*)",
      "Bash(uv run bond link:*)", "Bash(uv run bond grant:*)",
      "Edit(~/.local/share/bond/**)", "Write(~/.local/share/bond/**)"
    ]
  }
}
```

Command rules match by prefix, so they stop the obvious calls, not a determined script: for that, run Claude Code with its sandbox on, which keeps shell writes inside the project. Keep the home outside any folder Cowork can reach. Agents in Claude Desktop's and claude.ai's chat have no shell.

## The viewer

A graph of a workspace's versions, with Markdown, JSON, table (CSV and TSV) and PDF viewers where cited content links to what it cites. `bond view` (with the `view` extra) serves it read-only on localhost and opens a browser:

```bash
bond view                                                   # every workspace in the Bond home
bond view --workspace smith --workspace jones               # some of them
```

It opens workspaces as they are and never creates one: each `--workspace` given, or with none every workspace in the home. The picker beside Bond switches between them. Workspaces on other stores (Postgres, S3) are served from Python: `bond.http.server.create_app(Fixed({...}))` is the app `bond view` runs (see [Storage](storage.md)).

It listens on 127.0.0.1 unless you pass `--host`. **It has no authentication**, so anyone who can reach it can read every offered workspace.

A workspace of up to 300 versions (`--graph-limit`) is drawn whole. A bigger one is browsed: filter the document list, open a document, and the graph shows the versions within two citation hops of it, both what it rests on and what rests on it (every version next to it, and at most 25 more from each further out, so a policy every case cites doesn't swamp the view). Navigating to a version outside the graph re-centres it there. `?browse` browses any workspace this way. Versions in a linked workspace open like the workspace's own, and unreachable ones (cited, but held by no workspace it links to now) are drawn as dashed outlines that can't be opened.

### The demo home

```bash
uv run python examples/seed.py --home .bond-demo     # case-001, case-003 and policies; each case links to policies
(cd ui && npm install && npm run build)              # builds the UI into src/bond/static/viewer
uv run bond view --home .bond-demo
```

Use the workspace picker to switch between `case-001` (Jordan Avery), `case-003` (Samuel Okafor) and `policies`; all of it is synthetic. Each case links to `policies` and cites the same policy version there, and no case contains the other client's documents; following a citation of the policy opens it in `policies`. To view one case alone, add `--workspace case-001`. `--force` replaces a demo home from an earlier run, and nothing else.

For tables, select `case-001` and open `expense_history_case_001`, a note that cites the client's expense log (`expenses_case_001`, a CSV) by column, row, cell and part of a cell; click a claim to land on what it cites in the table. Then open `expense_rollup_case_001`, a CSV written with citations at every granularity: its header row and line-number column are the writer's own, its cells quote the export's cells, its notes quote and paraphrase parts of the export's notes, and its total rests on the amounts above it in the same table.

Behind `bond view`, `bond.http.server` serves each selected workspace as the read-only JSON API in [ui/API.md](../ui/API.md), and the built UI on the same port. The UI ships inside the package (`src/bond/static/viewer`, built by `npm run build` and not committed), so a wheel must be built after the UI. For UI development, see [ui/README.md](../ui/README.md).

## Claude Desktop extension

```bash
(cd ui && npm run build)                       # the views the bundle carries
uv run python packaging/mcpb/build.py          # writes dist/bond-<version>.mcpb
```

Double-click the `.mcpb` to install it, or have a Team or Enterprise owner allowlist it for the organization. It's a `uv` extension: Claude Desktop installs Python and Bond's dependencies itself, so the person needs neither, and the bundle carries Bond's source and built views.

Its two settings are the Bond home (empty for the default) and workspaces to bind statically (empty for grant mode, under the grants name `desktop`). After changing them, quit and reopen Claude Desktop: it runs two Bond processes, one serving the model's calls and one the view's, and a settings change restarts only the view's, so until a restart the model works with the old settings.

Before relying on it, check these in Claude Desktop with a test workspace (`bond new test && bond add test fixtures/policy_ip112.md`):

1. **App-only tools are kept from the model.** Ask Claude to call `grant_workspace` or `add_sources` itself. It shouldn't see them. If it does, the call still fails, since a person's acts need a secret only the view has; a model that sees app-only tools is a host bug worth reporting.
2. **The model hears about a grant.** Ask Claude to summarize the test case and pick `test` in the picker; Claude should go on in `test` without being told its name.
3. **Adding sources in the chat works**, a big file included (a 5 MB PDF, say): the view sends every file in parts of 96 KiB (`upload_part`), as `view_blob` brings bytes down, so no call should outgrow the host's limit.
4. **A PDF page renders** in the view, from bytes fetched through `view_blob`, with no network access.
5. **The extension installs**, and its settings can be changed after install.
