# Bond viewer HTTP API

A read-only JSON API over explicitly offered Bond workspaces, served by `bond.http.server` (run by `bond view`) and consumed by
the UI in `ui/`. Every route is `GET` under `/api`. This file is the contract
between the two; change both together.

Run it with:

```
uv run python examples/seed.py --home .bond-demo      # a demo Bond home: two cases and their policies
uv run bond view --home .bond-demo --no-browser          # the API (and the built UI) on :8000
cd ui && npm run dev                                       # Vite proxies /api to :8000
```

A workspace's citations can rest on versions in the workspaces it links to (see
[Workspaces and links](../docs/python-api.md#workspaces-and-links)). Those are served through it like its own:
`/api/versions/{v}` and the routes under it answer for them, and each names the `workspace` it is in. A version no
workspace it links to holds now (its link was removed, say) is unreachable: it is never served (`/api/versions/{v}` is
a 404), and where it appears it is marked `"unreachable": true` and only its id, and the locator citing it, are known.

## Shared shapes

**Locator**: exactly the `to_dict()` form from `bond.locators`.

| type | fields | notes |
|---|---|---|
| `text` | `start`, `end` | Unicode **code point** offsets `[start, end)` into the raw file (not UTF-16: the UI must convert) |
| `json` | `pointer`, optional `start`, `end` | RFC 6901 pointer; the range is code points inside a string value. Bond stores JSON locators by position (`path`); this API names values by pointer only, sent and received |
| `csv` | `row`, `column`, optional `start`, `end` | a table's cell (both), row (`row` only) or column (`column` only), 0-indexed; the range is code points inside a cell's decoded value |
| `pdf` | `page`, `bbox` | `page` is 0-indexed; `bbox` is `[x0, y0, x1, y1]` normalized 0-1, top-left origin |
| `version` | | the whole version |

**Ref**: `{"version": str, "locator": Locator}`.

**Format**: `"markdown" | "json" | "csv" | "tsv" | "pdf"`.

**Kind**: `"transcription" | "quote" | "paraphrase" | "unsourced" | "source" | "classification" | "assessment"`.

**VersionSummary**:

```json
{
  "version": "5e29…",            // the version id (a sha256 over its content and citations), the id everywhere
  "workspace": "case-001",       // the workspace it is filed in: this one, or one this one links to
  "content_hash": "ee87…",       // sha256 of its bytes: versions with the same bytes share it
  "name": "fax_case_001.reducto", // the version's home: the first document it was filed in
  "title": "Fax OCR, case 001",  // the home document's title for people, or null: show `name` then
  "documents": ["fax_case_001.reducto"],  // every document whose history holds the version
  "format": "json",
  "created_at": "2026-10-12T21:41:40.034903+00:00",
  "version_index": 1,            // 1-based position in the home document's history, oldest first
  "version_count": 2,            // how many distinct versions the home document has held
  "latest": false                // version_index == version_count: the home document's head
}
```

Versions have no names of their own; documents do. In the home document's history each version counts
once, at its latest entry, so after a revert the reverted-to version is last and `latest`.

**Citation**:

```json
{
  "id": "5d41e09b…",             // sha256 of the citation, unique within its owner
  "owner_version": "…",          // the version this citation belongs to
  "kind": "transcription",
  "claim": Locator,              // in the owner version
  "claim_text": "FAX COVER SHEET" | null,   // null for whole-version claims (e.g. an ingested file's SOURCE)
  "supports": [
    {
      "version": "…", "name": "fax_case_001", "format": "pdf",   // format null if unreachable
      "locator": Locator,        // a JSON locator in an unreachable version is its stored {"path": [...]}
      "text": "…" | null,        // null for pdf regions, whole versions, and unreachable versions
      "unreachable": false       // true: no workspace this one links to holds the version, so it can't be opened
    }
  ],
  "attrs": {"transcriber": "reducto"}
}
```

Errors are `{"detail": str}`: 404 for an unknown version, 400 for a malformed parameter or a locator that is
malformed or invalid in its version (including a citation id the version doesn't hold). Requests are served concurrently, from a thread pool.

## Workspace selection

`GET /api/workspaces` returns `[{"id": str, "name": str, "created_at": str}]` for the workspaces the host offers
(a `bond.workspaces.Workspaces`, read on every request), in default-first order: `name` is the name each is offered
under, `id` Bond's own id for it. Every data route below accepts `?workspace=<name>`; without it the first offered
is used, preserving single-store clients. A name that isn't offered returns 404.
The selection is per request, including blob requests; there is no shared mutable current workspace.

`bond view` offers every workspace in the Bond home (`--home` moves it), or each `--workspace` given (repeatable:
a name in the home, or a directory); with Postgres, each `--name`.
The viewer has no authentication: offering several stores makes all of them readable to its callers.

## Routes

### `GET /api/overview`

Which workspace this is, and how big:
`{"workspace": {"id": str, "name": str, "created_at": str}, "version_count": int, "document_count": int,
"unreachable_count": int, "graph_limit": int}`. `version_count` counts the versions stored in this workspace, and
`unreachable_count` how many versions something here rests on that no workspace it links to holds. A workspace of more than `graph_limit` versions is browsed by document (`/api/documents`,
`/api/neighborhood`), not drawn whole.

### `GET /api/documents?q=…&offset=0&limit=50`

A page of documents, ordered by name, whose names or titles contain `q` (ignoring case; every document if absent).
`limit` is 1 to 500.

```json
{"total": 5001, "offset": 0, "documents": [{"id": "doc_…", "name": "cases/00042/summary", "title": "Summary, case 42" | null, "head": "<version>",
  "format": "markdown", "created_at": "…", "updated_at": "…", "version_count": 1}]}
```

`total` counts every match; `updated_at` is when the document last moved, and `version_count` how many distinct
versions its history holds.

### `GET /api/neighborhood?version=V&depth=2&limit=200`

The versions within `depth` (0 to 5) citation hops of V, either way: through supports (what a version rests on) and
citers in this workspace (what rests on it). It is a `Graph` like `/api/graph`'s, over just those versions, plus where it is centred:

```json
Graph + {"center": "<version>", "depth": 2, "truncated": false}
```

Breadth first from V, nearest first. Every version next to V is included, but each one further out brings at most 25
new ones, so a hub (a policy every summary cites) doesn't fill the graph with its other citers; at most `limit` (1 to
1000) in all. `truncated` says some were left out. Edges join only the versions included.

### `GET /api/graph`

Every version is a node; edges aggregate citations between versions. Versions in linked workspaces that something here
rests on are nodes too, named and filed as their own workspace has them. Unreachable ones are nodes with
`"unreachable": true`, `"workspace": null`, `"format": null`, `"content_hash": null`, `"created_at": null`, no
documents, and their id's first 12 characters as `name`. A workspace of more than `graph_limit` versions (see
`/api/overview`) is refused with a 400.

