# Python API

A `Bond` is one workspace. See [Concepts](concepts.md) for what versions, citations and locators are.

```python
from bond import Bond, Citation, CitationKind, Ref, TextLocator, VersionLocator

bond = Bond.local("./cases/case-001")  # a workspace; or Bond(store, blobs, name) on your database and bucket: see storage.md

policy = bond.ingest("policy", "policy.md", title="Policy IP-112")   # a source throughout, filed as "policy"
[hit] = bond.search("at least 6 weeks of written notice")     # discovery and citing in one

content = "Policy requires at least 6 weeks of written notice."
cite = [Citation(CitationKind.PARAPHRASE, claim=TextLocator(0, len(content)), supports=[hit.ref])]
bond.check_write("note", content, cite)                      # every issue a write would have, filing included
result = bond.write_document("note", content, cite)

bond.citations(result.version)                                       # one hop
bond.trace(Ref(result.version, TextLocator(0, len(content))))        # all the way down
bond.check_support(Ref(result.version, VersionLocator())).status     # does it all rest on declared sources?

# The next version of a document names the head it started from, so concurrent writers can't overwrite each other.
edited = "Policy requires six weeks of written notice."
bond.write_document("note", edited, [Citation(CitationKind.PARAPHRASE, TextLocator(0, len(edited)), [hit.ref])],
                    base=result.version)
```

## Evidence

| Function | Does |
|---|---|
| `ingest(document, source, *, format=None, base=None, title=None, origin=None)` | Store a file that is a source throughout, from its bytes or a path, and file it: shorthand for writing it with one whole-version `source` citation. The format comes from a path's suffix or is sniffed from the bytes if not given. The same bytes are always the same version, wherever they are filed. `origin`, where the file came from (a path or URL), is recorded on the history entry that files it: filing, not identity, so it changes no id and `verify` never reads it. Ingesting the same bytes again where they already are adds no entry, so records no new origin. |
| `write_document(document, content, citations, *, format=None, base=None, policy=STRICT, title=None)` | Write a cited version (bytes, or text stored as UTF-8) and file it in `document`, or raise `WriteError` with every issue at once. The format is sniffed if not given (Markdown or JSON; name CSV and TSV). |
| `check_write(document, content, citations, *, format=None, base=None, policy=STRICT, title=None)` | Every issue `write_document` would refuse the same write for, filing included, as a list (empty if it would succeed); nothing is stored. For callers that expect refusals, like an agent's tools. |
| `search(phrase, *, documents=None, within=None, limit=50)` | Find a phrase in documents' current versions, or inside one reference (not both), and pin each match to an exact locator. Matching is case-insensitive and forgives whitespace, quote characters, ligatures and hyphenated line breaks. Each hit carries its direct `declarations` and overlapping `kinds`. |
| `declarations(ref)` | Direct declarations, one hop: `ref`, overlapping `citations` with their original subject spans and supports, and exact `undeclared` gaps (the uncovered words of a version written under `ALLOW_GAPS`, for one). No ancestry judgment. |
| `check_support(ref, *, max_depth=None, limit=1000)` | Assess all selected declarations' ancestry to declared sources. Returns a `SupportReport`: enum `status`, selected `regions` with individual outcomes, `issues` with reasons and explanatory paths. Computed on demand, never stored. See [Support](concepts.md#support). |
| `citations(version)` | The citations a version was written with. |
| `trace(ref, max_depth=None, limit=None)` | Follow provenance from a reference down to its sources, as a DAG: each distinct reference is one node, and each edge is a citation. `limit` keeps the nearest nodes, and `complete` says whether limits omitted any traversal. Versions outside the workspace are reported separately, as its `frontier`. |
| `cited_by(ref)` | The reverse of one hop: citations, from any version, with a support overlapping a reference. |
| `freshness(ref, document=None, as_of=None)` | Whether a reference's version is still the head of each document that holds it (or only `document`), computed now, never stored. For each that moved past it: its head, when it moved (`superseded_at`), and for a span, whether its text is in the head (`unchanged`, `moved`, `gone` or `unknown`, with where). `as_of` also asks whether it was the head then; entry times are approximate (taken just before each entry is created, on the writer's clock), so an `as_of` near another write can't order the two. `superseded` is true when no document holds it as its head any more. A suggestion of where to look: Bond never moves a citation. |
| `verify(version, policy=STRICT)` | Check a version and every version beneath it: recompute their hashes and ids, and run the write checks again on each as it was written. Returns the issues found, each naming its version (none if intact). |
| `blob(version)`, `text(ref)` | A version's raw bytes; the text a reference addresses (`Ref(v, VersionLocator())` for all of it). A version whose stored bytes are missing or malformed raises `CorruptVersion`; `verify` reports it instead. |

