# Concepts

Bond stores bytes, citations, and filing. Everything else, including the structure of each format, is computed on demand.

- **Version.** One exact file together with its citations, identified by a hash of its bytes, format and citations. Immutable and nameless: the unit of evidence, and the only thing a citation can point at. Every write, ingested or written, produces one.
- **Document.** A name, like a file path, with a stable id and an append-only history of the versions it pointed at; the last is its head. Documents are filing, never provenance. One version can be filed in many documents, and one document can hold the same version more than once (a revert).
- **Citation.** A provenance record: this part of a version (its subject span, stored as `claim`) rests on these parts of other versions (its supports), in this way (its kind); or declares a source; or declares no supporting evidence.
- **Locator.** The address of a span inside one version, in that version's format: a character range, a JSON value, a table cell, a region of a PDF page. A locator always travels with its version as a `Ref(version, locator)`.
- **Workspace.** A world of documents and their versions, such as one case. Writes cite versions in it, or in the workspaces it links to, where they are: a case links to a shared policy library and cites it with nothing copied. Who may open which workspace, and which links to which, are the host's access decisions.

A **source** is a property of a region, not of a document: an ingested file is a source throughout, and a written document can have source regions next to cited ones.

A **claim unit** is a block whose words must be accounted for (a Markdown paragraph, heading, list item, table row or code block; a JSON leaf value; a non-empty table cell). Claim units are computed from the content on demand and never stored. Coverage is checked word by word inside them, so citing one phrase doesn't cover the rest of a paragraph.

The word *claim* in a citation names its subject span; Bond does not detect factual assertions.

## Citation kinds