```json
{
  "nodes": [
    VersionSummary + {
      "citation_count": 153,                 // citations owned by this version
      "self_citation_count": 18,             // of those, how many have a support inside this same version
      "is_source": false,                    // true if a whole-version SOURCE citation covers it (an ingested file)
      "unreachable": false                   // true for an unreachable version (see above)
    }
  ],
  "edges": [
    {
      "id": "<from>-><to>",
      "from": "<owner version>",             // the citing version
      "to": "<support version>",             // the cited version
      "count": 18,                           // citations of `from` with at least one support in `to`
      "kinds": {"transcription": 18}
    }
  ]
}
```

Supports inside the same version are not edges (see `self_citation_count`).

### `GET /api/versions`

`[VersionSummary]`, oldest first.

### `GET /api/versions/{version}`

```json
VersionSummary + {
  "content": "…" | null,          // the whole text for markdown/json; null for pdf
  "siblings": [VersionSummary]    // every version of the home document, oldest first (includes this one)
}
```

### `GET /api/versions/{version}/blob`

The raw bytes with `Content-Type` `application/pdf`, `text/markdown; charset=utf-8` or `application/json`.

### `GET /api/versions/{version}/citations`

`[Citation]` owned by the version, in reading order of their claims.

### `GET /api/versions/{version}/annotations`

The version's claim units, each with the kinds of the citations whose claims overlap it (empty if
uncited), and its gaps: the exact spans of claim units that no citation covers and that hold a word
character (only possible in a version written under a policy that allows gaps). Both are empty for PDFs.

```json
{"units": [{"locator": Locator, "kinds": [Kind], "text": "…" | null}], "gaps": [Locator]}
```

