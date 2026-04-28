# Partial Task Regeneration

**Base revision:** `534419fd6bcad69d3da799f5d974d211d0c52c71` on branch `main` (as of 2026-04-27T21:45:31Z)

## Summary

Today, `Optio.resync()` regenerates the full task list: it calls the user-supplied `get_task_definitions(services)` callback, replaces every Mongo process record, rebuilds the executor's task registry, and re-syncs every cron schedule. There is no way to refresh a subset.

This design adds an optional **metadata filter** that scopes resync to tasks whose `TaskInstance.metadata` matches the filter. Out-of-scope tasks, registry entries, and scheduler jobs are left untouched.

The change is threaded end-to-end: optio-api HTTP body → Redis stream payload → optio-core consumer → `_sync_definitions` → store / executor / scheduler. Each layer self-scopes using its own knowledge of metadata.

## Motivation

A consumer of optio-core may have hundreds of task definitions distributed across logical groups (e.g. `metadata.group = "ingest"`, `"etl"`, `"reports"`). A change in one group should not require the user-supplied callback to recompute every task in every group, nor should it churn the schedule of unrelated jobs. Today it does.

## Non-goals

- Richer filter expressions (regex, `$in`, ranges). Future work — see "Future evolution".
- Selective regeneration by `process_id` list, prefix, or predicate function. Filter is the chosen mechanism.
- Distributed coordination between concurrent resyncs. Per-row last-writer-wins (current behavior) is sufficient.
- Backwards compatibility for the `get_task_definitions` callback signature. The package is pre-1.0; existing callbacks must be updated.

## Filter type

A new type alias is introduced in both languages so the implementation can evolve later without breaking callers:

```python
# optio-core/src/optio_core/models.py
from typing import TypeAlias, Any
ProcessMetadataFilter: TypeAlias = dict[str, Any]
```

```typescript
// optio-api/src/types.ts
export type ProcessMetadataFilter = Record<string, unknown>;
```

For this iteration, semantics is **flat AND-equality**: the filter matches a task's metadata if every key/value in the filter equals the corresponding entry in `metadata`. An empty dict (`{}`) is treated as "no filter" (full sync), same as `None`.

A helper lives in `models.py` (imported by `executor.py` and `scheduler.py`):

```python
def matches_filter(
    metadata: dict[str, Any],
    filter: ProcessMetadataFilter | None,
) -> bool:
    if not filter:
        return True
    return all(metadata.get(k) == v for k, v in filter.items())
```

## End-to-end data flow

```
HTTP POST /api/processes/resync
  body: { clean?: boolean, metadataFilter?: ProcessMetadataFilter }
  └─> adapter (fastify | express | nextjs-app | nextjs-pages)
      └─> handlers.resyncProcesses(redis, db, prefix, clean, metadataFilter)
          └─> publisher.publishResync(redis, db, prefix, clean, metadataFilter)
              └─> redis.xadd(stream, '*', 'type', 'resync',
                                          'payload', JSON.stringify({clean, metadataFilter}))

[optio-core consumer]
  CommandConsumer dispatches "resync" to lifecycle._handle_resync
    └─> _sync_definitions(metadata_filter)
          ├─> get_task_definitions(services, metadata_filter)   [user callback]
          ├─> filter returned tasks: drop any whose metadata
          │    does not match `metadata_filter` (no-op when filter is None)
          ├─> upsert_process(...)        for each in-scope task
          ├─> remove_stale_processes(valid_ids, metadata_filter) [Mongo]
          ├─> executor.register_tasks(tasks, metadata_filter)
          └─> scheduler.sync_schedules(tasks, metadata_filter)
```

If `metadata_filter` is `None` (or empty), every layer is a no-op wrapper around the existing full-sync logic.

**Framework-side input filter:** After the callback returns, `_sync_definitions` discards any task whose `metadata` does not match `metadata_filter`. This makes the partial-sync contract symmetric: a callback may either honor the filter (returning the subset directly) or ignore it (returning its full list); in both cases only in-scope tasks reach upsert / register / schedule. Callback authors are therefore free to ignore `metadata_filter` entirely if they prefer — the framework guarantees the input to downstream layers is in-scope.

