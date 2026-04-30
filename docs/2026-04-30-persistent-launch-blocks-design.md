# Persistent Launch Blocks ("Perma-Ban")

**Base revision:** `6ed273a9e55bfefe213708b90740462553b858bd` on branch `main` (as of 2026-04-30T17:51:02Z)

## Summary

Extend the existing in-memory launch-block mechanism in `LifecycleManager` so that selected blocks can be persisted to MongoDB and survive process restarts. Persistent blocks (informally "perma-bans") are loaded into memory at `LifecycleManager` initialization and matched against incoming launches via the existing `_check_launch_blocks` pipeline. A new admin operation removes them.

The feature is exposed on the existing public API by adding a `persist` flag (and a `reason` kwarg) to three functions and introducing one new function (`unblock_launches`). No Redis-stream commands are added.

## Motivation

Today, `LifecycleManager.block_launches(filter)` registers an in-memory block whose lifetime equals the caller's `async with` scope. After process restart, all blocks are lost. There is no persistent way to express "do not ever launch processes whose metadata matches this filter, until I explicitly say otherwise".

Operators need a durable form of this guard for situations such as:

- Banning a misbehaving tenant or process class until the underlying defect is resolved.
- Preventing reactivation of a flagged workload across deploys/restarts.

The existing `block_launches` primitive is already the correct shape; we extend it with persistence rather than introduce a parallel admin-only API.

## Public API

All additions live on `LifecycleManager` in `optio_core/lifecycle.py`. Signatures use keyword-only kwargs for the new options.

### Modified — `block_launches`

```python
async def block_launches(
    self,
    launch_filter: ProcessMetadataFilter,
    *,
    persist: bool = False,
    reason: str | None = None,
) -> AsyncIterator[None]:
```

Behavior:

- `persist=False` (default): unchanged — block is in-memory only and is removed on context exit.
- `persist=True`: a record is written to the persistent-blocks collection on entry, and the block remains active after the `async with` exits. The `__aexit__` does **not** remove the block from memory or from MongoDB.
- `reason` is stored on the persistent record. Ignored when `persist=False`.

### Modified — `group_cancel` and `group_cancel_and_wait`

```python
async def group_cancel(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
    *,
    persist: bool = False,
    reason: str | None = None,
) -> None:

async def group_cancel_and_wait(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
    *,
    persist: bool = False,
    reason: str | None = None,
) -> None:
```

Behavior:

- When `persist=True`, the launch block installed during the cancel sweep becomes persistent (record written, block survives the call).
- `persist=True` with `block_new_launches=False` raises `ValueError`. (A persistent block must originate from a real `block_new_launches=True` cycle; otherwise there is nothing to persist.)
- `reason` is stored on the persistent record. Ignored when `persist=False`.

### New — `unblock_launches`

```python
async def unblock_launches(
    self,
    filter: ProcessMetadataFilter,
) -> int:
```

Behavior:

- Removes every record from the persistent-blocks collection whose `filter` is equal to `filter` by exact dict equality.
- Removes every entry from the in-memory block dict whose filter is equal to `filter` by exact dict equality, regardless of how it was installed (persistent or transient context-manager-held).
- Returns the count of in-memory entries removed.
- Transient context-manager holders' `__aexit__` already tolerates a missing token (`pop(token, None)`); external removal mid-scope is therefore safe.

There is no public list/inspect API. Operators may query the MongoDB collection directly when audit is needed.

### Modified — `LaunchBlocked`

The exception class itself is unchanged. The message constructed at the raise site in `lifecycle.py` is extended: when the matched block carries a non-null `reason`, append `; reason={reason}` to the existing message format. When `reason` is null, the message is unchanged from today.

## Behavior and Semantics

### Load on initialization

During `LifecycleManager` async initialization, before `run()` accepts any work:

- All records in the persistent-blocks collection are read.
- Each record is registered as an in-memory block (fresh UUID token, filter and reason populated from the record).
- An empty or missing collection produces an empty load. No migration is required; MongoDB creates the collection lazily on first insert.

After load completes, the manager behaves identically to today with respect to launch matching: `_check_launch_blocks` walks the in-memory dict and raises `LaunchBlocked` on a match.

### Write-through on persist

For `block_launches(F, persist=True)` and `group_cancel*(F, ..., persist=True)`:

- Look up an existing record by exact-filter equality on the `filter` field.
- **No existing record** — insert `{ filter: F, createdAt: utcnow, reason }`.
- **Existing record** — dedupe (no second record). Reason update rule:
  - If existing `reason` is non-null AND the new `reason` is non-null, set the record's `reason` to `f"{existing} AND {new}"`.
  - If either side is null, keep the existing record's `reason` unchanged.