Lower-level reads for tools built on Bond (version records, the documents a version is filed in, where a span is and the text around it, claim units, JSON Pointers) are functions in `bond.details`.

A write (or plan) that cites a superseded version still goes through, with a `cites_superseded` warning in `WriteResult.warnings` (and `WritePlan.warnings`) naming the citation and support. `Policy(refuse_superseded=True)` refuses it instead. Search hits and traces say the same where they find older versions: `Hit.newer` and `Trace.newer` map each document that moved past a version to its head.

## Filing

| Function | Does |
|---|---|
| `document(name)`, `documents()` | A document (`id`, `name`, `head`, `title`), or all of them. `document` raises `UnknownDocument` for a name that isn't filed; `bond.filing.document(name)` returns `None` instead, to ask whether there is one. |
| `history(name)` | The versions a document pointed at, oldest first, each with the `base` it was written from. A revert is a new entry. |

A write files its version in a document by name, which must not be blank (`invalid_name`, as for a blank title). Leave `base` out to create the document; to write its next version, pass its head as `base`.

- A write from an old head fails with `stale_base`, and nothing is stored.
- A write without a base to an existing document fails with `document_exists`, and a base for a missing document with `unknown_document`.
- Writing the version a document already points at is a no-op (`advanced` is false), so retries are safe.
- Reverting is writing the old content and citations again: they are the same version.
- `title` labels a document for people to read it by; like the name, it changes no id.

## Workspaces and imports

Everything above happens inside one workspace: search finds its documents, and a write can only cite versions in it. Material from elsewhere comes in by import:

```python
policies = Bond.local("./policies")      # shared: the office's intake policies
case = Bond.local("./cases/case-001")    # one case; its name is its directory's

case.import_document(policies, "criteria/IP-112")          # its id and citations preserved; what it rests on stays outside
case.frontier()                                            # versions it rests on that stayed in policies
case.import_version(policies, case.frontier()[0])          # bring one in later; nothing else changes
```

| Function | Does |
|---|---|
| `import_document(source, document, *, name=None, base=None, depth=0, limit=None)` | Copy a document's head with its identity, bytes, and citations preserved, and file it under `name` (the same, by default), from `base` as in `write_document`. `depth` counts dependency hops (`None` for all); `limit` bounds distinct versions visited. Dependencies not copied remain explicit outside-workspace references. History records the original workspace and version. Every copied artifact is checked against its identity. |
| `import_version(source, version, *, depth=0, limit=None)` | Copy a version an earlier import left outside, and what it rests on as `import_document` does, without filing it. `advanced` says whether anything was copied. |
| `frontier()` | Versions something here rests on that aren't here. |
| `workspace` | This workspace's `id` and `name`. |

A support into a version outside the workspace is a valid state, and only an import makes one: a write still refuses any support that isn't here. `trace` stops there and lists such versions in `Trace.frontier`. `verify` reports each once as `outside_workspace` and checks everything else, skipping only what needs the missing text (a quote's match), so an empty result still means everything beneath was checked.

