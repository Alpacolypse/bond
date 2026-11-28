# Bond

Bond is a virtual file system that makes it easy for AI agents to write documents with provenance. It gives agents search tools and a unified citation mechanism that let them cite as precisely as they need, across all document types, with minimal token spend. Human reviewers can trace any claim back to its source through a provenance graph.

> **Research preview: source available, not licensed for use.** Bond is under active development and testing. The source is published to be read, and run locally to evaluate it; no other use, copying, modification or distribution is licensed (see [LICENSE](LICENSE)). Don't use it with real personal or health data. There is no release, and the storage schema changes without migrations.

## What it looks like

For e.g, an agent writes Markdown with inline citations. The handles come from Bond's `search`:

```
<bond:cite kind="quote" from="d246983a71c5/result/chunks/1/blocks/4/content@12-63"><bond:anchor id="drip">Active drip</bond:anchor> from the bathroom ceiling light fixture</bond:cite>.

<bond:cite kind="paraphrase" from="#drip">The leak is still active.</bond:cite>
```

Bond turns the tags into citations, checks that every word is covered and every quote matches its source, and stores the version. Tracing "still active" then walks back to the source:

```
"still active"                            notes, line 3
  └─ paraphrase → "Active drip"            notes, line 1
       └─ quote → "Active drip from the bathroom ceiling light fixture"   fax_case_001.reducto
            └─ transcription (reducto) → page 2, region 0.11,0.29 – 0.60,0.35   fax_case_001.pdf
                 └─ source
```

## Quick start

Bond needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). Install it from a checkout:

```bash
git clone https://github.com/Alpacolypse/bond.git && cd bond
uv sync --extra mcp
```

### Give an agent a case

```bash
uv run bond new case-001                                   # a workspace in your Bond home
uv run bond add case-001 fixtures/policy_ip112.md fixtures/expenses_case_001.csv
claude mcp add bond -- uv run --directory "$PWD" --extra mcp bond mcp --workspace case-001
```

The agent gets the authoring tools (`documents`, `read`, `search`, `trace`, `write` and `edit`) and instructions on how to cite. It can reach only the workspaces its server was given, and it can't add sources: only you can, with `bond add`. `bond mcp` speaks MCP over stdio, so any MCP client works the same way. Before you give an agent a shell, read [Running Bond locally](docs/local.md#only-people-add-sources).

### Use it from Python

```python
from bond import Bond, Citation, CitationKind, Ref, TextLocator, VersionLocator

bond = Bond.local("./cases/case-001")                      # a workspace, created if new
bond.ingest("policy", "fixtures/policy_ip112.md")          # a source, filed as "policy"
[hit] = bond.search("at least 6 weeks of written notice")

note = "Policy requires at least 6 weeks of written notice."
result = bond.write_document("note", note, [Citation(CitationKind.PARAPHRASE, TextLocator(0, len(note)), [hit.ref])])

bond.check_support(Ref(result.version, VersionLocator())).status   # SupportStatus.SUPPORTED
bond.write_document("other", "An uncited claim.", [])              # raises WriteError: no citation covers 'An uncited claim.'
```

### See it

```bash
(cd ui && npm install && npm run build)                    # build the viewer UI once (Node 22)
uv run python examples/seed.py --home .bond-demo           # two synthetic cases and a policies workspace
uv run bond view --home .bond-demo
```

## What Bond guarantees, and what it doesn't

Bond checks structure, not meaning. It validates that every reference points at real content, that every required word has a citation, that quotes match their sources, and that stored bytes still match their ids. It does **not** judge whether a paraphrase follows from what it cites or whether a source is authoritative. A successful write is not a factual endorsement; deciding which sources and reviews are enough is the application's job.

| Term | Contract |
|---|---|
| `source` | A declared origin of evidence. Authority is not evaluated. |
| `unsourced` | No supporting evidence is declared. This can describe a heading or an unsupported assertion; it says nothing about whether the text is true. |
| `quote` | The text matches its support under Bond's normalization. The support may itself be unsourced. |
| `paraphrase` | The writer declares that the cited material supports the claim. Semantic support is not evaluated. |
| `STRICT` (default policy) | Every required word has a declaration, including explicit `unsourced` ones. |
| `verify(version)` | Recomputes ids and re-runs the write checks down the chain, and reports missing dependencies explicitly. |

[tests/test_provenance.py](tests/test_provenance.py) exercises these boundaries, including structurally valid contradictory paraphrases and entirely unsourced drafts.

## Concepts

- **Version**: one exact file plus its citations, immutable and named by a hash of both. The only thing a citation can point at.
- **Document**: a name, like a file path, with an append-only history of versions. Documents are filing, never provenance.
- **Citation**: this span of a version rests on these spans of other versions, in this way (`quote`, `paraphrase`, `transcription`, …); or it declares a `source`; or it declares itself `unsourced`.
- **Locator**: the address of a span in its version's format: a character range, a JSON value, a table cell, or a region of a PDF page.
- **Workspace**: a world of documents, such as one case. Writes cite what is in it, or in the workspaces it links to (a shared policy library, say), where they are.

[docs/concepts.md](docs/concepts.md) defines each precisely, with citation kinds, locators, policies, identity, trace and support.

## How it fits together

Bond is a **core**, two **APIs** over it, and **deployments** that decide where workspaces live and who can reach them.

- **`bond`**, the core, keeps provenance. Programs call `Bond` directly.
- **`bond.mcp`** is the API for agents: authoring tools over MCP, plus an in-chat view of the document. **`bond.http`** is the read-only JSON API behind the viewer.
- **`bond.local`** is one deployment: one person's computer, with workspaces in a Bond home and the person granting access. Another deployment implements the `Workspaces` protocol over its own stores and access control and leaves `bond.local` out.

Storage is pluggable: any store that can create a key once (SQLite, Postgres or memory ship with Bond), and a local directory or S3 for blobs.

## Documentation

| Doc | Covers |
|---|---|
| [Concepts](docs/concepts.md) | Versions, citations, kinds, locators, policies, identity, trace, support, invariants |
| [Python API](docs/python-api.md) | `Bond`'s methods, workspaces and links, formats, transcriptions of PDFs |
| [Agents](docs/agents.md) | The authoring tools, inline citation markup, handles, the MCP server and its app view |
| [Running Bond locally](docs/local.md) | The Bond home, the `bond` CLI, grants, the viewer, the Claude Desktop extension |
| [Storage and data handling](docs/storage.md) | Backends, schema, concurrency, and exactly where document content can end up |
| [Architecture](docs/architecture.md) | Layering rules and the source layout, for contributors |
| [Viewer API](ui/API.md), [UI development](ui/README.md) | The viewer's JSON API and its React front end |

## Development

```bash
uv sync                                                    # all dev dependencies
uv run pytest                                              # the suite, on SQLite
uv run ruff check && uv run ty check                       # lint and types
uv run python examples/case_001.py                         # ingest a fax, write a cited summary, trace it to the page
(cd ui && npm ci && npm test && npm run build)             # the viewer UI
```

Set `BOND_TEST_POSTGRES` to a Postgres DSN to run the suite against Postgres too. [CI](.github/workflows/ci.yml) runs all of the above on every push and pull request, on Linux and Windows, and also builds the wheel and serves a workspace from it.

All fixture data in [fixtures/](fixtures/) is synthetic; `fixtures/generate.py` regenerates it.

## License

Copyright © 2026 Anuj Iravane. All rights reserved. Bond is a research preview, published for reading and evaluation only; see [LICENSE](LICENSE). It isn't accepting contributions yet.
