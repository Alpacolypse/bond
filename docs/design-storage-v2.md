# Design: one write-once store, a pure write path, and staleness

Status: draft. In progress on the `storage-v2` branch:

- **Done:** the `Store` protocol and `MemoryStore` (`bond/storage/store.py`); Records, Filing and `MemoryIndex` on a store (`bond/storage/records.py`, `filing.py`, `index.py`, with `workspace.py` and `values.py`), held by `Bond` as `bond.records`, `bond.filing` and `bond.index`; `SQLiteStore` and `PostgresStore`, one `bond_keys` table each (`bond/storage/sql.py`). `Bond(store, blobs, workspace)` replaces `Bond(catalog, blobs)`, and the old `Catalog` protocol (and the interim class of that name), `SQLiteCatalog`, `PostgresCatalog` and their tables are gone. The suite runs on SQLite, memory and (with `BOND_TEST_POSTGRES`) Postgres stores; `tests/test_store.py` is the Store contract. Plan and commit (`bond/plans.py`): `plan_write`, `plan_ingest`, `plan_import`, `plan_import_version`, `commit`, `stage` and `load_plan`; every write goes through them. `transcriptions_of`, and read naming transcriptions relative to the current head. `freshness` (current, newer head, when superseded, whether current as of a time, span fate: unchanged, moved, gone or unknown), surfaced as `Hit.newer`, `Trace.newer`, `cites_superseded` warnings on plans and writes, `Policy(refuse_superseded=True)`, and in the agent tools.
- **Not yet:** DynamoDB/S3 stores, a Nexus store; span fate's "closest passage" when the text changed (it reports `gone`); following a PDF region into the new head's transcription (it reports `unknown`); freshness on `cited_by`.

## Summary

Bond asks a host to implement a 17-method `Catalog` plus `Blobs`. Most of those methods are Bond's own logic and indexes, shaped by SQL. They make Bond hard to put on any store that isn't a relational database, such as DynamoDB, S3 or a platform that keeps state as events, and they make each host re-implement rules that only Bond should own. This design:

1. **Shrinks what a host implements to one tiny store.**
   - Everything Bond stores becomes **write-once keys**: versions and their citations, the names of documents, the entries of their histories, and their titles. Nothing is ever updated in place, and a document's head is derived: it is its latest history entry.
   - A host provides a **`Store`** with three methods: create a key if it is absent, read a key, list keys under a prefix. Bytes stay in **`Blobs`**, as today.
   - **Records, Filing and the Index are Bond's own code** built on a `Store`, not protocols anyone implements. Bond ships `Store` adapters for SQLite, Postgres, DynamoDB, S3 and memory, so most hosts implement nothing at all.
2. **Separates deciding a write from committing it.**
   - `plan_write` is pure. It reads a snapshot and returns a plan, or every issue.
   - `commit` applies a plan.
   - A host that wants to own the commit, like an agent orchestrator that records each step, takes the plan and commits it itself.
3. **Finds a source's transcriptions through citations, not names.** This removes the only place where Bond would have wanted a guarantee spanning two documents.
4. **Adds staleness as a query.** It answers whether a version is still a document's head, what is newer, whether it was the head when it was cited, and whether the cited span survived. Search, trace, write and cited_by all show it.

The result is that Bond needs exactly one coordination primitive from any store: **creating a key only if it doesn't exist yet**, with strongly consistent reads.

## Who this affects

**Developers using Bond: nothing changes.** They open a workspace on a backend Bond ships and address documents by name:

```python
bond = Bond.local(root)                                              # or Bond(store, blobs) in production
bond.write_document("summary.md", draft, citations)                  # create
doc = bond.document("summary.md")                                    # its head: what the name means now
bond.write_document("summary.md", draft2, citations2, base=doc.head) # next version; stale_base if it moved
bond.history("summary.md")                                           # every version it has held
```

What they gain is `plan_write` and `commit`, `transcriptions_of`, and `freshness` (all below).

