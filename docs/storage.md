# Storage and data handling

## Backends

A workspace's records and filing live in a **store** (keys, each written once) and its bytes in **blobs** (named by their sha256). They are separate protocols, `bond.storage.store.Store` and `bond.storage.Blobs`, so they pair freely. One store holds any number of workspaces: every key starts with its workspace's id, and a `Bond` is opened on one workspace by name and reads and writes nothing else.

| Backend | For | Takes |
|---|---|---|
| `SQLiteStore(path)` | development, tests, one machine | a file path |
| `PostgresStore(pool)` | production, on your database | your psycopg 3 connection pool |
| `MemoryStore()` | tests, and workspaces that live as long as their process | nothing |
| `LocalBlobs(root)` | development, tests, one machine | a directory |
| `S3Blobs(client, bucket, prefix="")` | production, in your bucket (S3, or MinIO, R2, Ceph) | your boto3 S3 client |

```python
from psycopg_pool import ConnectionPool
import boto3

from bond import Bond
from bond.storage.postgres import PostgresStore
from bond.storage.s3 import S3Blobs

pool = ConnectionPool(dsn)
case = Bond(PostgresStore(pool), S3Blobs(boto3.client("s3"), "evidence", prefix="bond/"), "case-001")
```

The `bond` command serves local workspaces only. To serve these to an agent or the viewer, hand them to the servers directly:

```python
from bond.http.server import create_app          # the viewer's API, as `bond view` runs it
from bond.mcp.server import create_server        # the agent's tools, as `bond mcp` runs them
from bond.workspaces import Fixed

offered = Fixed({"case-001": case})
create_server(offered).run("stdio")              # or serve create_app(offered) with uvicorn
```

`Bond(store, blobs, workspace)` opens the workspace named `workspace`, creating it unless `create=False`; with no name, the store's only workspace (a new one named `default` if it has none). `Bond.local(root)` is a `SQLiteStore` at `root / "bond.db"` and `LocalBlobs(root / "blobs")`, a directory holding one workspace named after it, closed with the `Bond`. Any other store and blobs stay the caller's to close. Blobs are named by hash, so workspaces can share a bucket (the same policy is stored once) or keep their own prefixes, as you choose.

**Bringing your own store.** A `Store` is three methods: `create(key, value)` stores a value only if the key is absent and says whether it did, `get(key)` reads one, and `list(prefix, after=None)` lists keys in order. A store may also offer `last(prefix)` (the `LastKey` protocol), which finds a document's head in one read. That is all: no updates, deletes, transactions or indexes. Everything else is Bond's, so a store can't get a rule of filing wrong: `bond.storage.records` (versions), `bond.storage.filing` (documents: names, history entries, titles, and filing a version), `bond.storage.index` (reverse lookups) and `bond.storage.workspace` (opening one by name). A `Bond` keeps them private; `bond.details` has the lower-level reads built on them (history by document, citations without reading bytes, reverse lookups) for tools like the viewer. `tests/test_store.py` is the contract a store must pass.

- **Bond doesn't depend on a driver or SDK.** You pass the pool and client you configured (credentials, TLS, region, endpoint, retries), and install `psycopg[pool]` or `boto3` yourself.
- **The SQL stores are one table, `bond_keys (key, value)`**, plus `bond_schema`. Prefixed, so a store can share a database with your own tables; to keep it apart, point the pool's `search_path` at a schema. They are created when a store opens a database with none of Bond's tables; a database with another Bond schema is refused (there are no migrations).
- **One `Bond` is safe to share across threads**, and several processes or machines can share one store. Bond holds no connection or transaction itself: SQLite gives each thread its own connection, Postgres takes one from the pool per call.
- **Nothing is updated in place.** A version's record, a document's name, each entry of its history and each of its titles is a key written once. A document's head is its latest history entry, and its title its latest title entry.
- **Filing is creating the next history entry.** A write reads the document's head, then creates entry seq + 1. If another write created it first, the store refuses the create and the write fails with `stale_base` (or `document_exists`, for a new document). Writing a version that is already the head is a no-op, so a retried write succeeds.
- **A write stores in order: blob, record, entry.** A crash, or a write that loses the race for its entry, can leave an orphan blob, or a version stored but filed in no document (an *unfiled* version). It is never anyone's head, and nothing rests on it unless something filed cites it. A head always has its record, and a record its bytes.
- **Reverse lookups are an index Bond derives.** What cites a version, which documents hold it and which cited versions are stored elsewhere are kept in memory per `Bond`, caught up from the store before each such query, so writes by other processes are seen. Write checks and `verify` never read the index.
- **Backends store; they never judge.** Hashing, checks, kinds, policies and filing stay in Bond, and `verify` recomputes every id from what a backend returns.
- `S3Blobs` sends each blob's sha256 with its upload, so S3 refuses bytes corrupted on the way.

`tests/test_storage.py` checks Bond's records, filing and index on every store, most of it concurrency: threads sharing a store, writers racing on one head (which must leave one unbroken history), and the same version stored by several writers at once. It also checks isolation: two workspaces in one store sharing a version and a document name, where every read of one shows nothing of the other. Set `BOND_TEST_POSTGRES` to a Postgres DSN to run it, and the rest of the suite, against Postgres as well.