## Component changes

### `optio-core/src/optio_core/models.py`

- Add `ProcessMetadataFilter` type alias.
- Add `matches_filter(metadata, filter)` helper.
- Update the documented type of `get_task_definitions`:

```python
get_task_definitions: Callable[
    [dict[str, Any], ProcessMetadataFilter | None],
    Awaitable[list[TaskInstance]],
]
```

### `optio-core/src/optio_core/lifecycle.py`

`Optio.resync` and `_sync_definitions` accept an optional filter. `_handle_resync` reads `metadataFilter` from the Redis payload.

```python
async def resync(
    self,
    clean: bool = False,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    await self._handle_resync({"clean": clean, "metadataFilter": metadata_filter})

async def _handle_resync(self, payload: dict) -> None:
    clean = payload.get("clean", False)
    metadata_filter = payload.get("metadataFilter") or None  # treat {} as None
    if clean:
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        if metadata_filter:
            mongo_query: dict[str, Any] = {"parentId": None}
            for k, v in metadata_filter.items():
                mongo_query[f"metadata.{k}"] = v
            deleted = await coll.delete_many(mongo_query)
        else:
            deleted = await coll.delete_many({})
        logger.info(f"Nuked {deleted.deleted_count} process records")
    await self._sync_definitions(metadata_filter)

async def _sync_definitions(
    self,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    if self._config.get_task_definitions is None:
        return
    tasks = await self._config.get_task_definitions(
        self._config.services, metadata_filter,
    )
    # Framework guarantees only in-scope tasks reach downstream layers,
    # so callback authors may ignore `metadata_filter` if they prefer.
    if metadata_filter:
        tasks = [t for t in tasks if matches_filter(t.metadata, metadata_filter)]
    for task in tasks:
        await upsert_process(self._config.mongo_db, self._config.prefix, task)
    valid_ids = {t.process_id for t in tasks}
    removed = await remove_stale_processes(
        self._config.mongo_db, self._config.prefix,
        valid_ids, metadata_filter,
    )
    if removed:
        logger.info(f"Removed {removed} stale process records")
    self._executor.register_tasks(tasks, metadata_filter)
    await self._scheduler.sync_schedules(tasks, metadata_filter)
    scope = "(all)" if not metadata_filter else f"(filter={metadata_filter})"
    logger.info(f"Synced {len(tasks)} task definitions {scope}")
```

The `self._tasks` field is removed — it is no longer a single source of truth once partial sync is allowed. The executor and scheduler hold the authoritative per-process state.

### `optio-core/src/optio_core/store.py`

`remove_stale_processes` gains an optional filter:

```python
async def remove_stale_processes(
    db, prefix,
    valid_ids: set[str],
    metadata_filter: ProcessMetadataFilter | None = None,
) -> int:
    coll = db[f"{prefix}_processes"]
    query: dict[str, Any] = {
        "processId": {"$nin": list(valid_ids)},
        "parentId": None,  # only sweep root processes (preserve sub-trees)
    }
    if metadata_filter:
        for k, v in metadata_filter.items():
            query[f"metadata.{k}"] = v
    result = await coll.delete_many(query)
    return result.deleted_count
```

When `metadata_filter` is `None`, the Mongo query is identical to today's.

### `optio-core/src/optio_core/executor.py`

The task registry stores the full `TaskInstance` instead of just the execute function, so the executor can self-filter by metadata.

```python
self._task_registry: dict[str, TaskInstance] = {}

def register_tasks(
    self,
    tasks: list[TaskInstance],
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    if not metadata_filter:
        self._task_registry = {t.process_id: t for t in tasks}
        return
    new_ids = {t.process_id for t in tasks}
    for pid in list(self._task_registry):
        existing = self._task_registry[pid]
        if matches_filter(existing.metadata, metadata_filter) and pid not in new_ids:
            del self._task_registry[pid]
    for t in tasks:
        self._task_registry[t.process_id] = t
```

The single existing call site that reads the registry (`launch_process`, currently `executor.py:64`) becomes:

