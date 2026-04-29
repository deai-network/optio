# Design: Launch Guard

**Date:** 2026-04-29
**Base revision:** `191fd39f5880a7c13f06ed0daa1b174d1cb8703c` on branch `main` (as of 2026-04-29T18:38:03Z)
**Scope:** Add a launch-guard mechanism to optio-core that lets callers temporarily reject any launch whose task metadata matches a registered filter. Used by downstream consumers (e.g. excavator's project-delete handler) to prevent new processes from being created during teardown windows.

## Motivation

When a downstream consumer is in the middle of tearing down a logical scope (e.g. cancelling all running tasks for a project, deleting the project's domain data, then dropping the project record), there is a window during which **new** processes for that scope must not start. Sources of new launches:

- UI-triggered launches via API endpoints (Redis `launch` commands).
- Parent tasks spawning children via `ProcessContext.run_child` mid-execution.
- Other Redis command handlers that internally call `Optio.launch()` or `optio_core.launch()` (e.g. excavator's `extract-metadata` command handler creates and launches an ad-hoc task).

Without a guard, a launch that arrives during teardown either bypasses cancellation entirely (its process record is created **after** `gracefully_terminate_tasks` finished its sweep) or starts execution against domain data that is being cascade-deleted.

A guard at the launch layer closes the window. It is a single primitive in optio-core that any consumer with a teardown sequence can wrap around its critical section.

## Project-agnostic by design

optio-core has no notion of "project", "tenant", or any other domain concept. The guard operates purely on `ProcessMetadataFilter` — the existing flat AND-equality dict type already used by `resync`, `list_processes`, and `gracefully_terminate_tasks`. Excavator passes `{"project": slug}`; another consumer can pass `{"tenant": id}` or `{"customer": x, "shard": y}`. optio-core never names the keys.

Match check uses the existing `matches_filter` helper in `optio_core.models`.

## Public API

A single primitive on `Optio`:

```python
class Optio:
    def block_launches(
        self,
        filter: ProcessMetadataFilter,
    ) -> AsyncContextManager[None]:
        """Register a launch block for the duration of the context.

        While the returned context manager is active, any attempt to
        launch or define a process whose metadata matches `filter`
        raises LaunchBlocked.

        Multiple concurrent block_launches() calls — with overlapping
        or identical filters — stack independently. Each context owns
        its own block; exiting one does not lift another's block.

        An empty filter {} matches every task metadata — registering
        it blocks all launches. Allowed; useful as a panic-stop, but
        rarely needed.
        """
```

Usage:

```python
async with optio_core.block_launches({"project": slug}):
    await optio_core.gracefully_terminate_tasks({"project": slug})
    await db["sources"].delete_many({"projectId": slug})
    ...
```

No public token API. Manual register/unregister primitives are deliberately out of scope: callers needing more flexibility can use `contextlib.AsyncExitStack` to manage multiple `block_launches` lifetimes from one place.

## New exception

In `optio_core.models`:

```python
class LaunchBlocked(RuntimeError):
    """Raised when a launch is rejected by an active launch block.

    The exception message includes both the matching filter and the
    task metadata so the rejection is traceable from logs alone.
    """
```

`RuntimeError` is the right base: a launch arriving inside a block is a runtime condition, not a programmer error or a hard system failure.

Exported from `optio_core/__init__.py`.

## Internal storage

```python
class Optio:
    def __init__(self):
        ...
        self._launch_blocks: dict[uuid.UUID, ProcessMetadataFilter] = {}
```

In-memory dict, keyed by `uuid.UUID`. Token uniqueness lets two concurrent registrations of the same filter coexist without ref-counting collisions: each `async with` owns its own token; both must exit before the project is unblocked.

State is lost on engine restart. This matches the deadline-cancel design's single-engine assumption: a crashed teardown handler leaves no lingering block (Python's context-manager protocol runs `__aexit__` on exception too; only an abort like `os._exit` skips it, in which case the engine is dead and the dict goes with it).

The Redis `delete-project` command (or whichever command drives the teardown) remains in the stream until ACKed. If a handler crashes mid-block, the consumer redelivers on the next engine start; the new handler re-registers the block from scratch. No persisted-block recovery needed.

## Internal helper

A single private method, called from every launch-path doorway:

```python
def _check_launch_blocks(self, metadata: dict | None) -> None:
    if not self._launch_blocks:
        return  # fast path
    md = metadata or {}
    for filter in self._launch_blocks.values():
        if matches_filter(md, filter):
            raise LaunchBlocked(
                f"Launch blocked by filter {filter}; "
                f"task metadata={md}"
            )
```

The fast path (no blocks registered) is a single dict-empty check — negligible per-call cost. When blocks are present, the loop iterates all registered filters; in practice the dict size is 0 or 1 during teardown.

## Where the check fires

Four logical doorways cover every entry into "a process is about to be defined or started". Two delivery models matter for where the check is placed:

- **Synchronous-await callers** (`Optio.launch_and_wait(pid)`, `Optio.adhoc_define(task)`, `ProcessContext.run_child`): the caller awaits the call. If the check raises `LaunchBlocked`, the caller observes the exception and can react.
- **Fire-and-forget callers** (`Optio.launch(pid)` — schedules an asyncio Task and returns; Redis `launch` commands consumed by `_handle_launch` and dispatched to `_handle_launch_by_process_id`): nobody is awaiting the inner Task. An exception inside the Task becomes an unhandled-Task-exception, observable only in logs at process exit. To surface rejections cleanly, fire-and-forget paths run the check **synchronously, before scheduling the asyncio Task**.

The four checkpoints:

1. **`Optio.launch(pid)` / `Optio.launch_and_wait(pid)`** — both methods run `_check_launch_blocks(task.metadata)` synchronously before scheduling the executor's Task. Look up the TaskInstance via `self._executor._task_registry.get(pid)`. If unknown to the registry, no metadata to match — fall through and let the existing "no execute function found" failure path run inside the executor.

2. **`Optio.adhoc_define(task, ...)`** — pre-launch process record creation. Matches on `task.metadata`. Check fires before any DB write.

3. **`Executor.execute_child(parent_ctx, ...)`** — child process spawning via `ProcessContext.run_child`. Children inherit `parent_ctx.metadata` (current behaviour: `metadata=parent_ctx.metadata` at executor.py line 219). Match on the parent's metadata. The parent task observes a `LaunchBlocked` exception from its `run_child` call; existing parent-side error handling kicks in.

4. **`Optio._handle_launch(payload)`** — the Redis launch command consumer. Synchronously runs the check, catches `LaunchBlocked`, logs a warning at `WARNING` level, and ACKs the message. The exception is not allowed to bubble into the stream consumer.

Implementation sketch for the consumer path:

```python
async def _handle_launch(self, payload: dict) -> None:
    process_id = payload.get("processId")
    if not process_id:
        return
    task = self._executor._task_registry.get(process_id)
    if task is not None:
        try:
            self._check_launch_blocks(task.metadata)
        except LaunchBlocked as e:
            logger.warning(f"Launch rejected: {e}")
            return  # consumer ACKs and moves on
    resume = payload.get("resume", False)
    await self._handle_launch_by_process_id(process_id, resume=resume)
```

Sketch for the synchronous Optio.launch wrapper:

```python
async def launch(self, process_id: str, resume: bool = False) -> None:
    task = self._executor._task_registry.get(process_id)
    if task is not None:
        self._check_launch_blocks(task.metadata)  # may raise LaunchBlocked
    asyncio.create_task(self._executor.launch_process(process_id, resume=resume))
```

A defensive secondary check inside `Executor.launch_process` is **not** added: the synchronous checks in entries 1, 2, 3, 4 are exhaustive. Adding another check inside `launch_process` would create a path where a block registered between "check passed" and "Task started" would silently swallow the launch — different from the public contract that `LaunchBlocked` is the explicit rejection signal. It is acceptable that a launch which passes the synchronous check but is then concurrent with a block registration completes normally (the guard is a doorway check, not a full-flight escort — see "Concurrency / nesting" below).

### Visibility of rejections to publishers

For Redis-stream-based publishers (the typical excavator flow: HTTP API publishes a `launch` command, returns 200 / 202 to the client immediately), a rejection downstream is invisible to the publisher — by design of the fire-and-forget delivery model, not as a flaw of the guard. Operators discover rejections via the `WARNING` log entry. If a downstream UX wants to surface rejections to end users, that is a separate problem (e.g. correlation ID + status lookup) outside this spec.

## Filter union semantics

A launch is blocked iff `matches_filter(task.metadata, filter)` is True for **any** registered filter. There is no AND of all filters — that would mean only a launch matching every block is rejected, the opposite of what the guard exists to do.

Empty filter `{}` matches every metadata via `matches_filter`. Registering `{}` blocks every launch — a panic-stop. Allowed.

## Concurrency / nesting

asyncio is single-threaded; reads and writes of `self._launch_blocks` are serialized by the event loop. No locks needed.

Two patterns work the same:

- Two independent coroutines registering the same filter: each gets its own uuid token, both contribute to the block, both must exit before the union is empty.
- One coroutine re-entering: nested `async with` adds a second token. Both exits required.

If a registered filter exits while a launch is mid-flight (the `_check_launch_blocks` returned, then the registration was removed before the launch finished its setup): no harm. The launch was checked at the doorway and is allowed to proceed. The guard is a doorway check, not a full-flight escort.

## Observability

- `LaunchBlocked` message includes both the matching filter and the rejected task metadata for log searchability.
- The Redis consumer wrapper logs at `WARNING` level on every rejection.
- No metric or counter is introduced. If rejection volume becomes interesting (e.g. as evidence that callers are spamming during teardowns), a counter can be added later.

## Tests

New test file: `~/deai/optio/packages/optio-core/tests/test_launch_guard.py`.

Required cases:

1. **Block matches direct `launch`.** Register `block_launches({"project": "p1"})`; call `Optio.launch(pid)` for a task with metadata `{"project": "p1", "sourceId": "s1"}`; assert `LaunchBlocked` is raised synchronously; verify the process state did not transition out of its pre-launch state (i.e. the executor was never reached).
2. **Block matches `adhoc_define`.** Register the same block; call `Optio.adhoc_define(task)` with matching metadata; assert `LaunchBlocked` raised; no process record created.
3. **Block matches `run_child`.** A parent task with matching metadata calls `ctx.run_child(...)`; assert the parent observes `LaunchBlocked` from inside its execute body.
4. **Block matches Redis launch command.** Publish a `launch` command on the stream for a blocked task; assert the consumer logs a warning at `WARNING` level, ACKs the message, and no process is created.
5. **Non-matching launch passes through.** Two tasks, metadata `{"project": "p1"}` and `{"project": "p2"}`. Block `{"project": "p1"}`. The first launch raises; the second succeeds normally.
6. **Two concurrent context managers, same filter.** Both register independently. Exiting one keeps the block in force; both exits required.
7. **Re-entry in the same coroutine.** Nested `async with` works as expected.
8. **Empty filter blocks all launches.** `block_launches({})` rejects every launch.
9. **Block lifted on normal exit.** After the context manager exits without exception, launches with matching metadata succeed.
10. **Block lifted on exception inside the body.** Inside the `async with`, raise an exception; assert that the block is removed (the original exception still propagates, but post-exception launches succeed).
11. **`LaunchBlocked` message contents.** Assert the message includes the filter and the rejected task metadata.

Existing tests that exercise launch paths are reviewed for any incidental change in error output.

## Migration impact

- **Public API.** Pure additive: one new method on `Optio` (`block_launches`), one new exception class (`LaunchBlocked`), both exported from `optio_core/__init__.py`. No existing caller breaks.
- **Wire protocol.** No changes to Redis stream payloads, command names, or Mongo schema. No DB migration.
- **Internal types.** New private attribute `Optio._launch_blocks` on the Optio instance.
- **Performance.** Every launch-path callsite gains a dict-empty check (no-op fast path) plus one short loop when blocks are registered. Negligible.

## Cross-repo coordination

This spec is independent of the deadline-driven cancel work. Either can land first; neither blocks the other on the optio side.

Excavator depends on **both**: project-delete's handler wraps its cascade in `block_launches` (this spec) and relies on `Optio.cancel()` having deadline semantics (deadline-cancel spec) so `gracefully_terminate_tasks` can be a thin wrapper. The excavator integration is sequenced after both optio-core specs land.

Suggested branch in optio: `csillag/launch-guard`, cut from `main`. Independent of `csillag/project-removal` (which holds the deadline-cancel work + the eventual `gracefully_terminate_tasks` helper).

## Out of scope

- **Multi-engine semantics.** Blocks are per-engine in-memory. A second engine in a multi-engine deployment is unaware of another engine's blocks. Single-engine assumption — same as deadline-cancel.
- **Persistence across restarts.** Lost on restart. The teardown handler re-registers when the Redis command is redelivered.
- **Block TTL / per-block timeout.** A block lives exactly as long as its `async with` body runs; no automatic expiration.
- **Per-source rate limiting / quotas.** This is a binary block, not a throttle.
- **Direct token-based primitives.** Single primitive policy. Callers needing manual lifetime control compose `block_launches` with `contextlib.AsyncExitStack`.