- An in-memory entry is registered for the call regardless of dedupe outcome. Multiple in-memory entries with the same filter are tolerated, matching today's `block_launches` semantics for overlapping calls.

### Unblock

`unblock_launches(F)`:

- Deletes every persistent record with `filter == F` exactly.
- Removes every in-memory block entry whose filter equals `F` exactly.
- Returns total in-memory entries removed.

### `persist` + `block_new_launches` constraint on `group_cancel*`

`persist=True` with `block_new_launches=False` raises `ValueError`. A persistent block can only be installed in tandem with the existing block-new-launches code path of `group_cancel*`; `persist` does not, by itself, install or persist a block.

### Concurrency

Best-effort dedupe. Two concurrent `persist=True` calls with the same filter may both observe "no existing record" and both insert. Functionally harmless: matching launches are blocked either way, and `unblock_launches(F)` removes all matching records and all matching in-memory entries.

### Restart semantics

After a restart, persistent blocks are reloaded from the collection and become active before any launch can be admitted. Transient blocks (those installed without `persist=True`) are gone, as today. Reloaded blocks have new UUID tokens.

## Persistence Contract

### Collection

Name: `{prefix}_launch_blocks` — using the same `prefix` configuration that names the existing `{prefix}_processes` collection.

### Record schema

```
{
  _id:       ObjectId,
  filter:    object,    // ProcessMetadataFilter — dict[str, Any], stored as native BSON
  createdAt: ISODate,   // UTC, set at first insert; never updated on dedupe
  reason:    string | null
}
```

### Indexes

None required. The collection is expected to be small (admin-managed, low cardinality). Lookups at upsert and delete time use exact-filter equality and are linear in collection size, which is acceptable.

### Lifecycle and migration

The collection is created lazily by MongoDB on first insert. There is no migration. There is no TTL — records persist until an explicit `unblock_launches` call. Existing deployments without the collection start with zero persistent blocks on first init.

### Concurrent safety

The design relies on MongoDB's per-document atomicity for insert and delete. Best-effort dedupe is acceptable (see Concurrency above).

## Out of Scope

- **Redis-stream command exposure.** The current `group_cancel*` and `block_launches` operations are Python-API-only; this work preserves that scope. Adding fire-and-forget Redis commands would be straightforward, but `*_and_wait` operations would need a reply protocol that does not exist today, so we keep the entire surface Python-only for now.
- **List/inspect API.** Operators inspect MongoDB directly when audit is needed.
- **Removal by anything other than exact filter equality** (e.g., subset match, ID-based removal).
- **TTL or scheduled expiry** of persistent blocks.

## Test Strategy

A new file `tests/test_persistent_launch_blocks.py` covers:

### Store-level (pure-function unit tests)

- `load_all` returns all rows; an empty collection returns the empty list.
- `upsert_block` first call inserts; second call with the same filter dedupes (no new row, count = 1).
- Reason concatenation: existing non-null + new non-null → `"existing AND new"`; either side null → reason unchanged.
- `delete_by_filter` deletes all matching rows; returns the count; non-matching is a no-op.

### LifecycleManager-level (integration with mongo)

- After init with persistent records present, matching launches are blocked from the start.
- `block_launches(F, persist=True)` exit does **not** remove the block; the record is present after `async with` exits.
- `block_launches(F, persist=True, reason="x")` writes a record with `reason="x"`; the resulting `LaunchBlocked.args[0]` contains `; reason=x`.
- A second `block_launches(F, persist=True, reason="y")` produces a single record with `reason="x AND y"`.
- `group_cancel(F, block_new_launches=True, persist=True, reason="z")` cancels matching processes, installs a persistent block, writes a record with `reason="z"`, and running processes terminate.
- `group_cancel(F, persist=True, block_new_launches=False)` raises `ValueError`.
- `group_cancel_and_wait(F, ..., persist=True)` matches `group_cancel` semantics plus wait.
- `unblock_launches(F)` removes the record and all in-memory entries; subsequent launches matching `F` succeed; the returned count is correct.
- Restart simulation: install a persistent block, dispose the manager, instantiate a new manager against the same database — the block is reloaded and matching launches remain blocked.
- A transient and a persistent block with the same filter coexist; `unblock_launches(F)` removes both.

The existing `tests/test_group_cancel.py` is left untouched; persist-flag cases for `group_cancel*` live in the new file for cohesion with the rest of this feature.
