# Design: changing Bond without breaking what it has stored

Status: draft. Nothing here is implemented yet.

## Summary

Bond is about to be used by its first host outside `bond.local`. From then on, ids, records and plans that Bond wrote will live in stores and systems Bond doesn't control, and will outlive any single Bond release. Today Bond handles a change in what it stores by refusing the old store, which is fine for a research preview and not for a host. This design:

1. **Names every surface a host keeps.** That covers stored keys and values, ids and handles, serialized plans, and the rules `verify` checks against. It says how each one may change.
2. **Gives every store a schema marker.** Every adapter, not just SQL, then knows what wrote it and refuses what it can't read.
3. **Versions every stored value.** Readers keep reading old formats, so most changes need no migration at all.
4. **Records how each id was made and which rules each version was checked under.** `verify` can then check history as it was written, rather than under today's code.
5. **Migrates by copying.** Key layout changes use a resumable copy into a new keyspace. That is natural for a store where nothing is ever updated in place.
6. **Freezes identity.** Version and citation ids never change meaning. A new id scheme, if ever needed, is added beside the old one.

Steps 4 and the start of 3 are small and additive, and are worth doing before any host stores a real id.

## Who this affects

- **Hosts that keep Bond data.** That means Antelope first, and any customer running Bond on its own Postgres or DynamoDB. They get a promise: a Bond release reads every store written within a published window, and moving past the window is a documented `migrate` step, never silent breakage.
- **`bond.local` users.** For them nothing changes, except that `bond migrate` replaces "start a new home".
- **Bond developers.** They get rules for what kind of change needs what, and golden-store tests that fail when a change breaks an older store.

## What a host keeps

| Surface | Where | Lives as long as | Changes today |
|---|---|---|---|
| Keys and values in a `Store` | the host's database | the case | refused: `SCHEMA_VERSION` mismatch (SQL only) |
| Blobs | the host's object store | the case | never: addressed by content hash |
| Version and citation ids | records, and **outside Bond**: host events, outputs that name them | forever | a new domain tag (`bond.version.v2`) changes every id |
| Handles | agent transcripts, drafts, anything a model wrote | the transcript | follow the ids they prefix |
| Serialized plans (`WritePlan.to_json`) | host logs, e.g. Antelope's stem events | as long as the host keeps them | refused unless `bond_plan == PLAN_FORMAT` |
| The rules (`check`, kind validators, normalization, `Policy`) | Bond's code | each release | `verify` re-checks history under today's rules |
| Python API and MCP tools | host code, agent prompts | each release | semver, once Bond has releases. Not covered here |

## Where Bond stands today

- **One schema number, SQL only.** `storage/sql.py` has `SCHEMA_VERSION = 11` in a `bond_schema` table, and `_init_schema` refuses any other number. `MemoryStore`, and any adapter written on the bare `Store` protocol, has no marker at all. A `DynamoDBStore` written today could open a store from an incompatible Bond and misread it.
- **Values carry no format.** `storage/values.py` writes canonical JSON objects with no version field.
- **Readers already ignore unknown fields.** `_record` in `storage/records.py` looks fields up by name, so a field added to a value is invisible to an older reader.
- **Readers refuse unknown values of known fields.** `CitationKind(...)`, `Format(...)` and `locator_from_dict` raise on a kind, format or locator type they don't know. An older Bond can't read a record that uses a new one.
- **Id schemes are versioned, but invisibly.**
  - `ids.py` prefixes the hashed forms with `bond.version.v2` and `bond.citation.v1`, so a scheme change yields distinct ids.
  - Neither the id nor the record says which scheme made it. `verify` recomputes every id with today's tag, so it would report every version written under an older scheme as corrupt.
- **`verify` uses today's rules.** Its docstring says "Rules are today's and policy is the one supplied now; the historical policy is not stored." A stricter rule, say tighter quote normalization, would make versions that were valid when written fail verification.
- **Plans are versioned, strictly.** `plans.py` has `PLAN_FORMAT = 1`, and `from_json` refuses any other format.

## Kinds of change

Every change to what Bond stores falls into one of these kinds. Each needs something different.

