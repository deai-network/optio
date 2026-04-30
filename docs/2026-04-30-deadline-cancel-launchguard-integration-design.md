# Design: Deadline-Cancel × Launch-Guard Integration

**Date:** 2026-04-30
**Base revision:** `59782ec7d7d5f5fc96e017973b684d3ac4c56504` on branch `main` (as of 2026-04-30T11:09:15Z)
**Scope:** Land `csillag/project-removal` (deadline-driven cancel work, 17 commits) onto `main` (which gained 14 launch-guard commits in parallel since shared base `191fd39`). Resolve one mechanical conflict, document three interaction surfaces, add two minimal interaction tests.

## Status of upstream work

- **Deadline-cancel.** Spec `docs/2026-04-29-deadline-driven-cancel-design.md`, plan `docs/2026-04-29-deadline-driven-cancel-plan.md`, both implemented and merged into `csillag/project-removal` HEAD `19b5448` (163 tests pass on the branch).
- **Launch-guard.** Spec `docs/2026-04-29-launch-guard-design.md`, plan `docs/2026-04-29-launch-guard-plan.md`, both on `main` HEAD `59782ec`. Adds `LaunchBlocked` exception, `Optio.block_launches(launch_filter)` async context manager, `_launch_blocks: dict[uuid.UUID, ProcessMetadataFilter]` registry, `Optio._check_launch_blocks(metadata)` method, and integrations at five doorways: `Optio.adhoc_define`, `Optio.launch`, `Optio.launch_and_wait`, `Optio._handle_launch`, and `Executor.execute_child`. `Executor.__init__` gained an optional `optio: "Optio | None" = None` kwarg storing `self._optio = optio`. `lifecycle.init` constructs `Executor(..., optio=self)`. Tests in `tests/test_launch_guard.py`. `LaunchBlocked` and `block_launches` exported via `optio_core/__init__.py`.

The two specs are independent and remain untouched by this design.

## Goal & Approach

Land deadline-cancel work onto main without losing launch-guard. Linear rebase of `csillag/project-removal` (17 commits) onto `main` HEAD, manual conflict resolution at one commit, plus a small number of integration-level docs and tests.

The two features are orthogonal in intent — deadline-cancel adds a deadline to cooperative cancel; launch-guard rejects launches whose metadata matches an active block. They share `Executor` and `Optio` constructors as integration points but no operational paths interact: `_check_launch_blocks` runs synchronously before any cancel state exists, and the cancel mechanism never reads launch-block state.

## Conflict resolution rules

One textual conflict to resolve manually during rebase, at deadline-cancel commit `6d691b2` ("track running asyncio tasks and per-process cancel entries"). All other 16 commits expected to rebase cleanly.

### Region: `Executor.__init__` (signature + body) in `packages/optio-core/src/optio_core/executor.py`

Resolved final form:

```python
def __init__(
    self,
    db: AsyncIOMotorDatabase,
    prefix: str,
    services: dict[str, Any],
    optio: "Optio | None" = None,
):
    self._db = db
    self._prefix = prefix
    self._services = services
    self._optio = optio
    self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
    self._running_tasks: dict[ObjectId, asyncio.Task] = {}
    self._task_registry: dict[str, TaskInstance] = {}
```

Rules per region:

