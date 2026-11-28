# Python API

A `Bond` is one workspace. See [Concepts](concepts.md) for what versions, citations and locators are.

```python
from bond import Bond, Citation, CitationKind, Ref, TextLocator, VersionLocator

bond = Bond.local("./cases/case-001")  # a workspace; or Bond(store, blobs, name) on your database and bucket: see storage.md

policy = bond.ingest("policy", "policy.md", title="Policy IP-112")   # a source throughout, filed as "policy"
[hit] = bond.search("at least 6 weeks of written notice")     # discovery and citing in one

content = "Policy requires at least 6 weeks of written notice."
cite = [Citation(CitationKind.PARAPHRASE, claim=TextLocator(0, len(content)), supports=[hit.ref])]
plan = bond.plan_write("note", content, cite)                # checked now, nothing stored; WriteError with every issue
result = bond.commit(plan)                                   # the write itself (write_document does both)

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
| `search(phrase, *, documents=None, within=None, limit=50)` | Find a phrase in this workspace's documents' current versions, or inside one reference (not both, and the reference may be in a linked workspace), and pin each match to an exact locator. Matching is case-insensitive and forgives whitespace, quote characters, ligatures and hyphenated line breaks. Each hit carries its direct `declarations` and overlapping `kinds`. |
| `declarations(ref)` | Direct declarations, one hop: `ref`, overlapping `citations` with their original subject spans and supports, and exact `undeclared` gaps (the uncovered words of a version written under `ALLOW_GAPS`, for one). No ancestry judgment. |
| `check_support(ref, *, max_depth=None, limit=1000)` | Assess all selected declarations' ancestry to declared sources. Returns a `SupportReport`: enum `status`, selected `regions` with individual outcomes, `issues` with reasons and explanatory paths. Computed on demand, never stored. See [Support](concepts.md#support). |
| `citations(version)` | The citations a version was written with. |
| `trace(ref, max_depth=None, limit=None)` | Follow provenance from a reference down to its sources, as a DAG: each distinct reference is one node, and each edge is a citation. `limit` keeps the nearest nodes, and `complete` says whether limits omitted any traversal. Nodes may be in linked workspaces; unreachable versions are reported separately, as `unreachable`. |
| `cited_by(ref)` | The reverse of one hop: citations, from any version in this workspace, with a support overlapping a reference (which may be in a linked workspace). |
| `verify(version, policy=STRICT)` | Check a version and every version beneath it: recompute their hashes and ids, and run the write checks again on each as it was written. Returns the issues found, each naming its version (none if intact). |
| `blob(version)`, `text(ref)` | A version's raw bytes; the text a reference addresses (`Ref(v, VersionLocator())` for all of it). A version whose stored bytes are missing or malformed raises `CorruptVersion`; `verify` reports it instead. |

Lower-level reads for tools built on Bond (version records, the documents a version is filed in, where a span is and the text around it, claim units, JSON Pointers) are functions in `bond.details`.

Bond tells you when there is newer evidence, and never decides for you. A citation names an exact version, so it stays exactly as true when its document moves on, and citing an older version is often right (the text a decision was made on). So search hits and traces name what is newer (`Hit.newer` and `Trace.newer` map each document that moved past a version to its head now), and a write (or plan) that cites a version no document holds as its head any more goes through with a `cites_superseded` warning in `WriteResult.warnings` (and `WritePlan.warnings`) naming the citation and support. Whether to cite the newer version instead, or to look again at what rests on an older one, is the application's call: `cited_by` and `newer` answer "what in this case rests on a version that has since moved on".

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

## Workspaces and links

Everything above happens inside one workspace: a write stores and files in it, and search finds its documents. A workspace can also link to others, as its deployment decides: its citations can then rest on their versions where they are, with nothing copied. A case links to a shared policy library and quotes it:

```python
policies = Bond.local("./policies")                        # shared: the office's intake policies
case = Bond.local("./cases/case-001", links=[policies])    # one case; its name is its directory's