**Hosts with their own storage** implement `Store` and `Blobs`, or pass a client to an adapter Bond ships. That is the only extension point. Everything below "The store" explains what Bond does on top of it, for reviewers of this design; a host doesn't need to know it.

## Motivation

The first host outside `bond.local` is Antelope, an agent on a platform built on DynamoDB, S3 and an event store. Mapping `Catalog` onto it showed four problems:

- **Most of the contract is Bond's logic, not storage.** `citations_supported_by`, `documents_holding`, `head_versions`, `outside_versions`, `citation_counts` and `citation_edges` are indexes, easy in SQL and a projection to build and keep consistent anywhere else. `file` holds Bond's filing rules: when a write conflicts, when it's a harmless retry. A host implementing these re-implements Bond, and can get it subtly wrong.
- **Writes are a single opaque call.** `Bond.write_document` checks, writes blobs and files in one step, against stores Bond holds. A host that wants writes to go through its own transaction or event log, and that wants a write step to be safe to re-run, can't separate deciding the write from performing it.
- **What Bond needs for correctness was never stated separately from what it uses for convenience.** Working it through: Bond needs per-document coordination and nothing global. Its one apparent exception, `transcription.record`, comes from linking a transcription to its source by name.
- **Filing reads as mutable when it almost isn't.** History rows are already insert-only, and the head is already derived from them. The only update in place is `retitle`. So a conditional *create* is all Bond needs, and conditional create is far more widely supported than conditional update.

## Goals