| Kind | Meaning | Checked |
|---|---|---|
| `transcription` | Text read from a region of a page (of a PDF, today's only format with no text of its own) | One region support; `attrs.transcriber` required, a producer id |
| `quote` | The supporting text, verbatim | One text/JSON support, and the text must match (forgiving whitespace, curly quotes, line-break hyphens and ligatures) |
| `paraphrase` | The supporting text in other words | One or more text/JSON supports; faithfulness is left to a judging layer |
| `unsourced` | No supporting evidence declared, e.g. a heading, id, or vendor metadata | No supports; use an explicit text/value locator rather than a whole-version locator |
| `source` | A declared origin of evidence, over a whole ingested file or part of a document | No supports; authority is not evaluated |
| `classification` | A label a producer assigned to a region of a page | One region support; `attrs.classifier` required, a producer id |
| `assessment` | A producer's judgment of the text it rests on, e.g. a transcriber's confidence in the text it read | One or more text/JSON supports; `attrs.assessor` required, a producer id |

Each kind is checked as strongly as it honestly can be, and a kind's meaning never changes: a changed meaning would be a new kind. Evidence from a source with no text of its own (a PDF) enters a document only through `transcription` and `classification`, so every chain above a transcription stays traceable through readable text down to a page region.

Vendor values are cited where they sit, never copied into `attrs` or converted onto Bond's own scale. Attrs never hold free text: the only ones are the producers of transcriptions, classifications and assessments, each named by an id (a lowercase letter, then lowercase letters, digits and hyphens). Which producers are known is the policy's business (see [Policies](#policies)).

`unsourced` is always explicit: Bond never infers it from missing citations. In a trace it is an edge with no supports. An undeclared gap remains a gap. `source` declares an origin at any size: an ingested file, or a region such as a reviewer's own rationale. Neither declaration establishes factual correctness. The core enforces that every word is accounted for, not who may account for it how: the agent layer refuses `source`, so agents must cite or mark text `unsourced`.

A support can point into the document being written with `SelfRef(locator)`, such as the text another of its citations claims, so a summary's conclusion can rest on its own findings. Such supports must not form a loop.

Version ids are typed `VersionId`, distinct from the plain `str` hash of a blob's bytes. A citation being written rests on `Support`, a `Ref(version, locator)` or a `SelfRef(locator)`; everything Bond stores, returns and reads by is a `Ref`, so a type checker rejects a `SelfRef` (or a content hash) where a stored version is expected.

## Locators

| Format | Locator | Example |
|---|---|---|
| Markdown | Code-point range `[start, end)` | `{"type": "text", "start": 412, "end": 465}` |
| JSON | A value by position: the index of each step from the root, optionally with a range in a string | `{"type": "json", "path": [0, 12, 0], "start": 0, "end": 31}`, in Python `JsonLocator((0, 12, 0), (0, 31))` |
| CSV, TSV | A cell, a whole row or a whole column (from 0); a cell optionally with a range in its value | `{"type": "csv", "row": 3, "column": 4}`, `{"type": "csv", "row": 3}`, in Python `CsvLocator(3, 4)`, `CsvLocator(row=3)`, `CsvLocator(3, 4, (0, 5))` |
| PDF | Page (from 0) and bbox, normalized 0–1, top-left origin | `{"type": "pdf", "page": 1, "bbox": [0.12, 0.41, 0.64, 0.43]}` |
| Any | The whole version | `{"type": "version"}` |

Each format has exactly one canonical locator, which is what citations store and what containment compares. Anything else (line numbers, heading paths) is display context returned alongside, never stored.

- **Offsets are Unicode code points** over the UTF-8-decoded content exactly as stored. Bond never normalizes line endings, byte-order marks or Unicode, since that would change the hash; text that isn't valid UTF-8 is refused.
- **JSON values are addressed by position, not by key.** A path steps through an object by its members' order in the version's bytes and through an array by index, so `[0, 12, 0]` in `{"blocks": [..., {"text": ...}]}` is `/blocks/12/text`. Keys are unique and versions never change, so a path names one value exactly and translates losslessly to and from a JSON Pointer in its version: `details.locate_pointer(bond, version, "/blocks/12/text")` and `details.pointer(bond, ref)`. Stored locators carry no keys, which are often data; handles, the viewer and messages name values by pointer. JSON with duplicate keys, `NaN` or `Infinity`, or nested more than 200 deep is refused, and a range is valid only inside a string.
- **A table's cells hold their decoded values** (a quoted field's quotes are syntax), and every row is data: the first row is never guessed to be a header. A row contains its cells, a column its cells, and a row and a column overlap. A leading byte-order mark is skipped when cells are read; the stored bytes keep it.
- **PDF coordinates are normalized by whoever cites them** (vendors differ in units and origin), so each producer's `Transcription` converts before writing. A region is valid when its page exists and `0 ≤ x0 < x1 ≤ 1`, `0 ≤ y0 < y1 ≤ 1`.
- **Every locator addresses content.** Nothing names a citation, so every provenance edge runs between spans: a transcriber's confidence rests on the text it read.
- **Containment and overlap** are computed within one version: ranges numerically, JSON paths by prefix, regions geometrically; the whole version contains everything in it. Locators of different versions are never compared.

## Policies

**Integrity** is always checked and no policy can waive it: content is well-formed (UTF-8; valid JSON with no duplicate keys), every locator is valid in its version and every supporting version exists, each citation passes its kind's validator, and supports within the document never loop (`circular_support`).

**A policy** decides the rest, and it is plain data: `Policy(gaps=False, kinds=frozenset(CitationKind), producers=PRODUCERS)`.

| Policy | Requires |
|---|---|
| `STRICT` (the default) | Every word is accounted for. All text is claim text: in Markdown, paragraphs, list items, headings, table rows (headers included), code and HTML blocks, front matter and any other line; in JSON, every non-null leaf. Only syntax and punctuation need no citation. Citing one phrase of a paragraph doesn't cover the rest of it. |
| `ALLOW_GAPS` | Nothing more: uncovered spans are stored as gaps, for drafts and migrations, listed exactly by `declarations(...).undeclared` and never turned into `unsourced`. |
| `Policy(kinds=...)` | Only citations of these kinds, e.g. `bond.authoring.AGENT_POLICY`, which allows `quote`, `paraphrase` and `unsourced`. |
| `Policy(producers=...)` | Only these producers named in `attrs` (`unknown_producer` otherwise). `bond.policies.PRODUCERS`, the default, holds `pypdf` and `reducto`, the producers Bond reads; a deployment recording another vendor's output passes `Policy(producers=PRODUCERS \| {"acme-ocr"})` when it writes and when it verifies. |

The policy is passed with each call and never stored: a version is a record of facts, and whether it meets a policy is a judgment different consumers can make differently. Bond checks that content is well-formed and its provenance structurally valid, not that it matches a schema: a caller that needs JSON of a given shape validates it before writing.

A version is its bytes, format and citations together: the same bytes with different citations are another version (sharing one blob), and the same write again returns the same version. Errors come back together as `Issue`s, each with a code, a message, and where it applies: the citation's index (and the support's, for a problem with one), the locator, the citations in a loop, and a hint. Nothing is written on failure.

## Identity and verify

A citation's id is the sha256 of the canonical JSON of its kind, claim, supports (as a set) and attrs. A version's id is the sha256 of the canonical JSON of its bytes' hash, its format and the sorted ids of its citations, so neither depends on the order citations are listed in, and a version's citations can never change. Supports into the version itself are hashed as `"self"` and stored under the new id.

An id hashes the ids it cites, so ids form a Merkle DAG: one expected id (recorded with an artifact, say) anchors identity checks throughout its available provenance chain. `verify` recomputes the blob hashes and ids from a version down, and runs the write checks again on each version as it was written, under the policy supplied to verification now. Hashes show nothing changed since it was stored; the checks show it met the rules, whatever code path stored it. The historical writing policy is not stored. Document names, histories and timestamps are filing, not provenance, and aren't covered: the same evidence hashes the same wherever it is filed.

## Trace

A node `(V, L)` has an edge for every citation of `V` whose claim overlaps `L`, and that citation's supports are the next nodes. So a support pointing at a paragraph of a summary picks up every citation inside it, and each of those the text it cites, down to a page region. A chain ends at a `source` edge (declared origin), an `unsourced` edge (no declared support), a node with no edges (uncited), or an unreachable version: one held by no workspace the traced one links to now (a link was removed, say).

The graph is acyclic: supports into other versions must exist when a version is written, and supports inside one are checked for loops. `cited_by` walks one hop the other way: what to look at again when evidence turns out to be wrong, like an OCR misreading on page 2.

## Support

```python
from bond import SupportStatus

assessment = bond.check_support(hit.ref, limit=1000)
if assessment.status is SupportStatus.SUPPORTED:
    pass  # every selected declaration's support chain reaches declared sources
```

| `SupportStatus` | Meaning |
|---|---|
| `SUPPORTED` | All selected content covered by the format rules has complete support chains to declared sources. |
| `PARTIALLY_SUPPORTED` | Some selected regions pass and some fail, with no unresolved region. |
| `UNSUPPORTED` | All selected regions fail the support check. |
| `UNKNOWN` | A missing dependency or traversal budget leaves at least one selected region unresolved. Known region outcomes remain in the report. |
| `NOT_APPLICABLE` | The selection contains only content exempt from the format's coverage rules. |

This is structural support, not semantic entailment, source authority, or an integrity recheck. A contradictory paraphrase can still pass. `verify()` separately checks hashes and citation validation. A heading marked unsourced satisfies `STRICT`'s declaration requirement but fails recursive support checking.

Format rules count words and numbers in Markdown (including headings and labels), JSON scalar values, and table cells; syntax, whitespace, punctuation-only strings, nulls, and empty values are exempt. PDF inspection uses page geometry: a selected region needs declaration coverage. The absence of extracted text never makes a PDF region supported or not applicable.

Every supporting reference of a relevant derived citation must pass. An overlapping source declaration does not hide an unsourced declaration or a failing dependency. Regions are split at citation boundaries, not by inferred semantic claims. If one citation covers a paragraph and cites partly unsupported evidence, that citation fails for its whole selected subject. Supporting spans are inspected as cited, even if they are larger than the selected subject.

`limit` counts distinct references including the root; `max_depth=0` inspects only direct declarations. Shared ancestry is read once per query. Issues identify affected spans with a `SupportReason` and one explanatory path of references. Reasons include undeclared content, unsourced declarations, missing versions, depth/reference limits, invalid references, cycles, and a support containing no content under the coverage rules.

Support is a query result, not a stored property of a version. The same immutable version can be `UNKNOWN` in one workspace and `SUPPORTED` in another, or change outcome when a link to what it rests on is made or removed. Changing the query budget can change its outcome too. Any future cross-query cache must account for workspace evidence availability, the queried reference, assessment rules, and query parameters; a version ID alone is not a valid cache key.

Bond reserves `SupportStatus` for its documented structural check. Application judgments such as accepted, needs review, or authoritative belong in application-owned enums and functions built over `declarations()`, `trace()`, and/or `check_support()`.

## Invariants

What everything relies on, and what any change has to keep:

1. Versions are immutable and identified by a hash of their bytes, format and citations, with cited versions by id. A version's citations are set once, when it is written.
2. A version's id is global: computed from its content and citations alone, never from where it is stored, so the same version has the same id in every workspace.
3. One canonical locator per format, always travelling with its version. Text offsets are code points over unmodified UTF-8; JSON values are addressed by position, never by key.
4. Bond stores bytes, citations and filing; all structure is computed on demand by parsers.
5. Provenance is one fixed citation structure plus an enum of kinds, each with a validator that checks as much as it honestly can.
6. Coverage is enforced word by word by default. Absence of supporting evidence is declared with `unsourced` and an origin of evidence with `source`; Bond never infers either. Gaps (under `ALLOW_GAPS`) stay gaps.
7. A PDF's text is a derived document whose citations transcribe it (a transcription), never part of ingestion, and a producer's output reaches it only from whatever ran the producer.
8. Provenance composes by overlap, every edge runs between spans of versions, and edges never loop.
9. Evidence and filing are separate. Citations point at versions, never documents, and a write advances a document only from the head it started from.
10. Records hold no content: every stored field of a version and its citations is a hash, an enum, a number, a positional locator, a producer id or Bond's workspace id.
11. The workspace is the unit of access, isolation and deletion. Citations rest on versions in the workspace or in the workspaces it links to, which a deployment decides; a workspace can read what those link to in turn, links never give write access, and a deployment's links never loop, so a shared workspace never rests on the cases that cite it. A support no reachable workspace holds (its link was removed) is a valid state that `verify` reports as `unreachable` rather than fails.