| Kind | Example | An older Bond reading newer data | What it takes |
|---|---|---|---|
| **Additive field** | `origin` on history entries | ignores it | nothing. Readers must treat it as optional |
| **New value of a known field** | a new citation kind, locator type or format | raises | read support one release before write support ([mixed fleets](#mixed-fleets)) |
| **Changed meaning or shape** | a field's type changes, a value is split in two | misreads or raises | a new value format; readers keep both |
| **Key layout** | new key shapes, prefixes or partitioning | can't find anything | [migration by copy](#migration-by-copy), or reading both layouts |
| **Rules** | stricter normalization, a new validator check | verifies history wrongly | [rules versions](#rules-versions) |
| **Identity** | how ids are hashed, canonical JSON, a locator's `to_dict` | every id differs | [never in place](#identity) |

## Design

### A schema marker in every store

Bond reserves a key outside every workspace and records its store format there:

```python
("bond", "format", seq) -> {"format": 2, "min_reader": 2, "at": "..."}
```

- **Write-once, like everything else.** The marker is an entry list, and the latest entry is the store's current format. Raising it creates entry `seq + 1`, so the `Store` protocol needs nothing new and every adapter gets the check for free.
- **`format`** is the newest value and key format any Bond has written to this store.
- **`min_reader`** is the oldest Bond store format that can still read it. It is raised once a change writes data that older readers can't read.
- **Opening a store:**
  - If there is no marker, a fresh store gets one. A store with Bond keys but no marker is format 1, which is today's layout.
  - If the marker's `min_reader` is newer than this Bond's store format, refuse with "this store needs Bond ≥ …".
  - If the marker's `format` is older than this Bond's oldest supported format, refuse with "run `bond migrate`".
  - Otherwise, open it.
- **Raising the marker** is an explicit host action (`bond upgrade-store`), never a side effect of opening the store. A new release must not lock out older processes that are still running.
- **SQL's `bond_schema` table** stays as the guard for table shapes, which are the adapter's own business. Bond's store format moves to the marker. The move is format 1 → 2, and it is the last change that is refused rather than read.

### Formats in values

Every stored value gains a format field, `"f": n`. A value without one is format 0, which is everything written today.

- Each kind of value (workspace, record, name claim, history entry, title, marker) has its own format number, so one kind can change without touching the others.
- Readers dispatch on `(kind, f)` and **keep every reader inside the support window**. Records are write-once and never rewritten, so a store can hold years of format 0 records next to new ones.
- Writers always write the newest format the store's marker allows.

### Mixed fleets

Several processes share a store (`docs/storage.md`), so during a deploy an older and a newer Bond run against the same keys. Any change an older reader can't read goes out in two releases:

1. **Release N reads the new form and still writes the old one.** It is safe to run beside N−1.
2. **Once no N−1 is left, the host runs `bond upgrade-store`.** This raises the marker's `format`, and its `min_reader` to N, so a stray older process refuses the store instead of misreading it.
3. **Release N+1 writes the new form when the marker allows it.** With an older marker it keeps writing the old form, so upgrading the code and upgrading the store stay separate steps.

Additive fields skip all of this.

### Identity

Version and citation ids are the one thing a host copies out of Bond and keeps: in its own events, in determinations, in anything that cites. So:

- **An id scheme, once used, is frozen.** Its domain tag, the canonical JSON in `ids.dumps`, every locator's `to_dict`, and the hashed fields never change meaning.
- **Records name their scheme.** A record gains `"id_scheme": "bond.version.v2"`, and each stored citation gains `"bond.citation.v1"`. That is additive, and absent means today's. `verify` recomputes each id with the scheme the record names, so history written under an old scheme still verifies.
- **A new scheme is added beside the old one.** New versions use it. Old versions keep their ids and their scheme, and support walks across both, since a support names a version by its id whatever scheme made it. Bond never rehashes a store.
- **Ids stay bare hex strings.** The domain tags already keep schemes apart inside the hash, and recording the scheme in the record makes it readable. A visible prefix on every id would lengthen every handle and buy nothing more. If a future scheme changes the hash length or function, its ids get a visible prefix then, and today's stay as they are.
- **Hosts store full version ids, never handles.** A handle is an id prefix that is unique in its workspace now and may stop being unique as the workspace grows.

### Rules versions

A version was accepted under the rules of the Bond that wrote it. Re-checking it under newer rules answers a different question.

- **Records name their rules.** A record gains `"rules": n`, a single number Bond raises whenever `check`, a kind validator or normalization changes what is accepted. It also gains the policy it was checked under, which is the same policy form `WritePlan.to_json` already writes.
- **`verify` checks each version under the rules and policy recorded with it.** Bond keeps old rule implementations only while they are inside the support window. A version whose rules this Bond no longer carries is reported as `rules_unknown`, never as invalid.
- **`verify(..., rules="current")`** keeps today's behaviour as an explicit option: "would this still be accepted?"
- Hashes and integrity checks are not rules. They are identity, and never change.

### Migration by copy

A key layout change can't be made in place, because nothing in a store is ever updated. It is made by copying:

```python
bond.migrate(source: Store, target: Store, workspace: str | None = None) -> MigrationReport
```

- **What it does.** It reads every key of a workspace (or of every workspace) through the old format's readers. It then creates the corresponding keys in the target through the new format's writers, and writes the target's marker last.
- **Resumable.** Every write is create-if-absent, so a migration that crashed is run again from the start and skips what it already did.
- **Checked.** At the end it runs `verify` on every head and compares document histories and citations on both sides. The report lists anything that differs.
- **Blobs are not copied.** They are addressed by content hash and shared, so the old and new stores can point at the same bucket.
- **The old store stays readable** until the host deletes it, so a host can migrate one workspace at a time and roll back by switching stores.
- **Reading both layouts** (the new one first, falling back to the old) is an alternative for changes where old data can simply age out. It is chosen per change, in that change's design.
- **`bond migrate`** in the CLI wraps this for `bond.local` homes and for hosts with a DSN.

### Plans

`WritePlan.to_json` is meant to be stored and committed later, possibly by another process or another release.

- `from_json` reads every plan format inside the support window, not just the current one.
- Committing an old plan checks it again under the **current** rules. A plan is a proposal, not history, so it must meet today's rules to be filed.
- Hosts that keep plans as an audit record (Antelope's stem events) should treat the resulting **version id** as the durable reference. The plan is a means to commit, not the record of what was committed.

### Support window

- **Before 1.0:** as today. A store from a different format is refused, with `bond migrate` where one exists.
- **From 1.0:**
  - Every Bond in a major version reads every store format written by that major version and the previous one.
  - Key layout changes only happen in a new major version, and ship with `migrate`.
  - Identity schemes are never dropped from `verify`.
- The window is published in `docs/storage.md`, beside "What Bond stores".

### Tests

- **Golden stores.** `tests/golden/<format>/` holds a small workspace dumped as key/value pairs plus its blobs, captured from each released store format. It covers documents, an import, a transcription, a retitle and a lost race.
  - Every test run opens each golden store and checks that reads, search, trace and `verify` give the recorded answers.
  - It also migrates each golden store to the current format and checks the result.
- **Mixed fleets.** A test opens a store with marker `min_reader` above the running format and checks that Bond refuses it with the "needs Bond ≥" error, writing nothing.
- **Identity.** Fixed vectors pin each id scheme's output for known inputs, so a change to canonical JSON or a locator's `to_dict` fails a test instead of silently changing ids.
- **Conformance.** `bond.testing.conformance` adds the marker and `migrate` to what it checks for an adapter.

## Rollout

Each step is a separate change, and each leaves `main` working:

1. **Identity vectors.** Pin today's ids in tests. No behaviour change, and it guards everything after.
2. **`id_scheme`, `rules` and policy in records.** Additive. `verify` uses them when present and today's values when absent.
3. **Formats in values.** Additive. Readers treat a missing format as 0.
4. **The store marker** and the format 1 → 2 change. This is the last refused change: SQL homes move with `bond migrate`, the others start fresh.
5. **Golden stores** for format 2. From here, every store format change adds one.
6. **`migrate`** and `bond migrate`, with the conformance additions.
7. **Rules versions in `verify`**, with `rules="current"`.
8. **Multi-format plan reading.**
9. **Publish the support window** at 1.0.

Steps 1–3 should land before Antelope stores its first real version id.

## Open questions

- **Rules granularity.** Is one rules number enough, or should each kind validator carry its own, so a change to `quote` doesn't mark paraphrases as checked under new rules?
- **How long a window?** "This major and the previous" assumes a few majors over the life of a case. Prior-auth cases may need to verify years later, so identity and rules may need to be readable longer than values.
- **Who are the hosts?** If every host is run by the people who build Bond, the window can be shorter and migrations run by us. If customers host Bond, it must be generous and `migrate` must be safe to run unattended.
- **Compacting old formats.** Should `migrate` also be the way to rewrite a store's format 0 records into the current format in a new store, so a long-lived store can drop old readers?
- **Marker scope.** One marker per store, or one per workspace, so workspaces in one store can migrate at different times?