- A host can back Bond with anything that offers create-if-absent, strongly consistent reads and ordered listing by prefix: SQL, DynamoDB, S3 on its own, or an event store with expected-version appends.
- A host implements at most five methods (three for `Store`, two for `Blobs`, which most already have), and a conformance suite proves them correct.
- All of Bond's rules (conflicts, retries, heads, indexes) live in Bond, written and tested once.
- Nothing Bond stores is ever updated in place.
- Writes can be decided without performing I/O against Bond's stores, and committed by the host.
- Agents and reviewers can see when evidence has been superseded, without Bond ever moving a citation.
- Every [invariant](concepts.md#invariants) holds, and PHI-safe records stay PHI-safe.

## Non-goals

- **A global order, change feed or event log.** A host that wants one builds it from its own commits. Bond doesn't depend on order across documents.
- **Atomic writes across documents as a Bond requirement.** These are an optional store capability for host features (below). Bond's own operations never need them.
- **Forking histories.** Bond could let two writers both append after the same head and keep several heads, as git or a CRDT does, and then need no coordination at all. It doesn't: that moves the conflict from write time, where an agent gets `stale_base` and re-reads, to read time, where every reader has to work out which head is the document.
- **Preventing stale citations.** This is impossible in principle (see [Staleness](#staleness)). Bond reports staleness.
- **Delete as a Bond operation.** Deleting for PHI stays out of band, as today (see [What Bond stores](storage.md#what-bond-stores)).
- **Settling document ids versus names as keys.** This design keeps today's document ids. Whether a document could simply be its name is a separate question (see [Open questions](#open-questions)).

## Design

### The store

```python
Key = tuple[str | int, ...]   # e.g. ("ws_3f…", "entries", "doc_9a…", 4)

class Store(Protocol):
    """Write-once keys. Safe to use from any thread, process or machine."""

    def create(self, key: Key, value: bytes) -> bool:
        """Store value under key if nothing is stored there, durably before returning. True if this call created
        it; False if the key already existed, in which case nothing is changed. Never overwrites."""

    def get(self, key: Key) -> bytes | None:
        """The value under key, or None. Strongly consistent: sees every create that returned before it started."""

    def list(self, prefix: Key, after: Key | None = None) -> Iterator[Key]:
        """Keys under prefix, in key order, starting after `after` if given."""
```

- **Key order** is element by element: strings by code point, integers numerically. Adapters for stores that sort strings encode integers so they sort correctly, for example zero-padded.
- **Values are opaque bytes.** Bond encodes them (JSON), so the store never interprets them.
- **That's the entire contract.** No updates, deletes, transactions or secondary indexes.

One optional capability, which `Store` adapters provide when their store can:

```python
class LastKey(Protocol):
    def last(self, prefix: Key) -> Key | None:
        """The greatest key under prefix. Lets Bond find a document's head with one read instead of a listing."""
```

`Blobs` is unchanged: `put(content_hash, data)` and `get(content_hash)`. It is conceptually write-once too, but stays separate because bytes are large and usually belong in object storage while keys belong in a database, and hosts pair the two freely.

### What Bond stores in it

Every key starts with the workspace's id, so one store can hold many workspaces and a workspace's keys can be listed, exported or deleted together.

| Kind | Key | Value | When the key already exists |
|---|---|---|---|
| Workspace | `("workspaces", sha256(name))` | id, name, created_at | someone created it: use theirs |
| Record | `(ws, "records", version)` | the version and its citations | same record: done |
| Name claim | `(ws, "names", sha256(name))` | document id, name, created_at | someone claimed it: use their id |
| History entry | `(ws, "entries", document_id, seq)` | version, base, at, `imported`, `origin` | **conflict**: `stale_base` or `document_exists` |
| Title | `(ws, "titles", document_id, seq)` | title or none, at | taken: retry at the next seq |

- **Names are hashed in keys** and kept in values. Keys show up where values don't: S3 access logs, CloudTrail, DynamoDB metrics, database query logs. A document name can hold content (`Cases/Smith/fax.pdf`), so it never appears in a key. This is also why entries and titles are keyed by document id rather than name.
- **The only real conflict** is the history entry: two writers who both read entry 5 and both create entry 6 with different versions. Exactly one wins. Every other key is content-addressed, so two writers write the same thing, or is resolved by using the first writer's value or retrying.

### Bond's layers on the store

These are Bond's classes, not protocols. They are described here so the design can be reviewed.

#### Records

A **record** is one version with the citations it was written with: today's `NewVersion` minus the bytes, which live in Blobs. `Records.put` is `store.create` (a `False` result means it was stored already, which is fine), `Records.get` is `store.get`, and `Records.scan` lists `(ws, "records")`.

This replaces `get_version`, `put_version`, `citations_of` and `all_versions`.

#### Filing

A document is a name plus the ordered list of versions it has pointed at; the last one is what the name means now. Bond stores that as a name claim and numbered history entries, and derives the rest:

- **Head:** the document's latest entry. With `LastKey`, one read. Without it (S3), Bond lists the document's entries, starting after the last seq it has seen in this process, so a warm read is one short listing and a cold read lists a history of a few dozen entries.
- **Title:** the latest title entry, found the same way.
- **Documents in a workspace:** the name claims that have at least one entry.

**Filing a version:**

1. Read the document's head. Decide with today's `filing_problem`, unchanged: refiling the current head is a no-op, a new document needs no base, an existing one needs its head as base.
2. For a new document, create its name claim with a fresh random id. If the claim exists already, use the id in it. A claim with no entries yet is not a document, so a crash between claiming and appending leaves nothing visible, and a retry reuses the claim.
3. Create entry `seq + 1` (entry 1 for a new document).
4. If the create returns `False`, someone created that entry first. Re-read the head. If it is the version being filed, the write already happened (a retry, or two writers filing the same version): succeed with `advanced=False`. Otherwise fail with `stale_base`, or `document_exists` when the writer meant to create the document.

**Retitling** reads the latest title entry and creates the next one; if that seq is taken, it re-reads and retries. Titles are labels, not provenance, so the last writer wins, and they are kept apart from history entries so retitling never adds a history entry or conflicts with a write.

**Filing as of any time.** Because no entry is ever replaced, a document's head at any past moment is the latest entry at or before it. Staleness uses this to say whether a version was current when something cited it.

This replaces `file`, `get_document`, `all_documents`, `history`, `retitle` and `head_versions`.

#### Index

Reverse lookups, derived from records and filing:

| Query | Used by | Derived from |
|---|---|---|
| `supported_by(v)` (was `citations_supported_by`) | `cited_by`, read's transcription list, `import_version` | records' supports |
| `holding(v)` (was `documents_holding`) | search hits, staleness | history entries |
| `outside()` (was `outside_versions`) | `frontier` | supports pointing at versions with no record |
| `counts()`, `edges()` | the viewer | records |

- **`MemoryIndex` is the default.** It is built by scanning a workspace's records and entries once per `Bond`, kept up to date by that `Bond`'s own commits, and refreshed on an interval or when a host calls `bond.refresh()` to pick up other processes' writes. That is enough for workspaces of a case's size (hundreds of versions).
- **A backend may supply a persistent index** for speed. The SQL backend does, with tables that keep `cited_by` and search fast as workspaces grow. An index is the one optimisation hook, and it is never a source of truth: `bond.index.rebuild` repopulates one from the store, and the conformance suite checks that a rebuilt index answers every query the same as one maintained incrementally.
- **A lagging index can only under-report**, such as a missing `cited_by` entry. It never changes what a version cites or whether a write is accepted, because write checks and `verify` read records and filing only.

#### Size of the contract

| | A host implements | Updates in place | Coordinates |
|---|---|---|---|
| Today | `Catalog` (17) + `Blobs` (2) | `retitle`, and a head that `file` moves | `file` |
| This design | `Store` (3) + `Blobs` (2), or neither, with a shipped adapter | none | `create`, on one key |

### Write order and crash safety

A commit is **blob → record → name claim → entry → title → index**. Every step creates keys that are either absent or already hold what the step would write, so every step is idempotent:

- A crash after the blob leaves an orphan blob, as today.
- A crash after the record leaves an **unfiled version**: present in records, held by no document. Unfiled versions already exist, since `put_version` makes them for imports. They are harmless: search covers heads only, and nothing cites them unless something filed does.
- A crash after a name claim leaves a claimed name with no entries. It isn't a document, and the next write to that name reuses the claim.
- A write that loses the race for an entry also leaves an unfiled version. This is a change: today's `file` appends the history entry first and stores rows only after winning. The trade buys a contract with no transactions. The viewer labels unfiled versions, and an optional `bond gc` can drop records that are unfiled, unimported and uncited, along with their blobs.

A commit retried from the start is safe at every step: creating a key that already holds the same value succeeds, and appending a version that is already the head is a no-op.

### The pure write path

```python
plan = bond.plan_write(document, content, citations, base=..., policy=..., title=...)
# -> WritePlan, or raises WriteError with every issue, exactly as write_document does today

result = bond.commit(plan)
# -> WriteResult, or WriteError([stale_base]) if the document moved after planning
```

```python
@dataclass(frozen=True)
class WritePlan:
    workspace: str                    # the workspace's id: committed only there
    versions: tuple[PlannedVersion, ...]  # NewVersions (bytes and citations), each before any resting on it;
                                      # an import's marked imported (identity checked, not policy)
    filing: FilingChange | None       # document, version, base, origin, import source, title
    policy: Policy                    # what written versions were checked against, and are again if loaded
    # later: warnings, e.g. cites superseded evidence (see Staleness)
```

- **`plan_write` reads only.** It performs no writes, so the plan it yields from a given snapshot is deterministic, and `check_write` becomes "plan and discard".
- **`commit` is the only function that writes**, in the order above. `write_document` becomes `commit(plan_write(...))`, and its behaviour is unchanged for existing callers.
- **The plan carries the base the writer gave.** `commit` checks it against the head again and creates the next entry, so whether a commit wins is decided by one create, and committing the same plan twice is safe (filing the head again is a no-op).
- **Imports plan too.** `plan_import(source, document, ...)` returns a `WritePlan` whose records are the versions brought across.
- **Plans serialize.** `WritePlan.to_json()` holds records and the filing change, and names blobs by hash. A host that records each step durably, like an orchestrator writing to a task log, stores the blob first and the plan by reference, then commits later, or commits again after a crash.

### Optional: atomic commit across documents

Some host features want several documents to move together, for example an artifact and the summary that cites it. Because everything is write-once, this is creating several keys at once:

```python
class AtomicCreate(Protocol):
    def create_all(self, items: Mapping[Key, bytes], absent: Sequence[Key] = ()) -> bool:
        """Create every item if none of their keys exist and none of the keys in absent exist; otherwise create
        nothing. True if they were created."""
```

`absent` expresses "this document hasn't moved since I read it" as "its next entry doesn't exist yet". With it, `bond.commit_all(plans, read_set=...)` commits several plans as one, optionally failing if any document the writer read has moved. This catches write skew for hosts that care. Without it, `commit_all` is unavailable and Bond's own operations are unaffected. SQL and DynamoDB (`TransactWriteItems`) adapters provide it. S3 can't.

### Transcriptions found through citations

Today `transcription.record` reads the source's head, cites regions of it, and files the output as `<document>.<producer>`. Its base is the transcription document's own head. If the source advances in between, the transcription document holds a transcription of an older version under a name implying the current one. Preventing this would need a condition on two documents.

The link already lives in the evidence: a transcription's `TRANSCRIPTION` citations name exactly the version they transcribe. `authoring.tools.read` already finds a PDF's transcriptions this way (`cited_by` the version, filtered to `TRANSCRIPTION`). This design makes that the rule:

- **New core query, `Bond.transcriptions_of(version) -> list[TranscriptionOf]`.** Each result gives a transcribing version, its producer (from the citations' attrs), and the documents holding it. It moves the logic out of `authoring/tools.py` into the core, where the CLI, the viewer and hosts can use it.
- **Reading a PDF reports transcriptions relative to its current head.** If the head has none but earlier versions do, read says so: "`fax.pdf` is at P2; its transcriptions are of P1 (`fax.pdf.reducto`)". This is the staleness query applied to transcriptions.
- **`<document>.<producer>` becomes only a default label.** `record()` keeps filing there, so names stay predictable for people. Nothing in Bond infers what a transcription transcribes from its name. `record()`'s `WriteResult` gains the source version it transcribed.
- **The race becomes harmless.** A transcription filed after its source moved is correct evidence of the older version, and every reader is told so.

### Staleness

A write can be checked against the current heads at commit time and still be stale a second later, so freshness can't be guaranteed by a write. It is a relationship between a version and filing *now*. Like support status, it is computed when asked and never stored.

#### The query

```python
bond.freshness(ref, document: str | None = None, as_of: str | None = None) -> Freshness
```

```python
@dataclass(frozen=True)
class Freshness:
    version: VersionId
    in_documents: list[DocumentFreshness]   # one per document whose history holds the version
    imported: bool                          # brought from another workspace: newer versions there are unknown

@dataclass(frozen=True)
class DocumentFreshness:
    document: str
    current: bool                           # the version is this document's head now
    head: VersionId                         # the document's head now
    superseded_at: str | None               # when the document first moved past it (that entry's at)
    current_as_of: bool | None              # whether it was the head at as_of, when as_of was given
    span: SpanFate | None                   # only when asked for a Ref narrower than the whole version

@dataclass(frozen=True)
class SpanFate:
    status: Literal["unchanged", "moved", "changed", "gone", "unknown"]
    at: Ref | None                          # where it is in the head now (moved), or the closest passage (changed)
```

- **Staleness is relative to a document.** The same version can be the head of one document and superseded in another. With `document=` the answer is about that document only.
- **"Current when cited."** Given `as_of`, freshness reads the history as it stood then, which write-once filing makes exact. `cited_by` and `trace` pass the citing version's filing time, so a reviewer sees "this was current when the summary cited it, and superseded since" instead of just "stale".
- **Span fate** asks whether the cited part actually changed. For text formats, Bond looks for the span's text in the head: an exact match, then a match under quote normalization (`normalize.py`), then the closest passage. For JSON, it compares the value at the same pointer, then looks for an equal value elsewhere. For CSV, the same cell, then an equal row. For PDF regions it reports `unknown`, and points to the head's transcriptions instead. `at` is a suggestion only: **Bond never re-points a citation**.
- **Imported versions** report `imported: True` and nothing newer, since the source workspace may have moved on. Re-importing checks it, as with the frontier.
- **Cost.** `current` and `head` take one `index.holding` lookup and one head read per document. `current_as_of` reads one document's history. Span fate parses the head and is computed only where noted below.

#### Where it shows up

| Surface | What it carries | Span fate? |
|---|---|---|
| `search` hits | `current`, and the newer head's handle when not current | no |
| `trace` nodes | `current`, `head`, and whether it was current when its citer was filed | on request (`freshness=True`) |
| `write` / `plan_write` | a `cites_superseded` warning per citation into a non-current version, with span fate | yes |
| `cited_by` | each citing version's own freshness, and whether the cited version was current when it was cited | no |
| authoring tools | the same, rendered for agents: `superseded in notes by <handle>; the quoted text is unchanged there` | as above |

- Warnings are new: `WriteResult` and `WritePlan` gain `warnings: list[Issue]`. They never block.
- `Policy(refuse_superseded=True)` turns `cites_superseded` into a refusal, for hosts that want it. It is checked against the snapshot, so it is a best-effort guard, not a guarantee.

## Adapters Bond ships

Each takes a client or pool the host configured, the way `S3Blobs` takes a boto3 client today, and depends on no SDK Bond installs.

| Adapter | create | get | list | `LastKey` | `AtomicCreate` |
|---|---|---|---|---|---|
| `SQLiteStore(path)`, `PostgresStore(pool)` | `INSERT … ON CONFLICT DO NOTHING` into `bond_keys (key, value)` | primary key | range scan on `key` | `ORDER BY key DESC LIMIT 1` | a transaction |
| `DynamoDBStore(client, table)` | `PutItem` with `attribute_not_exists` | `GetItem`, strongly consistent | `Query`, partition key = the key's first elements, sort key `begins_with` | `Query`, descending, `Limit 1` | `TransactWriteItems` |
| `S3Store(client, bucket, prefix)` | `PutObject` with `If-None-Match: *` | `GetObject` | `ListObjectsV2` with `StartAfter` | — | — |
| `MemoryStore()` | a dict, under a lock | | | ✓ | ✓ |

- **SQL also ships `SQLIndex`**, reverse-lookup tables in the same database, so `cited_by`, search and the viewer stay fast. Bond maintains it after each commit; `rebuild` regenerates it.
- **`S3Store` plus `S3Blobs` is a complete Bond backend in one bucket**, with no database. It suits small deployments and testing against real object storage.
- **An event-sourced host** can use `DynamoDBStore` beside its log, or implement `Store` over the log: each key's create is an append to a stream for that key that expects version zero.

The platform's backend is then `Bond(DynamoDBStore(client, table), ArtifactStoreBlobs(...))`, where the second is a two-method wrapper over the platform's existing content-addressed artifact store.

## Conformance suite

Shipped as `bond.testing.conformance`, so a host can run it against its own adapter:

- **`Store`** (all a host-written adapter needs to pass):
  - Creating a key twice with the same value returns `True` then `False`, and creating it with a different value never overwrites.
  - Concurrent creates of one key have exactly one winner.
  - A read after a create sees it.
  - Listing returns every key under a prefix, in key order, including integer elements in numeric order, and honours `after`.
  - Keys under different prefixes never interfere.
  - If provided: `last` matches the last listed key, and `create_all` is all or nothing.
- **`Blobs`:** durable before returning; `get` of a missing hash returns `None`.
- **Bond on the store** (run once per adapter, exercising Bond's layers):
  - Writers racing on one head leave one unbroken history and exactly one winner.
  - Refiling the head is a no-op whatever the base; a missing base fails with `document_exists`, and a wrong base with `stale_base`.
  - A claimed name with no entries is invisible, and is reused by the next write.
  - Concurrent retitles all land, the latest wins, and none conflicts with a write.
  - Workspaces in one store see nothing of each other.
  - A rebuilt index answers every query the same as one maintained incrementally.
  - A full write, import and transcription workload, crashed at each step of `commit` and retried, ends in the same state as an uninterrupted run.

## Invariants

All eleven [invariants](concepts.md#invariants) hold as written. Two get sharper wording, and one is added:

- **9.** *Evidence and filing are separate.* Add: "Bond's only coordination is creating the next entry of one document's history, if no one has; nothing Bond does needs two documents to change together."
- **7.** *A PDF's text is a derived document…* Add: "What a transcription transcribes is read from its citations, never from its name."
- **New, 12.** "Nothing Bond stores is updated in place. Every stored key is written once; a document's head and title are its latest entries."

**PHI.** Records' contents are unchanged and still hold no content. Name claims and titles hold free text, as `bond_documents` does today, and [What Bond stores](storage.md#what-bond-stores) is updated to list them by key kind. Two things are new:
- **No free text in keys**: names are hashed in keys (above), because keys leak into logs that values don't.
- **Every past title is kept.** A deployment deleting a document's names and titles deletes its name claim and all its title entries.

`Freshness` and `SpanFate` return handles, store nothing, and quote document text only in agent-facing messages, as write issues already do.

## Rollout

Each step is a separate change, and each leaves `main` working:

1. **`Store`, `MemoryStore` and Bond's layers.** Add Records, Filing and `MemoryIndex` over a `Store`, with the filing logic moved into Bond. Add `Bond(store, blobs)` beside today's `Bond(catalog, blobs)`, and run the existing test suite against both.
2. **Conformance suite** as `bond.testing.conformance`, passing on `MemoryStore`.
3. **SQL adapters.** `SQLiteStore`, `PostgresStore` and `SQLIndex`, as a new schema version. `bond.local` homes move to it with a one-time copy (`bond migrate`), since Bond has no in-place migrations. Then remove `Catalog` and the old backends.
4. **Plan and commit.** Split `_write` into `plan_write` and `commit`, and `import_document` into `plan_import` and `commit`. `write_document` and `check_write` become thin wrappers. Add plan serialization, versioned from the start.
5. **`DynamoDBStore` and `S3Store`**, each passing the conformance suite.
6. **`transcriptions_of`**, moved from the authoring tools into the core, with read reporting transcriptions relative to the current head.
7. **`freshness`** and its surfaces: search, trace, write warnings, cited_by, then the authoring tools' rendering. Then `refuse_superseded`.
8. **`AtomicCreate` and `commit_all`**, only if a host asks for them.

Steps 1–5 are the storage work the platform needs. Steps 6–7 don't depend on them and can go in parallel.

## Open questions

- **Document ids or names.** Keys use a random document id from a name claim, as today. Keying entries by the (hashed) name instead would remove claims, at the cost of ever supporting renames. Parked for now.
- **Index freshness with several writer processes.** `MemoryIndex` refreshes on an interval or on `refresh()`. Is that enough for the platform, where several task workers write one case at once, or does it need `SQLIndex`'s equivalent in DynamoDB from the start?
- **Cold-start cost on S3.** Building `MemoryIndex` reads every record in a workspace, one `GetObject` each. Fine for hundreds of versions; a workspace with thousands may want a persistent index even on S3.
- **Should orphans from lost races be hidden or labelled in the viewer?** Labelling is more honest, and hiding is less confusing.
- **Span fate for PDFs.** Should freshness on a PDF region follow the old transcription to the new head's transcription by text, rather than reporting `unknown`?
- **Keeping every past title.** Write-once titles keep history that today's in-place column discards. Is that wanted, or should an adapter be allowed to compact all but the latest title entry?