```python
task = self._task_registry.get(process_id)
return await self._execute_process(
    proc, task.execute if task else None, resume=resume,
)
```

`adhoc_define` (lifecycle.py:165) and `_cleanup_ephemeral` (executor.py:41) both currently store/remove `TaskInstance.execute`; they update to store/remove `TaskInstance` (or use `.execute` accessor) consistently.

### `optio-core/src/optio_core/scheduler.py`

Track the full `TaskInstance` per scheduled job so jobs can be filtered by metadata. The set `_job_ids` is replaced by a dict `_jobs`.

```python
self._jobs: dict[str, TaskInstance] = {}  # job_id -> task

async def sync_schedules(
    self,
    tasks: list,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    if not self._scheduler:
        return

    new_ids = {f"sched_{t.process_id}" for t in tasks if t.schedule}

    # Remove in-scope jobs that are no longer present in `tasks`,
    # OR (full sync) every existing job.
    for job_id in list(self._jobs):
        existing = self._jobs[job_id]
        if metadata_filter is None:
            should_remove = True
        else:
            should_remove = (
                matches_filter(existing.metadata, metadata_filter)
                and job_id not in new_ids
            )
        if should_remove:
            try:
                await self._scheduler.remove_job(job_id)
            except Exception:
                pass
            del self._jobs[job_id]

    # Add or replace jobs for the (possibly partial) `tasks` list.
    for task in tasks:
        if not task.schedule:
            continue
        job_id = f"sched_{task.process_id}"
        if job_id in self._jobs:
            try:
                await self._scheduler.remove_job(job_id)
            except Exception:
                pass
        try:
            from apscheduler.triggers.cron import CronTrigger
            trigger = CronTrigger.from_crontab(task.schedule)
            await self._scheduler.add_job(
                self._launch_fn,
                trigger=trigger,
                id=job_id,
                args=[task.process_id],
            )
            self._jobs[job_id] = task
            logger.info(f"Scheduled {task.process_id}: {task.schedule}")
        except Exception as e:
            logger.error(f"Failed to schedule {task.process_id}: {e}")
```

### `optio-api/src/types.ts`

Add `ProcessMetadataFilter` type alias.

### `optio-api/src/handlers.ts`