Importing preserves a policy's identity: every case gets the same version, so a host that can open many cases can ask each one `cited_by` the same span of it, which is how to find every determination resting on a paragraph that changed. Bond doesn't run that query across workspaces itself; the host does, over the workspaces it may open. A workspace never learns who imported from it.

Imports never turn derived or unsourced content into a source. A host that has reviewed an external artifact and wants to treat it as a source here ingests its bytes: `case.ingest("accepted-criteria", policies.blob(version))`. With a depth or version budget, inspect the frontier rather than treating a partial import as complete. An import can leave copied evidence in the destination if a later filing conflict or dependency error occurs; it does not rewrite existing evidence.

From the command line: `bond import --from ./policies --into ./cases/case-001 policy/IP-112` (`--as` files it under another name; `--depth`, a number of hops or `all`, and `--limit` bound dependencies; identity and citations are always preserved).

[examples/workspaces.py](../examples/workspaces.py) builds a policies workspace and a workspace per case.

## Plans: deciding a write apart from doing it

```python
plan = bond.plan_write("note", content, cite)   # checked now; stores nothing; WriteError as a write would raise
result = bond.commit(plan)                       # stores it and files it; stale_base if the document moved since
```

| Method | What it does |
|---|---|
| `plan_write(...)`, `plan_ingest(...)`, `plan_import(...)`, `plan_import_version(...)` | Take what `write_document`, `ingest`, `import_document` and `import_version` take, and return a `WritePlan` (`bond.plans`): the versions to store and where to file one. Checked against the workspace as it is; nothing is stored. Those four methods are `commit` of these. |
| `commit(plan)` | Store a plan's versions (blobs, then records) and file one (its history entry, then its title). Refused with `stale_base` if the document moved since the plan was made, having filed nothing. Committing again is harmless. |
| `stage(plan)` | Put a plan's bytes in the blobs, and return it as JSON (bytes named by hash), to keep where a host keeps its steps. |
| `load_plan(text)` | A staged plan, with its bytes read back. It is checked again when committed, so a plan from outside can't store what a write couldn't. |