[hit] = policies.search("at least 6 weeks", documents=["policy/IP-112"])
case.write_document("note", "at least 6 weeks", [Citation(CitationKind.QUOTE, TextLocator(0, 16), [hit.ref])])
case.trace(Ref(case.document("note").head, VersionLocator()))   # from the case into policies, down to its source
```

| Function | Does |
|---|---|
| `Bond(..., links=...)`, `Bond.local(..., links=...)`, `set_links(links)` | The workspaces this one links to: a list of opened workspaces, or a function giving them, asked on every read, for a deployment whose links change while it runs. |
| `links()` | The workspaces this one links to directly, as its deployment says now. |
| `reachable()` | Every workspace this one can read: those it links to, those they link to, and so on, each once. A link that loops back is followed no further. |
| `workspace` | This workspace's `id` and `name`. |

A link gives read access only. Reading a version (`text`, `blob`, `citations`), checking a write's supports, `declarations`, `check_support`, `trace` and `verify` all look in the workspace first, then in what it can reach; `search` and `documents` stay in this workspace (search a linked workspace on its own `Bond`, or with the agent tools, which search both), and `cited_by` answers what in this workspace cites a span, so a library never learns which cases cite it. `bond.details.owner(bond, version)` says which workspace a version is read from.

A version no reachable workspace holds (its link was removed, say) is unreachable: a valid state, never a broken one, since its id is still committed to. `trace` stops there and lists it in `Trace.unreachable`; a support check reports it as `unreachable`, so the result is `UNKNOWN`; `verify` reports it once as `unreachable` and checks everything else, skipping only what needs its text (a quote's match). Linking again makes it reachable, and nothing else changes. A write refuses a support no reachable workspace holds.

A version's id is the same wherever it is read from, so a host that can open many cases can ask each one `cited_by` the same span of a policy, which is how to find every determination resting on a paragraph that changed. Bond doesn't run that query across workspaces itself; the host does, over the workspaces it may open. A policy's newer version shows up from the case too: `newer` on a hit or a trace names the policy's head now.

Bond keeps no record of links: they are the deployment's decision (`bond.local` keeps them in its home, refuses a link that would loop, and reads them at each call). Keep links pointing one way, from cases to shared workspaces, so nothing outside a case rests on it and a case can be deleted alone.

[examples/workspaces.py](../examples/workspaces.py) builds a policies workspace and a workspace per case that links to it.

## Plans: deciding a write apart from doing it

```python
plan = bond.plan_write("note", content, cite)   # checked now; stores nothing; WriteError as a write would raise
result = bond.commit(plan)                       # stores it and files it; stale_base if the document moved since
```

| Method | What it does |
|---|---|
| `plan_write(...)`, `plan_ingest(...)` | Take what `write_document` and `ingest` take, and return a `WritePlan` (`bond.plans`): the version to store (none if it is stored already), where to file it, and any `warnings`. Checked against the workspace and its links as they are, or `WriteError` with every issue; nothing is stored. Those two methods are `commit` of these. |
| `commit(plan)` | Store a plan's version (blob, then record) and file it (its history entry, then its title). Refused with `stale_base` if the document moved since the plan was made, having filed nothing. Committing again is harmless. |

To check a write without doing it, plan it and don't commit: `plan_write` raises `WriteError` with every issue the write would have, filing included, and stores nothing. That is how the agent tools' `dry_run` works.

A plan lives in memory, in the process that made it. `commit` doesn't check it again, so it commits only plans `plan_write` and `plan_ingest` made, and refuses one built any other way. A plan stays good: versions are never removed, so what it checked stays true, except the document it files in, which `commit` checks again atomically. So a caller can plan in one step and commit in the next, or commit again after a crash. A host that keeps writes as steps of its own, to commit later or in another process, keeps what it would write (document, content, citations and base) and plans again when it commits: the plan, and its version's id, come out the same, and whatever changed meanwhile (a stale base, a removed link, a superseded source) is checked again.

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
| `record(bond, document, transcription, output, *, source=None)` | Write a producer's output for a version of `document` (`source`, the version the producer read, which `document` must have held; by default the one it holds now: pass `source` for output from a job that ran on bytes read earlier) as `<document>.<producer>`, in the producer's output format, from that document's head if it exists: recording after a new version of the source, or a new run, writes its next version, and the same output for the same source changes nothing. Its citations name the producer by its id; nothing needs registering. Raises `TranscriptionError` if the source has text of its own, isn't a format the producer reads, or the output isn't the producer's, with nothing written. |
| `import_reducto(bond, pdf, parse, name)` | Ingest a PDF as `name` and record its Reducto parse as `name.reducto`, for a PDF not added yet. |

What a transcription transcribes is read from its citations, never from its name: `bond.transcriptions_of(version)` lists the versions with `transcription` citations into it, each with its producer, the documents it is filed in, and those it is the head of now (`current`). `<document>.<producer>` is only where `record` files one by default. `transcribe` cites the version its producer read, so a transcription that finishes just as its source moves on is evidence of the older version, and says so: reading a PDF whose current version has no transcription names those of its earlier versions, and that they may not match it.

A transcription's citations name its producer, and what rests on them can come back supported down to the page. So its output must reach `record` from whatever ran the producer, never by way of a model: an agent may ask for a transcription through a tool its deployment offers, but never hands one in. On the command line, `bond transcribe` is a person's command for the same reason (see [Running Bond locally](local.md#only-people-add-sources)).
