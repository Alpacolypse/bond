# Storage and data handling

## Backends

A workspace is a **catalog** (versions, citations and documents: rows) and **blobs** (bytes named by their sha256). They are separate protocols, `bond.storage.Catalog` and `bond.storage.Blobs`, so they pair freely. One database holds any number of workspaces: every row carries its workspace in its primary key, and a catalog is opened on one workspace by name and reads and writes nothing else (`tests/test_storage.py` checks every query for it).

| Backend | For | Takes |
|---|---|---|
| `SQLiteCatalog(path, workspace=None)` | development, tests, one machine | a file path, and a workspace's name (the only one in the file if not given) |
| `PostgresCatalog(pool, workspace=None)` | production, on your database | your psycopg 3 connection pool, and a workspace's name |
| `LocalBlobs(root)` | development, tests, one machine | a directory |
| `S3Blobs(client, bucket, prefix="")` | production, in your bucket (S3, or MinIO, R2, Ceph) | your boto3 S3 client |

```python
from psycopg_pool import ConnectionPool
import boto3

from bond import Bond
from bond.storage.postgres import PostgresCatalog
from bond.storage.s3 import S3Blobs

pool = ConnectionPool(dsn)
case = Bond(PostgresCatalog(pool, workspace="case-001"), S3Blobs(boto3.client("s3"), "evidence", prefix="bond/"))
```

`Bond.local(root)` is `Bond(SQLiteCatalog(root / "bond.db"), LocalBlobs(root / "blobs"))`, a directory holding one workspace named after it. Blobs are named by hash, so workspaces can share a bucket (the same policy is stored once) or keep their own prefixes, as you choose.

- **Bond doesn't depend on a driver or SDK.** You pass the pool and client you configured (credentials, TLS, region, endpoint, retries), and install `psycopg[pool]` or `boto3` yourself.
- **Tables are prefixed `bond_`**, so a catalog can share a database with your own tables; to keep it apart, point the pool's `search_path` at a schema. They are created when a catalog opens a database with none of them; a database with another Bond schema is refused (there are no migrations yet). Postgres must be UTF8.
- **One `Bond` is safe to share across threads**, and several processes or machines can share one catalog. Bond holds no connection or transaction itself: SQLite gives each thread its own connection, Postgres takes one from the pool per call.
- **Filing is a compare-and-swap on the catalog's unique constraints**, not a lock: a write appends the history entry after the head it read (`INSERT … ON CONFLICT DO NOTHING`), and if another write appended there first, it fails with `stale_base` and stores nothing. The entry goes in before the version's rows, which follow only once it is in, so a refused write needs no rollback; the history's foreign keys to versions are deferred to commit. Versions and blobs are content-addressed, so concurrent writers of the same one simply store it once.
- **Backends store; they never judge.** Hashing, checks, kinds and policies stay in Bond, and `verify` recomputes every id from what a backend returns. A blob is written before its rows, so a crash can leave an orphan blob but never a row without bytes.
- `S3Blobs` sends each blob's sha256 with its upload, so S3 refuses bytes corrupted on the way.

`tests/test_storage.py` is the contract every backend passes, most of it concurrency: threads sharing a catalog, writers racing on one head (which must leave one unbroken history), and the same version stored by several writers at once. It also checks isolation: two workspaces in one database sharing a version and a document name, where every read of one shows nothing of the other. Set `BOND_TEST_POSTGRES` to a Postgres DSN to run it, and the rest of the suite, against Postgres as well.

## Schema

```
bond_workspaces (id PK, name UNIQUE, created_at)
-- evidence
bond_versions  (workspace, id, content_hash, format, created_at)                    PK (workspace, id)
bond_citations (workspace, owner_version, id, kind, claim_locator JSON, attrs JSON)  PK (workspace, owner_version, id)
bond_supports  (workspace, owner_version, citation_id, position, version, locator JSON)
-- filing
bond_documents (id PK, workspace, name, title, created_at)   UNIQUE (workspace, name)
bond_history   (workspace, document_id, seq, version, base, at,
                from_workspace, from_workspace_name, from_version, origin)   PK (document_id, seq), version FKs deferred
```

Nothing is updated or deleted, except a document's title, which is a label. A support's `version` has no foreign key, since it may be outside the workspace; a write checks every support before storing anything. No derived data is stored: parsed documents are held in memory only (a bounded cache per `Bond`), and there is no search index.

## What Bond stores

Bond is often deployed over documents with personal or confidential information, so this is precisely where that information can and can't end up. Whether any of it counts as regulated data is the deployer's determination; these are the facts to make it with.

**Records hold no content.** A version's record (its row in `bond_versions`, and its citations' rows in `bond_citations` and `bond_supports`) is made only of hashes, enums, numbers, positional locators, producer ids, and Bond's own workspace id. No field holds text from any document.

| Field | Holds |
|---|---|
| `workspace` (every record table) | Bond's id for the workspace (`ws_` and 32 hex digits), never its name |
| `id`, `content_hash`, `owner_version`, `citation_id`, `version` | SHA-256 hashes (hex) |
| `format`, `kind` | a format, a citation kind |
| `created_at`, `position` | a timestamp, an integer |
| `claim_locator`, `locator` | a locator: numbers only, and for JSON a positional path, never keys |
| `attrs` | `{}`, or a producer by id (`{"transcriber": "reducto"}`): a lowercase letter, then lowercase letters, digits and hyphens |

This is enforced, not hoped for: the kinds' validators refuse any other attr, and `tests/test_private_data_stays_out_of_records.py` checks a workspace built from every fixture plus adversarial sources (names in JSON keys and values, headings, file names, titles, and an agent writing through the tools): every record field must have one of the shapes above, and no word of any document may appear in any record row. The hashes can't be reversed, but a source so short that its candidates can be listed (a lone name or date) could be found by hashing each candidate; versions hash whole files, so this applies only to tiny sources.

**What else can hold content:**

| Where | What | Can hold content? |
|---|---|---|
| Blobs | every version's bytes | **Yes**: they are the documents |
| `bond_workspaces` | a workspace's id, name and creation time | **Yes**: the name is free text the host chose; name workspaces with opaque case ids, not clients' names or case numbers |
| `bond_documents` | a document's id, workspace, name, title and creation time | **Yes**: names and titles are free text, like file names |
| `bond_history` | which versions a document held, and when; for an import, where it came from; for an ingested file, its `origin` | **Yes**, through `from_workspace_name`, a copy of a workspace's name, and `origin`, free text like names and titles (a path like `Cases/Smith/fax.pdf` can hold a client's name); the rest are ids and timestamps |
| `bond_schema` | the schema version | No |

**Across workspaces.** One database can hold many workspaces, and an import copies a version's rows into the importing workspace, so each case holds its own copy of each policy it cites. A version's id is the same in every workspace that holds it, so someone with access across workspaces can see that two cases hold identical bytes (the same fax filed for two clients).

**What Bond emits.** Issues from writes and the authoring tools quote the text they are about (`no citation covers 'Jordan Avery'`), so a writer can fix it; they go to the caller, and Bond logs nothing. The viewer API and the authoring tools return document text to whoever calls them. The MCP server passes unexpected errors to the MCP SDK, which logs them, and a message can include values from a document, so treat those logs like the workspace.

**Deleting.** Bond has no delete operation. A deployment that must delete documents deletes their blobs and, if names, titles or origins hold content, their filing rows; citations into deleted versions then fail `verify` with `blob_missing`, and the records, holding no content, can stay. A workspace is the natural unit of deletion: nothing outside a case rests on it, so deleting a case is deleting its rows (every table, by `workspace`) and the blobs no other workspace holds.