```typescript
export async function resyncProcesses(
  redis: Redis,
  database: string,
  prefix: string,
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  await publishResync(redis, database, prefix, clean, metadataFilter);
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

### `optio-api/src/publisher.ts`

```typescript
export async function publishResync(
  redis: Redis,
  database: string,
  prefix: string,
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<void> {
  const payload: { clean: boolean; metadataFilter?: ProcessMetadataFilter } = { clean };
  if (metadataFilter && Object.keys(metadataFilter).length > 0) {
    payload.metadataFilter = metadataFilter;
  }
  await redis.xadd(
    getStreamName(database, prefix),
    '*',
    'type', 'resync',
    'payload', JSON.stringify(payload),
  );
}
```

### Adapters: fastify, express, nextjs-app, nextjs-pages

Each adapter's `resync` handler updates its body type and forwards the new field:

```typescript
resync: async ({
  query,
  body,
}: {
  query: { database?: string; prefix?: string };
  body: { clean?: boolean; metadataFilter?: ProcessMetadataFilter };
}) => {
  // ...
  const result = await handlers.resyncProcesses(
    redis, database, prefix,
    body.clean ?? false,
    body.metadataFilter,
  );
  // ...
}
```

## Initial sync

The startup call in `Optio.init` (currently `lifecycle.py:106`) continues to invoke `_sync_definitions()` with no filter — initial sync is always a full sync. Partial regeneration is exclusively a runtime operation.

## Filter wire format

The filter is JSON-serialized inside the Redis stream payload. Filter values must be JSON-representable scalars or containers. The flat AND-equality semantics already restrict practical usage to scalars (string, number, boolean, null); this is documented but not statically enforced beyond the type alias.

## Edge cases

- **Empty filter `{}`** — treated as no filter (full sync). Documented; `matches_filter` already returns `True` for empty input. The publisher omits `metadataFilter` from the Redis payload when empty, so the consumer is symmetric.
- **Filter matches nothing** — callback returns `[]` (or any list whose tasks all fail the framework-side filter, which is then narrowed to `[]`). Stale removal scoped to filter deletes any in-scope record (intentional: the caller declared the group empty). Executor and scheduler drop in-scope entries.

- **Callback over-returns** — callback ignores the filter and returns its full task list. The framework-side input filter narrows this to in-scope tasks before any downstream layer runs; out-of-scope tasks returned by the callback are silently dropped. This is the expected migration path for callbacks that don't yet have per-group logic.
- **`clean=True` + filter** — `delete_many` is scoped to the filter. Then `_sync_definitions` re-imports the subset. Documented as "nuke and re-import for this group". `clean=True` without a filter retains current "nuke everything" semantics.
- **Concurrent resyncs** — disjoint filters touch disjoint Mongo rows / registry keys / job ids; safe in parallel. Overlapping filters: per-row last writer wins (current behavior). No additional locking introduced.
- **Callback raises** — exception propagates as today; partial mutation (some upserts succeeded before failure) is possible. Caller can re-run idempotently.
- **`adhoc_define` interaction** — adhoc tasks live in the same `_task_registry`, but their `metadata` is set by the caller. A partial resync may legitimately drop an adhoc task whose metadata happens to match the filter and whose `process_id` is not in the callback's returned list. This is intentional and consistent: the callback is the source of truth for in-scope tasks.

## Testing

### optio-core (Python, pytest, mongodb-memory-server)

1. `test_resync_full_unchanged` — `metadata_filter=None` reproduces current behavior end-to-end.
2. `test_matches_filter_empty_dict_is_full` — `{}` and `None` behave identically.
3. `test_resync_partial_subset_upserted` — only matching tasks are upserted into Mongo.
4. `test_resync_partial_out_of_scope_untouched` — non-matching DB rows survive a partial resync.
5. `test_resync_partial_in_scope_stale_removed` — an in-scope DB row absent from the callback result is deleted.
6. `test_resync_partial_clean_scoped_delete` — `clean=True` + filter deletes only in-scope rows.
7. `test_register_tasks_partial` — registry merges; only in-scope absent entries are dropped.
8. `test_register_tasks_stores_full_taskinstance` — `_task_registry[id]` returns a `TaskInstance`, not a callable; `launch_process` lookup still works.
9. `test_sync_schedules_partial` — APScheduler jobs scoped (mock `_scheduler`).
10. `test_remove_stale_processes_with_filter` — Mongo query correctness for the scoped delete.

### optio-api (TypeScript, jest)

1. `publisher.test.ts` — payload includes `metadataFilter` when provided, omitted when absent or empty.
2. `handlers.test.ts` — `resyncProcesses` passes the filter through to `publishResync`.
3. Each adapter test (`fastify`, `express`, `nextjs-app`, `nextjs-pages`) — body `{ clean, metadataFilter }` is parsed and forwarded.

### Integration

No new integration tests. The existing end-to-end resync tests cover the `metadata_filter=None` path; per-layer unit tests cover the partial path.

## Migration

The `get_task_definitions` callback signature is breaking-changed. Callers must add a second parameter:

```python
# Before
async def get_task_definitions(services): ...

# After
async def get_task_definitions(services, metadata_filter=None): ...
```

A callback that ignores `metadata_filter` continues to work: it returns its full list, and `_sync_definitions` filters that list to in-scope tasks before any downstream layer (`upsert_process`, `register_tasks`, `sync_schedules`, `remove_stale_processes`) runs. No backwards-compat shim is added; the package is pre-1.0.

CHANGELOG entry to call this out.

## Future evolution

Reserved for later iterations:

- Richer filter operators (`$in`, `$ne`, range, regex). Would change `ProcessMetadataFilter` from a flat dict to a structured type; helper `matches_filter` updates accordingly.
- Resync-by-`process_id`-list, by prefix, or by predicate. Could be added as additional optional parameters to `resync`, but the metadata-filter route covers the common case.
- Per-task lifecycle hooks fired on partial resync (e.g. "this task is no longer scheduled, run a teardown"). Out of scope here.