## Keys

Every value is JSON. `ws` is the workspace's id (`ws_` and 32 hex digits); a name in a key is its sha256, never the name. So every element of a key is a word of lowercase ASCII letters, digits and `_` (an id, a hash, or a kind of key like `records`) or a seq, an integer from 0 to 10<sup>20</sup> − 1, and a store is promised nothing else: `key_path` refuses any other key. It writes a key as one string, `/` between elements and a seq as `~` and twenty digits, that sorts in key order by bytes or by code point.

```
("workspaces", sha256(name))         -> {id, name, created_at}
(ws, "records", version)             -> {id, content_hash, format, created_at,
                                         citations: [{id, kind, claim, producer, supports: [[version, locator], …]}]}
(ws, "names", sha256(name))          -> {id, name, created_at}           a document's id, claimed once
(ws, "entries", document_id, seq)    -> {version, base, at, origin}
(ws, "titles", document_id, seq)     -> {title, at, after}               after: the entry seq it followed
```

A claimed name with no entries yet is not a document (a writer stopped between the two). No derived data is stored: parsed documents are held in memory only (a bounded cache per `Bond`), and the index is rebuilt from the keys.

## What Bond stores

Bond is often deployed over documents with personal or confidential information, so this is precisely where that information can and can't end up. Whether any of it counts as regulated data is the deployer's determination; these are the facts to make it with.

**Records hold no content, and keys hold no text.** A version's record (its `records` key) is made only of hashes, enums, numbers, positional locators and producer ids. No field holds text from any document. Keys hold only ids, hashes, seqs and Bond's own words, since keys reach logs that values don't (an object store's access logs, a database's query log): a name in a key is its hash.

| Field | Holds |
|---|---|
| a record's key | Bond's id for the workspace (`ws_` and 32 hex digits, never its name), `"records"`, the version's id |
| `id`, `content_hash`; a citation's `id`; a support's version | SHA-256 hashes (hex) |
| `format`, `kind` | a format, a citation kind |
| `created_at` | a timestamp |
| `claim`, a support's locator | a locator: numbers only, and for JSON a positional path, never keys |
| `producer` | `null`, or the tool whose output a citation is, by id (`"reducto"`): a lowercase letter, then lowercase letters, digits and hyphens |

This is enforced, not hoped for: a producer that isn't an id is refused, and a citation holds nothing else, and `tests/test_private_data_stays_out_of_records.py` checks a workspace built from every fixture plus adversarial sources (names in JSON keys and values, headings, file names, titles, and an agent writing through the tools): every record field must have one of the shapes above, no word of any document may appear in any record, and every key must be ids, hashes, seqs and Bond's own words. The hashes can't be reversed, but a source so short that its candidates can be listed (a lone name or date) could be found by hashing each candidate; versions hash whole files, so this applies only to tiny sources.

**What else can hold content:**

| Where | What | Can hold content? |
|---|---|---|
| Blobs | every version's bytes | **Yes**: they are the documents |
| `workspaces` values | a workspace's id, name and creation time | **Yes**: the name is free text the host chose; name workspaces with opaque case ids, not clients' names or case numbers |
| `names` values | a document's id, name and creation time | **Yes**: names are free text, like file names |
| `titles` values | each title a document has had, and when | **Yes**: titles are free text, and every past one is kept |
| `entries` values | which versions a document held, and when; for an ingested file, its `origin` | **Yes**, through `origin`, free text like names and titles (a path like `Cases/Smith/fax.pdf` can hold a client's name); the rest are ids and timestamps |
| keys | ids, hashes (of names too), seqs | No |
| `bond_schema` (SQL stores) | the schema version | No |

**Across workspaces.** One store can hold many workspaces. A case that links to a policy library cites the library's versions where they are: nothing is copied into the case, and the records of the case name the library's versions by id. Bond keeps no record of links; the deployment does (`bond.local` keeps them in `home.db`). A version's id is the same in every workspace that holds it, so someone with access across workspaces can see that two cases hold identical bytes (the same fax filed for two clients).

**What Bond emits.** Issues from writes and the authoring tools quote the text they are about (`no citation covers 'Jordan Avery'`), so a writer can fix it; they go to the caller, and Bond logs nothing. The viewer API and the authoring tools return document text to whoever calls them. The MCP server passes unexpected errors to the MCP SDK, which logs them, and a message can include values from a document, so treat those logs like the workspace.

**Deleting.** Bond has no delete operation. A deployment that must delete documents deletes their blobs and, if names, titles or origins hold content, their name, title and entry keys (out of band: no Store deletes); citations into deleted versions then fail `verify` with `blob_missing`, and the records, holding no content, can stay. A workspace is the natural unit of deletion: nothing outside a case rests on it (cases link to shared workspaces, never the other way), so deleting a case is deleting its keys (every key that starts with its id, and its `workspaces` key) and the blobs no other workspace holds.
