# Process collection indexes -- Design

**Base revision:** `7e49d201` on `main` (wire 0.5.3, 2026-10-09).

## Summary

optio-core makes sure, at every `init()`, that `{prefix}_processes` has the
indexes its own queries and optio-api's need, including the `expireAt` TTL
index. Two fixes make the restored TTL safe: a relaunch clears the previous
run's `expireAt`, and a child takes its parent's `ttlSeconds` so it expires with
its parent instead of outliving it.

## Why

On the excavator dev stack (2026-10-09) every process collection on the main
MongoDB has only the `_id` index:

- The engine looks a process up by `processId` about five times a second
  (several times per launch), and each lookup scans the whole collection:
  about 155 documents examined per lookup at 204 records. Lookups and deletes
  by `parentId` (every relaunch and dismiss) scan it too, and so do excavator's
  autopilot gate counts by `status.state` on every check. The cost grows with
  the collection.
- The `expireAt` TTL index is missing. Migration m004 created it once, only on
  the `*_processes` collections that existed when it ran; a database rebuilt
  later (as this one, after the 2026-09-24 wipe) or a prefix created later never
  gets it, so records with a TTL never expire there.

## Indexes

`store.ensure_process_indexes(db, prefix)` creates, on `{prefix}_processes`:

| Name | Keys | Options | Serves |
|---|---|---|---|
| `processId_1__id_-1` | `{processId: 1, _id: -1}` | | lookups by processId, newest first (engine launch, optio-api resolve / scope); `_id` covers the sort, without it the planner may walk `_id_` and filter instead |
| `parentId_1_order_1` | `{parentId: 1, order: 1}` | | children: delete_descendants, list_direct_children, cancel cascades, tree REST, roots_only counts |
| `rootId_1_depth_1_order_1` | `{rootId: 1, depth: 1, order: 1}` | | tree and multi-tree streams, `list_processes(root_id=)` |
| `status.state_1` | `{"status.state": 1}` | | `count_processes(states=)`, the startup reconcile, auto-resume |
| `originatingSessionId_1` | `{originatingSessionId: 1}` | | the session-events stream |
| `expireAt_ttl` | `{expireAt: 1}` | `expireAfterSeconds=0` | MongoDB's TTL monitor (same name and options as m004) |

`metadata.*` filters are application-specific and get no index from optio.

### When and how

- `Optio.init` calls it once, after the migrations and before
  `_reconcile_interrupted_processes` (whose `status.state` query then uses its
  index). `create_index` is a no-op for an index that already exists.
- An index with the same keys under another name (code 85,
  IndexOptionsConflict, or 86, IndexKeySpecsConflict) is logged as one warning
  naming the index; the remaining indexes are still created and `init` goes on.
  Any other error propagates and fails `init`.
- Only optio-core creates indexes. optio-api stays read-only (its architecture
  rule) and benefits from them.
- m004 is unchanged: harmless, and recorded as applied wherever it ran.

### Cost

Index builds do not block other operations; `init` waits for its own
collection's builds (milliseconds for hundreds of records). Inserts write every
index; an update writes index keys only for indexed fields it changes:
`status.state` two or three times a run, `originatingSessionId` once at start,
`expireAt` at the start (unset, below) and the end. Progress and log writes
touch no index.

## TTL fixes

### A relaunch clears `expireAt`

Today a relaunch keeps the previous run's `expireAt`, so a record relaunched
shortly before that time can be deleted by the TTL monitor while it runs.

`relaunch_reset` also `$unset`s `expireAt`, in the same single update. The run's
end sets it again (existing code, as do the late final write and the startup
reconcile). `dismiss` uses the same reset, so a dismissed (idle) record no longer
expires; dismiss resets a record to idle, and only terminal states carry a TTL.

### A child takes its parent's `ttlSeconds`

Children carry no `ttlSeconds`, so they never expire. Once the TTL deletes a
parent (excavator: a sync idle for 7 days), the children of its last run would
remain as orphans for good.

`create_child_process` gets a `ttl_seconds` argument and stores it as the
child's `ttlSeconds`; its callers pass the parent's (the executor from the
parent's own record, `adhoc_define` from the parent document it already
resolves). Since every child copies its parent's, a whole tree carries the
root's TTL. The child's own end write then sets its `expireAt` (no extra
write), so it
expires one TTL after it ended, at about the same time as its parent. A parent
that runs for longer than its TTL loses the records of children that ended more
than one TTL ago; no current user combines a TTL with such a long run (excavator
syncs run for seconds; agent sessions set no TTL).

Orphans that deployments with a working TTL index may already hold are left
alone; a cleanup would be a separate decision.

## Unchanged

The document schema, every field's meaning, the queries themselves, optio-api,
m004, `compute_expire_at` and the terminal-state writers' TTL handling.

## Testing

In `packages/optio-core/tests/` (real Mongo, no wall-clock dependence):

- `ensure_process_indexes` on a new prefix creates the six indexes with their
  keys, names and the TTL option; a second call changes nothing; a
  pre-existing index with the same keys as `processId_1__id_-1` under another name
  gives one logged warning naming it, the other five are created, no error.
- `Optio.init` on a new prefix leaves the six indexes in place.
- `explain` of the `processId` lookup and of the children-by-`parentId` query
  shows an index scan (`IXSCAN`), not `COLLSCAN`.
- A task with `ttl_seconds`: `expireAt` is set at its end; relaunched, it has no
  `expireAt` while running (checked while the body waits on an event) and gets
  one again at the next end. Dismiss removes it.
- A child (and a grandchild) under a root with `ttl_seconds` gets the root's
  `ttlSeconds` and its own `expireAt` at its end; under a root without one,
  neither.
- Not tested: MongoDB's TTL deletion itself (its monitor runs every 60 s; that is
  MongoDB's behaviour, and a test of it would depend on wall-clock time).
- Tests run from a separate worktree on the excavator host, not from the
  checkout the dev engine watches.

## Rollout

An optio-core patch release (wire, per the release cookbook), then excavator's
optio dependency bump. Indexes appear at the first engine start on the new
version: on the excavator dev stack when the commits land in the host checkout
the engine runs from (announced in topics.log first), elsewhere at the next
deploy. No manual step, no data migration.

## Measurement

On the excavator dev stack, before and after: a 30 s profiler sample of
`gm_processes` (documents examined per `processId` / `parentId` query: about
155 today, expected about 1), and the main MongoDB's `mongod` and container CPU
per minute.