- **Signature:** keep main's 4-arg form (`optio` kwarg with `None` default).
- **`self._optio = optio`:** keep main's line.
- **`_cancellation_flags` value type:** `_CancelEntry` (deadline-cancel side wins; launch-guard's bare-`Event` form is functionally subsumed since launch-guard never reads the value, only iterates).
- **`_running_tasks`:** keep deadline-cancel's addition.

### Other expected-clean regions (resolution rules, in case of conflict)

- **`Executor.execute_child`:** keep main's `if self._optio is not None: self._optio._check_launch_blocks(parent_ctx.metadata)` insert at the top of the method. Requires `self._optio` to exist (resolved by the constructor change above).
- **`lifecycle.init` body, `Executor(...)` call:** combined form `Executor(mongo_db, prefix, services, optio=self)` — keep main's `optio=self` plus deadline-cancel's `cancel_grace_seconds` plumbing into `OptioConfig` (which is on a separate line and shouldn't textually conflict).
- **`Optio.__init__` body:** both `self._launch_blocks: dict[uuid.UUID, ProcessMetadataFilter] = {}` (main) and `self._supervisor_task: asyncio.Task | None = None` (deadline-cancel) coexist. Adjacent but distinct lines.

If the rebase surfaces unexpected conflicts beyond this list, the implementer applies the same union-of-additive-changes rule and captures the resolution in the rebase commit notes.

## Documented interactions

Three interaction surfaces. None require new mechanism — just documentation here.

### Child launch blocked while parent runs

`Executor.execute_child` calls `self._optio._check_launch_blocks(parent_ctx.metadata)` at the top, before any state changes. If a block matches, `LaunchBlocked` raises out of `parent_ctx.run_child(...)` as a normal exception. The parent's `execute_fn` either handles it or lets it bubble — `_execute_process`'s `except Exception` clause catches it and writes `failed`. The blocked child never enters `_cancellation_flags` or `_running_tasks`, so the supervisor and shutdown have nothing to do for it.

### Cancel during an active block

`Optio.cancel(pid)` and `Optio.cancel_and_wait(pid)` write to Mongo (`cancel_requested` → `cancelling`) and the deadline registry. Neither path consults `_launch_blocks`. A block active in some other coroutine has zero effect on a cancel arriving at an existing running process. Symmetrically, an arriving `LaunchBlocked` synchronously rejects the launch before any process record exists, so it can't race a cancel.

### Shutdown while a block is active

`Optio.shutdown()` does not iterate or clear `_launch_blocks`. Blocks are owned by the `async with optio.block_launches({...}):` body in some user coroutine. When shutdown's force-cancel injects `CancelledError` into that coroutine, the `async with` exit pops the block from `_launch_blocks` via the existing `finally`. No leak. No extra shutdown logic needed.

## Integration tests

Two new tests in a new file `packages/optio-core/tests/test_deadline_cancel_launchguard.py`:

### `test_child_launchblocked_propagates_and_parent_cancellable`

Parent task spawns a child whose metadata matches an active block. Child raises `LaunchBlocked`. Parent's `execute_fn` catches the exception and returns. Concurrently (or just before the parent returns), `optio.cancel(parent_pid)` is issued. Assertions:

- Parent reaches a terminal state (cooperative `cancelled` if it honoured the flag, else `failed`).
- `_cancellation_flags` and `_running_tasks` have no orphan entries for parent or would-be child.
- `_launch_blocks` has no leaked entries (the `async with` body that registered the block exits cleanly).

### `test_force_cancel_with_active_block_does_not_leak`

A stubborn task is running (ignores cooperative flag). In a second coroutine, an `async with optio.block_launches({...}):` body is active. `optio.cancel_and_wait(stubborn_pid)` fires the supervisor force-cancel path. Assertions:

- Stubborn task ends `failed` with canonical error `"Task did not unwind within cancellation grace period"`.
- The `block_launches` context exits cleanly when its holding coroutine completes (via `CancelledError` injection by shutdown, or by the test's own teardown).
- `_launch_blocks` is empty after teardown.

These two cover the interaction risks the design identifies. A comprehensive matrix (cancel × block × shutdown × child-launch combinations) is intentionally out of scope — extra coverage not justified by the integration's narrow surface area.

## Out of scope

- **Modifying existing deadline-cancel spec/plan or launch-guard spec/plan.** Both stay as historical record. This spec captures only the integration delta.
- **Reworking `Optio.shutdown()` to interact with `_launch_blocks`.** Existing `async with` cleanup is correct; no new mechanism.
- **Public API for `_launch_blocks` introspection** (e.g., listing active blocks). Not needed for integration; would be a separate feature.
- **Performance.** Integration adds zero hot-path work — `_check_launch_blocks` already short-circuits on empty registry; `_running_tasks` and `_cancellation_flags` lookups don't touch `_launch_blocks`.

## Cross-repo coordination

This integration unblocks excavator's project-delete feature, which depends on the deadline-cancel mechanism via the merged `optio-core` on `main`. After this rebase + merge lands, excavator can pin the new `optio-core` version.