A plan stays good: versions are never removed, so what it checked stays true, except the document it files in, which `commit` checks again atomically. So a host can plan in one step (an agent's tool) and commit in another (the loop that runs it), or commit again after a crash.

## Formats

Everything Bond knows about a format is one entry in the format registry (`formats.py`): its media type, file suffixes, how to recognise its bytes, its parser, and whether it has text of its own to read, search and quote (a PDF doesn't). The core, the citation kinds, the server and the authoring tools ask the registry, the parsed document or what a locator addresses; none of them branches on a particular format. Adding a format is a `Format` value, a parser and a registry entry, plus an authoring codec to write it.

| Format | Claim units | Where a match is |
|---|---|---|
| Markdown | every block of text: paragraphs, list items, headings, table rows, code and HTML blocks, front matter, any other line | line number and heading path |
| JSON | non-null leaf values | its JSON path |
| CSV, TSV | non-empty cells with a word character | row and column, from 1 |
| PDF | none: a PDF holds no text claims; its text is a document that transcribes it | its page |

Bytes are sniffed as JSON only when the top level is an object or array, so a note that is just `42` stays Markdown. Tables are never sniffed: CSV and TSV are named by suffix (`.csv`; `.tsv`, `.tab`) or by the writer, and the delimiter is fixed by the format.

Search matches literal phrases case-insensitively after folding whitespace, quote characters, ligatures and hyphenated line breaks, keeping a map back to the raw offsets, so a phrase copied from OCR text finds its source and the locator addresses the stored bytes. JSON is searched value by value, so escapes never shift locators. Every match is returned; repeated text comes back once per occurrence.

## Transcriptions

Some formats hold no text of their own: a PDF is a picture of pages (`has_text` is false in its format's entry), and ingesting one extracts none. Its text is a **transcription**: a derived document holding one producer's output for the source, stored byte-for-byte in the producer's output format and filed as `<document>.<producer>`, whose citations say what each part of the output is. Text read from a region of a page is a `transcription` of that region, a label the producer gave a region a `classification`, its judgment of text it read an `assessment`, and the rest (job metadata, layout, page numbers) `unsourced`. Search finds the text, an agent cites it, and a trace follows it down to the region it was read from.

Nothing in `bond.transcription` knows a format or a producer. A producer is a `Transcription`: its id, the source formats it `reads`, the format its `output` is stored in, and how that output is cited. `record` checks that the source has no text of its own and is a format the producer reads, and the write checks that every region cited is valid in the source. So a new kind of source (an image, a TIFF fax) is a format in the core with a region locator, and a new producer is one `Transcription`; neither touches the other.

Bond keeps two jobs apart:

- **Reading a producer's output into citations is Bond's.** Each producer's `Transcription` is a module in `bond.importers`, listed in `TRANSCRIPTIONS` by its id. It is plain code over bytes, so the core never sees a vendor's schema and several transcriptions of one source can coexist.
- **Running a producer is a deployment's**: credentials, network, cost and sending a document to a vendor are its decisions. Bond runs only a `Transcriber`, a `Transcription` that needs nothing but the source's bytes, such as a PDF's own text layer; `transcribers_for(format)` lists those that read a format. A deployment that runs a vendor hands what it returned to `record`.

| Producer | Module | Reads | Runs in Bond | Cites |
|---|---|---|---|---|
| `pypdf` | `bond.importers.text_layer` (`TEXT_LAYER`) | PDF | yes: it needs only the PDF | the text a PDF made from a document carries, in Bond's own JSON. Each page's text is a transcription of the whole page: pypdf gives no box per line, so the region is the page, which certainly holds it. A scan or fax has no text layer and is refused, with nothing written. |
| `reducto` | `bond.importers.reducto` (`REDUCTO`) | PDF | no: record its parse | Reducto's JSON parse: each block's `content` is a transcription of the block's box, `type` a classification of it, and `confidence` an assessment resting on the content |

```python
from bond.importers.reducto import REDUCTO
from bond.importers.text_layer import TEXT_LAYER
from bond.transcription import record, transcribe

transcribe(bond, "fax.pdf", TEXT_LAYER)                   # Bond runs it: "fax.pdf.pypdf"
record(bond, "fax.pdf", REDUCTO, parse_bytes)             # a parse Reducto returned to you: "fax.pdf.reducto"
```

| Function | Does |
|---|---|
| `transcribe(bond, document, transcriber)` | Run a `Transcriber` (a `Transcription` with `transcribe(source, format) -> bytes`) on the source `document` holds, and `record` its output. |
| `record(bond, document, transcription, output)` | Write a producer's output for the source `document` now holds as `<document>.<producer>`, in the producer's output format, from that document's head if it exists: recording after a new version of the source, or a new run, writes its next version, and the same output for the same source changes nothing. The write allows the producer's id; `PRODUCERS` holds `pypdf` and `reducto`, so others verify with `Policy(producers=PRODUCERS \| {...})`. Raises `TranscriptionError` if the source has text of its own, isn't a format the producer reads, or the output isn't the producer's, with nothing written. |
| `import_reducto(bond, pdf, parse, name)` | Ingest a PDF as `name` and record its Reducto parse as `name.reducto`, for a PDF not added yet. |

What a transcription transcribes is read from its citations, never from its name: `bond.transcriptions_of(version)` lists the versions with `transcription` citations into it, each with its producer and where it is filed. `<document>.<producer>` is only where `record` files one by default. So a transcription recorded just as its source moved on is evidence of the older version, and says so: reading a PDF whose current version has no transcription names those of its earlier versions, and that they may not match it.

A transcription's citations name its producer, and what rests on them can come back supported down to the page. So its output must reach `record` from whatever ran the producer, never by way of a model: an agent may ask for a transcription through a tool its deployment offers, but never hands one in. On the command line, `bond transcribe` is a person's command for the same reason (see [Running Bond locally](local.md#only-people-add-sources)).