### `GET /api/cited-by?version=V&locator=L`

`[Citation]` (from any version, including V itself) with at least one support in V whose locator overlaps L.
`locator` is URL-encoded Locator JSON and defaults to `{"type":"version"}` (everything citing V).

### `GET /api/trace?version=V&locator=L&max_depth=N`

Provenance from a ref down to its roots, as a DAG (each distinct ref appears once). `locator` as above;
`max_depth` optional (0 or more), bounding each node's shortest distance from the ref. Node ids are
`"<version>:<canonical locator json>"`.

```json
{
  "root": "<node id>",
  "nodes": [{"id": str, "version": str, "name": str, "format": Format | null,
             "locator": Locator, "kinds": [Kind], "text": str | null, "unreachable": bool}],
  "edges": [{"citation_id": str, "kind": Kind, "from": "<node id>", "to": ["<node id>"],
             "claim": Locator}]
}
```

An edge goes from the node whose text the citation claims to the nodes of its supports. A SOURCE or
UNSOURCED edge has `"to": []`.

### `GET /api/search?q=…&limit=50&documents=d1,d2`

```json
[{"ref": Ref, "workspace": str, "documents": [str], "format": Format, "snippet": str, "context": {…}, "kinds": [Kind], "declarations": Declarations}]
```

Finds a literal phrase in every head of this workspace's documents, or only in `documents` (comma-separated). Every match is
returned with its direct declarations; `/api/check-support` says whether a match's support reaches declared
sources. `q` is required and `limit` must be positive.

### `GET /api/declarations?version=V&locator=L`

Direct declarations for any valid selection (whole version if locator is omitted):

```json
{"ref": Ref, "citations": [{"id": str, "owner_version": str, "kind": Kind, "claim": Locator, "supports": [Ref], "attrs": {}}], "undeclared": [Ref]}
```

This is `Declarations` in search results too. Citations retain their full subject spans; gaps are clipped
to the selection and follow format coverage rules. PDF selections use geometric declaration coverage,
not extracted text. JSON refs use display pointers when their version is local, canonical positions otherwise.
This reports declarations, not recursive support or factual judgments.

### `GET /api/check-support?version=V&locator=L&limit=1000&max_depth=N`

`locator` and `max_depth` are optional. `limit` must be positive and `max_depth` non-negative.

```json
{
  "ref": Ref,
  "status": SupportStatus,
  "regions": [{"ref": Ref, "status": SupportStatus}],
  "issues": [{"ref": Ref, "reason": SupportReason, "path": [Ref]}]
}
```

`SupportStatus` values: `supported`, `partially_supported`, `unsupported`, `unknown`, `not_applicable`.
Partial means different selected regions pass and fail; it does not assign fractions of a citation's
supporting evidence to particular words. Unknown takes precedence when any selected region is unresolved;
other region outcomes remain visible. Not applicable means only exempt content such as punctuation or nulls,
not a semantic finding of "no factual claims." PDF regions are assessed geometrically and never pass by
having no extracted text.

All relevant declarations and all their dependencies must pass. Source declarations are terminal; unsourced
or undeclared regions fail. Missing versions and exhausted budgets are unknown.
`limit` counts distinct references, including the root; `max_depth=0` inspects direct declarations.
Shared ancestry uses shortest distances. Supporting spans are inspected as cited, without inferred alignment
to narrower claim text. JSON display refs fall back to canonical positions for missing or invalid ancestors.

`SupportReason` values: `undeclared`, `unsourced`, `missing_version`, `limit`, `depth_limit`, `cycle`,
`invalid_reference`, `no_required_content` (a cited support contains only exempt content).
Each issue includes one explanatory path to the affected span; neither a particular path nor an exact
region subdivision is guaranteed. Use the trace endpoint to explore sources and citation edges. This query
checks neither semantic entailment nor source authority and does not replace the integrity checks in `verify()`.

Reports are recomputed on demand and never stored as version properties. Missing dependencies arriving in a
workspace, or a different query budget, can change the outcome for the same immutable version. Bond's status
enum has reserved structural meanings; application-owned enums and assessment functions can express other
judgments without redefining those meanings.

### `GET /api/text?version=V&locator=L`

`{"text": str | null}`: the text a ref addresses, for popovers.
