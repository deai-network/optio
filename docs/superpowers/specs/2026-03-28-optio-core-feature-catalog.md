# optio-core Feature Catalog

A comprehensive catalog of every task/process feature in optio-core, with test scenario suggestions for each. The goal is to guide the creation of a test application that meaningfully exercises all code paths.

**Assumptions:** Redis and optio-dashboard are available in the test environment for triggering commands (launch, cancel, dismiss, resync). This document does not cover application configuration or command transport — only what tasks and processes can do.

---

## Table of Contents

1. [TaskInstance Definition](#1-taskinstance-definition)
2. [ProcessContext API](#2-processcontext-api)
3. [State Machine](#3-state-machine)
4. [Child Processes](#4-child-processes)
5. [Cancellation](#5-cancellation)
6. [Progress Reporting & Helpers](#6-progress-reporting--helpers)
7. [Error Handling](#7-error-handling)
8. [Logging](#8-logging)
9. [Ephemeral Processes](#9-ephemeral-processes)
10. [Ad-hoc Processes](#10-ad-hoc-processes)
11. [Cron Scheduling](#11-cron-scheduling)
12. [Lifecycle Operations](#12-lifecycle-operations)

---

## 1. TaskInstance Definition

A `TaskInstance` is the unit of work provided by the application's task generator. Every field affects how the process behaves at runtime.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `execute` | `async (ctx) -> None` | required | The async function that runs the task. Receives a `ProcessContext`. |
| `process_id` | `str` | required | Unique identifier. Used in launch/cancel/dismiss. Convention: kebab-case. |
| `name` | `str` | required | Human-readable display name. |
| `params` | `dict[str, Any]` | `{}` | Default parameters passed to the task via `ctx.params`. |
| `metadata` | `dict[str, Any]` | `{}` | Custom key-value pairs for filtering/tagging. Inherited by children. |
| `schedule` | `str \| None` | `None` | APScheduler cron expression. If set, the process launches automatically on match. |
| `special` | `bool` | `False` | Flag for special/internal processes. |
| `warning` | `str \| None` | `None` | Warning message displayed in the UI. |
| `cancellation` | `CancellationConfig` | `CancellationConfig()` | Controls cancellation behavior. |

### CancellationConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cancellable` | `bool` | `True` | Whether the process can be cancelled. When `False`, the UI hides the cancel button. |
| `propagation` | `str` | `"down"` | Intended propagation direction: `"down"`, `"up"`, `"both"`, `"none"`. Stored on the process record. |

### What happens at init

When `optio.init()` runs, it calls the task generator and for each `TaskInstance`:
- Creates or updates a process record in MongoDB via `upsert_process`.
- On insert: sets state to `idle`, progress to `0%`, empty logs, `parentId=None`, `rootId=self`, `depth=0`.
- On update: overwrites `name`, `params`, `metadata`, `cancellable`, `special`, `warning`. Preserves runtime state (status, progress, logs).
- Removes stale root processes (those not returned by the current task generator).
- Registers execute functions in the executor's task registry.
- Syncs cron schedules.

### Test Scenarios

- **Basic task with params**: Define a task with `params={"count": 5}` that reads `ctx.params["count"]` and loops that many times, reporting progress each iteration. Verifies params are passed through.
- **Task with metadata**: Define a task with `metadata={"category": "import", "priority": "high"}`. Launch it, then verify metadata appears on the process record and is inherited by any children it spawns.
- **Non-cancellable task**: Define a task with `cancellation=CancellationConfig(cancellable=False)`. Verify the dashboard hides the cancel button for this process (the `cancellable` field is a UI hint, not enforced by the executor).
- **Task with warning**: Define a task with `warning="This process takes a long time"`. Verify the warning appears in the UI.
- **Special task**: Define a task with `special=True`. Verify the flag is stored on the process record.

---

## 2. ProcessContext API

The `ProcessContext` is the interface task code receives. It provides read-only properties and methods for progress reporting, cancellation checking, child process management, and ephemeral marking.

### Read-only Properties

| Property | Type | Description |
|----------|------|-------------|
| `process_id` | `str` | The process's unique identifier. |
| `params` | `dict[str, Any]` | Parameters from the task definition (or parent, for children). |
| `metadata` | `dict[str, Any]` | Metadata dict. Inherited from parent for children. |
| `services` | `dict[str, Any]` | Custom services dict passed during `optio.init()`. |

### Methods

#### `report_progress(percent: float | None, message: str | None = None) -> None`

Updates the process's progress.

- `percent`: 0-100 numeric value, or `None` for indeterminate progress.
- `message`: If provided, also creates an "info" log entry. If `None`, only updates the progress number (no log).
- **Throttled**: Progress writes to MongoDB are buffered and flushed at intervals (default 100ms, controlled by `OPTIO_PROGRESS_FLUSH_INTERVAL_MS` env var).
- **Parent notification**: If a parent has wired a progress listener, the parent is notified immediately (in-memory, not throttled).

#### `should_continue() -> bool`

Returns `False` if cancellation has been requested for this process. Non-blocking.

#### `mark_ephemeral() -> None` (async)

Marks this process for automatic deletion after it reaches a terminal state. Can be called at any point during execution. See [Ephemeral Processes](#9-ephemeral-processes).

#### `run_child(...)` (async)

Launches a sequential child process. See [Child Processes](#4-child-processes).

#### `parallel_group(...)`

Creates a parallel execution scope. See [Child Processes](#4-child-processes).

### Test Scenarios

- **Services access**: Pass a custom service (e.g., `services={"api_key": "test123"}`) at init time. Define a task that reads `ctx.services["api_key"]` and logs it via `report_progress`. Verify the value arrives correctly.
- **Indeterminate progress**: A task that calls `report_progress(None, "Working...")`. Verify the process shows indeterminate progress in the UI/DB (percent is `None`).
- **Progress without message**: A task that calls `report_progress(50)` (no message). Verify progress updates to 50% but no new log entry is created.
- **Progress with message**: A task that calls `report_progress(50, "Halfway there")`. Verify both the progress percent and the "info" log entry.
- **Rapid progress updates (throttling)**: A task that calls `report_progress` in a tight loop (e.g., 1000 times). Verify that the DB isn't hammered with 1000 writes — the final progress should be correct, but intermediate values are coalesced.

---

## 3. State Machine

### All States

| State | Group | Description |
|-------|-------|-------------|
| `idle` | LAUNCHABLE | Initial state. Not yet launched. |
| `scheduled` | ACTIVE | Ready to run, about to execute. |
| `running` | ACTIVE | Currently executing. |
| `done` | END, LAUNCHABLE, DISMISSABLE | Completed successfully. |
| `failed` | END, LAUNCHABLE, DISMISSABLE | Execution failed with error. |
| `cancel_requested` | ACTIVE | Cancellation requested, not yet processing. |
| `cancelling` | ACTIVE | Cancellation logic is active. |
| `cancelled` | END, LAUNCHABLE, DISMISSABLE | Cancelled successfully. |

### Valid Transitions

```
idle         -> scheduled
scheduled    -> running, cancel_requested
running      -> done, failed, cancel_requested
done         -> scheduled, idle
failed       -> scheduled, idle
cancel_requested -> cancelling
cancelling   -> cancelled
cancelled    -> scheduled, idle
```

### State Groups

| Group | States | Meaning |
|-------|--------|---------|
| ACTIVE | scheduled, running, cancel_requested, cancelling | Process is in progress |
| END | done, failed, cancelled | Terminal states |
| LAUNCHABLE | idle, done, failed, cancelled | Can be (re)launched |
| CANCELLABLE | scheduled, running | Can receive cancel request |
| DISMISSABLE | done, failed, cancelled | Can be reset to idle |

### What triggers transitions

| Transition | Trigger |
|------------|---------|
| idle -> scheduled | Launch command |
| scheduled -> running | Executor picks up the task |
| scheduled -> cancel_requested | Cancel command while scheduled (short-circuits to cancelled) |
| running -> done | Task function returns normally without cancellation flag set |
| running -> failed | Task function raises an unhandled exception |
| running -> cancel_requested | Cancel command while running |
| cancel_requested -> cancelling | Executor finds and sets the cancellation flag |
| cancelling -> cancelled | Task function exits after checking `should_continue()` |
| done/failed/cancelled -> scheduled | Re-launch command |
| done/failed/cancelled -> idle | Dismiss command |

**Note on scheduled cancellation:** When a cancel command arrives for a process in `scheduled` state (not yet running), the framework skips the `cancel_requested` -> `cancelling` flow and goes directly to `cancelled` with a `stopped_at` timestamp.

### Test Scenarios

- **Full happy path**: Launch a task, watch it go through idle -> scheduled -> running -> done.
- **Re-launch from done**: After a task completes successfully, launch it again. Verify it clears previous results (logs, progress, timestamps) and goes through the full cycle again.
- **Re-launch from failed**: After a task fails, re-launch it. Same verification as above.
- **Re-launch from cancelled**: After a task is cancelled, re-launch it.
- **Dismiss from each terminal state**: Dismiss a process from done, failed, and cancelled. Each should reset to idle with cleared results.
- **Cancel while scheduled**: This requires a task that stays in `scheduled` long enough to cancel. In practice the transition from scheduled to running is near-instant, so this is hard to test with normal tasks. The observable behavior is that cancellation of a not-yet-running process goes directly to `cancelled`.

---

## 4. Child Processes

Tasks can spawn child processes, either sequentially (one at a time) or in parallel (concurrently). Children can spawn their own children to arbitrary depth.

### Sequential Children: `run_child()`

```python
state = await ctx.run_child(
    execute=my_child_fn,       # async function receiving ProcessContext
    process_id="parent-child1",
    name="Child 1",
    params={"key": "value"},   # default: {}
    survive_failure=False,     # default: False
    survive_cancel=False,      # default: False
    on_child_progress=callback, # default: None
)
# state is "done", "failed", or "cancelled"
```

- **Blocking**: Parent waits until the child completes.
- **Child record**: Created in MongoDB with `parentId`, `rootId`, `depth=parent.depth+1`, sequential `order`.
- **Metadata inheritance**: Child automatically receives parent's `metadata` dict.
- **Params**: Child gets its own `params` (not inherited from parent).
- **Return value**: The child's end state string: `"done"`, `"failed"`, or `"cancelled"`.
- **Log entry**: An "event" log entry `"Spawned child: {name}"` is written to the parent's log.

#### survive_failure

- `False` (default): If the child fails, a `RuntimeError("Child process '{name}' failed")` is raised in the parent. This causes the parent to fail too (unless the parent catches the exception).
- `True`: The child's failure is silently absorbed. `run_child()` returns `"failed"`. The parent can inspect the return value and decide what to do.

#### survive_cancel

- `False` (default): If the child is cancelled, the parent's cancellation flag is set. The parent will see `should_continue() == False` on next check.
- `True`: The child's cancellation is silently absorbed. `run_child()` returns `"cancelled"`. The parent continues normally.

### Parallel Children: `parallel_group()`

```python
async with ctx.parallel_group(
    max_concurrency=3,          # default: 10
    survive_failure=False,      # default: False
    survive_cancel=False,       # default: False
    on_child_progress=callback, # default: None
) as group:
    await group.spawn(fn_a, "child-a", "Child A")
    await group.spawn(fn_b, "child-b", "Child B", params={"x": 1})
    await group.spawn(fn_c, "child-c", "Child C")

# After the async-with block, all children have completed.
# group.results is a list of ChildResult(process_id, state, error)
```

- **Concurrency control**: Semaphore-based. `spawn()` blocks if `max_concurrency` active children are already running.
- **Waiting**: `__aexit__` calls `asyncio.gather()` on all spawned tasks. All children must complete before the block exits.
- **Results**: `group.results` is a list of `ChildResult` objects with `process_id`, `state`, and `error` (None if done, otherwise a message).
- **Failure handling**:
  - `survive_failure=False`: If any child failed, `__aexit__` raises `RuntimeError("Parallel group failed: N children did not complete successfully")`.
  - `survive_failure=True`: Failed children are recorded in `group.results` but no exception is raised.
- **Cancel handling**: Same pattern as failure. `survive_cancel=False` raises on any cancelled child.

**Implementation detail**: Internally, `spawn()` always calls `run_child()` with `survive_failure=True, survive_cancel=True`. The group tracks results itself and applies the survive flags in `__aexit__`.

### Nesting

- Children can call `ctx.run_child()` or `ctx.parallel_group()` themselves.
- Each level increments `depth` (root=0, child=1, grandchild=2, ...).
- `rootId` always points to the top-level ancestor.
- `order` is sequential within each parent (0, 1, 2, ...).

### Test Scenarios

- **Sequential children**: A parent that runs 3 children sequentially. Each child does some work and reports progress. Verify the tree structure in the DB (parentId, rootId, depth, order) and that children execute in order.
- **Parallel children**: A parent that spawns 5 children in a parallel group with `max_concurrency=2`. Each child sleeps for a random duration. Verify that at most 2 run concurrently (can be observed via running state timestamps).
- **Nested children (3 levels)**: A grandparent spawns a child, which spawns its own child. Verify depth=0, 1, 2 and that rootId is consistent across all three.
- **Metadata inheritance**: Parent has `metadata={"project": "alpha"}`. Verify child and grandchild both have the same metadata.
- **Params are NOT inherited**: Parent has `params={"x": 1}`, child is spawned with `params={"y": 2}`. Verify child sees `{"y": 2}`, not `{"x": 1}`.
- **Child failure propagation (default)**: Parent runs a child that raises an exception. Parent should also fail with the RuntimeError.
- **Child failure survived**: Parent runs a child with `survive_failure=True`. Child fails. Parent continues and completes successfully.
- **Parallel partial failure (default)**: Group of 3 children, one fails. Group raises RuntimeError. Parent fails.
- **Parallel partial failure (survived)**: Same but with `survive_failure=True`. Parent inspects `group.results` and completes. Verify results contain the failed child.
- **Mixed sequential and parallel**: A parent runs 2 sequential children, then a parallel group of 3, then 1 more sequential child. Verify correct order values and tree structure.

---

## 5. Cancellation

Cancellation in optio-core is cooperative. The framework sets a flag; the task code must check it and exit voluntarily.

### How cancellation works

1. A cancel command arrives (via dashboard/Redis).
2. If the process is in `scheduled` state: it goes directly to `cancelled` (no code was running).
3. If the process is in `running` state:
   - State transitions to `cancel_requested`.
   - The executor finds the process's `asyncio.Event` cancellation flag and sets it.
   - State transitions to `cancelling`.
   - The task code checks `ctx.should_continue()` and finds it returns `False`.
   - The task code should exit (return from the execute function).
   - After the execute function returns with the flag set, the state transitions to `cancelled`.

### Cancellation and children

- **Downward propagation**: Children share the parent's cancellation flag. When the parent is cancelled, children see `should_continue() == False` on their next check.
- **Upward propagation via survive_cancel=False**: If a child is cancelled and the parent used `survive_cancel=False`, the parent's cancellation flag is set. This causes the parent to also stop.
- **Blocking on child**: If a parent is blocked on `run_child()` and the cancellation flag is set, the child will see the flag (since it shares the parent's flag), exit, and then the parent resumes and can check `should_continue()`.

### What happens if the task ignores cancellation

If a task never checks `should_continue()`, it will run to completion. The cancellation flag is set, so after the execute function returns normally, the executor checks `cancel_flag.is_set()` and transitions the state to `cancelled` instead of `done`.

### CancellationConfig effects

- `cancellable=False`: The `cancellable` field is stored on the process record. The UI uses this to hide the cancel button. The framework does not enforce this at the executor level — it's a UI hint.
- `propagation`: Stored on the process record but not actively used by the executor in the current implementation. It's a declared intent for future use or for application logic to read.

### Test Scenarios

- **Cooperative cancellation**: A long-running task that loops and checks `should_continue()` each iteration. Cancel it mid-execution. Verify it transitions through cancel_requested -> cancelling -> cancelled. Verify it stops promptly.
- **Cancellation ignored**: A task that never checks `should_continue()` — just does work and returns. Cancel it while running. Verify it completes its work but ends up in `cancelled` state (not `done`).
- **Cancel propagation to children**: A parent running a long child. Cancel the parent. Verify the child also sees cancellation and both end up cancelled.
- **Child cancellation propagating up (survive_cancel=False)**: A parent spawns a child. The child is cancelled directly (or fails to continue). Parent should also get cancelled.
- **Child cancellation survived (survive_cancel=True)**: Same setup but parent survives the child's cancellation and continues.
- **Cancel during parallel group**: A parent runs a parallel group of 3 long-running children. Cancel the parent. Verify all children are cancelled and the group exits.
- **Non-cancellable task**: A task with `cancellable=False`. Verify the dashboard doesn't show a cancel button for it.

---

## 6. Progress Reporting & Helpers

### Direct Progress Reporting

```python
ctx.report_progress(50)                      # 50%, no log entry
ctx.report_progress(50, "Halfway done")      # 50% + info log entry
ctx.report_progress(None)                    # indeterminate, no log
ctx.report_progress(None, "Working...")      # indeterminate + info log
```

- **Throttling**: Progress writes to MongoDB are buffered. Default flush interval is 100ms (`OPTIO_PROGRESS_FLUSH_INTERVAL_MS`).
- **Final flush**: When the process completes (success or failure), any pending progress is force-flushed.
- **Parent listener**: If this is a child process and the parent has an `on_child_progress` callback, the parent is notified in-memory immediately (not subject to the DB throttle).

### Progress Helpers

These are factory functions that return callbacks suitable for the `on_child_progress` parameter of `run_child()` or `parallel_group()`.

#### `sequential_progress(ctx, total_children)`

Divides the parent's 0-100% into equal slots, one per child (based on declared `total_children`). Each child's progress fills its slot proportionally. Completed children (done/failed/cancelled) count as 100% of their slot.

```python
# Parent will show 0-100% spread across 3 children
cb = sequential_progress(ctx, 3)
await ctx.run_child(fn_a, "a", "Step 1", on_child_progress=cb)  # fills 0-33%
await ctx.run_child(fn_b, "b", "Step 2", on_child_progress=cb)  # fills 33-66%
await ctx.run_child(fn_c, "c", "Step 3", on_child_progress=cb)  # fills 66-100%
```

Children are assigned to slots by their position in the snapshot list (spawn order). If `total_children` is less than the actual number of children, extra children's progress is ignored. If `total_children` is more, the parent never reaches 100%.

#### `average_progress(ctx)`

Averages all children's progress. Completed children count as 100%. `None` percent counts as 0%.

```python
# Parent shows the average of all children's progress
cb = average_progress(ctx)
async with ctx.parallel_group(on_child_progress=cb) as group:
    await group.spawn(fn_a, "a", "Worker A")
    await group.spawn(fn_b, "b", "Worker B")
```

#### `mapped_progress(ctx, range_start, range_end)`

Maps a single child's 0-100% into a portion of the parent's progress. `range_start` and `range_end` are fractions (0.0 to 1.0). Uses the **last** child in the snapshot (the most recently spawned).

```python
# First child maps to 0-25% of parent
cb1 = mapped_progress(ctx, 0.0, 0.25)
await ctx.run_child(fn_a, "a", "Download", on_child_progress=cb1)

# Manual progress for the middle section
ctx.report_progress(50, "Processing...")

# Second child maps to 75-100% of parent
cb2 = mapped_progress(ctx, 0.75, 1.0)
await ctx.run_child(fn_b, "b", "Upload", on_child_progress=cb2)
```

### Child progress callback mechanics

- The `on_child_progress` callback receives a `list[ChildProgressInfo]` — a snapshot of all children that have reported progress so far.
- Each `ChildProgressInfo` has: `process_id`, `name`, `state`, `percent`, `message`.
- Callbacks are throttled at 10/sec (100ms interval).
- State changes (done/failed/cancelled) fire the callback immediately, bypassing the throttle.
- When a child reaches a terminal state, its `percent` is set to 100.0 in the snapshot.

### Test Scenarios

- **sequential_progress with 3 children**: Parent spawns 3 sequential children. Each child reports progress from 0 to 100. Verify the parent's progress smoothly advances from 0% to 100% in three equal segments.
- **sequential_progress with early failure**: 3 planned children, second one fails. Verify parent progress shows ~66% (first child done = 33%, second child failed = counts as 100% of its slot = 33% more).
- **average_progress with parallel children**: Parent spawns 4 parallel children. Each runs at different speeds. Verify parent progress is the average.
- **mapped_progress for phased work**: Parent does 3 phases: child A (0-30%), manual work (30-70%), child B (70-100%). Verify parent progress tracks each phase correctly.
- **Indeterminate progress in children**: A child reports `percent=None`. Verify the helper treats it as 0% (not as some special value).
- **Progress message creates log entries**: A task that alternates between `report_progress(n)` and `report_progress(n, "message")`. Verify logs only appear for the calls with messages.

---

## 7. Error Handling

### Unhandled exceptions in task code

When a task's execute function raises an exception:

1. The exception is caught by the executor.
2. `ctx.flush_final_progress()` is called (flushes any pending progress).
3. The process state transitions to `failed`.
4. `status.error` is set to the exception message (`str(e)`).
5. `status.failedAt` is set to the current timestamp.
6. An "error" level log entry is appended with the exception message.
7. If the process is ephemeral, it is deleted.
8. The exception does **not** propagate beyond the executor.

### Child failure propagation

See [Child Processes](#4-child-processes) for `survive_failure` and `survive_cancel` behavior.

**Chain of failure** (default behavior):
1. Grandchild raises exception -> grandchild fails.
2. Child's `run_child()` receives `RuntimeError("Child process 'grandchild' failed")` -> child fails.
3. Parent's `run_child()` receives `RuntimeError("Child process 'child' failed")` -> parent fails.

Each level stores its own error message and creates its own "error" log entry. The chain can be broken at any level by using `survive_failure=True`.

### Parallel group failure

When `survive_failure=False` (default) and any child in a parallel group fails:
- `__aexit__` raises `RuntimeError("Parallel group failed: N children did not complete successfully")`.
- "N" counts children whose state is not "done" (so both failed and cancelled children count).
- This exception propagates to the parent's execute function, causing the parent to fail (unless caught).

When `survive_failure=True`:
- No exception is raised.
- `group.results` contains all children's outcomes.
- The parent can inspect results and decide how to proceed.

### No execute function found

If a process is launched but no execute function is registered for it (e.g., stale process record), the executor transitions it directly to `failed` with error `"No execute function found"`.

### Test Scenarios

- **Simple failure**: A task that raises `ValueError("bad input")`. Verify state=failed, status.error="bad input", "error" log entry present, failedAt timestamp set.
- **Failure after partial progress**: A task that reports progress to 60%, then raises. Verify the final progress is flushed (shows 60%) and state=failed.
- **Cascading failure (3 levels)**: Grandchild raises -> child fails -> parent fails. Verify each level has its own error message and failed state.
- **Failure chain broken by survive_failure**: Grandchild raises, child uses `survive_failure=True`, parent completes successfully. Verify grandchild is failed, child is done, parent is done.
- **Parallel group failure (default)**: 3 children, one raises. Group raises. Parent fails.
- **Parallel group failure (survived)**: Same but `survive_failure=True`. Parent inspects results and completes.
- **Mixed survive in parallel**: Group with `survive_failure=True`. After the group, parent checks results, and based on which children failed, decides to continue or raise its own error.

---

## 8. Logging

### Log entry structure

```json
{
  "timestamp": "2026-03-28T10:30:00.000000+00:00",
  "level": "info | error | event",
  "message": "...",
  "data": {}
}
```

The `data` field is optional and only present when explicitly provided.

### Log levels and what triggers them

| Level | Trigger | Message |
|-------|---------|---------|
| `event` | State transition to scheduled | `"State changed to scheduled"` |
| `event` | State transition to running | `"State changed to running"` |
| `event` | State transition to done | `"State changed to done"` |
| `event` | State transition to cancelled | `"State changed to cancelled"` |
| `event` | Child spawned | `"Spawned child: {name}"` |
| `info` | `report_progress(n, message)` | The provided message string |
| `error` | Unhandled exception | The exception message (`str(e)`) |

### What does NOT create log entries

- `report_progress(n)` without a message — only updates progress, no log.
- State transitions to `failed` — the error is logged, but there's no separate "State changed to failed" event log. The error log entry serves that purpose.
- State transitions to `cancel_requested` or `cancelling` — these intermediate states don't generate log entries.

### Log lifecycle

- Logs accumulate during a process run.
- When a process is dismissed or re-launched, logs are cleared (along with all other result fields).
- Logs are never truncated during a run — they grow unbounded.

### Test Scenarios

- **Automatic event logs**: A simple task that runs and completes. Verify the log contains: "State changed to scheduled", "State changed to running", "State changed to done" (in that order).
- **Progress messages in logs**: A task that calls `report_progress(25, "Quarter done")`, then `report_progress(50)`, then `report_progress(75, "Almost there")`. Verify only "Quarter done" and "Almost there" appear as info logs.
- **Error logs**: A task that raises. Verify the error message appears as an "error" level log entry.
- **Child spawn logs**: A parent that spawns children. Verify "Spawned child: ..." event entries appear in the parent's log.
- **Logs cleared on re-launch**: A task that completes with several log entries. Re-launch it. Verify the old logs are gone and fresh ones appear.

---

## 9. Ephemeral Processes

Ephemeral processes are automatically deleted from the database after reaching a terminal state (done, failed, or cancelled).

### Two ways to mark ephemeral

1. **At definition time**: `adhoc_define(task, ephemeral=True)` — the process is ephemeral from creation.
2. **During execution**: `await ctx.mark_ephemeral()` — the process becomes ephemeral mid-run.

### Cleanup behavior

After a process's execute function completes (or fails):
1. The executor checks the process record's `ephemeral` field.
2. If `True`: calls `delete_process()`, which deletes the process **and all its descendants** from MongoDB.
3. The execute function is also removed from the task registry.

### Important details

- Cleanup happens for all terminal states: done, failed, and cancelled.
- Descendants are deleted recursively, regardless of whether they are themselves ephemeral.
- If a task is re-launched (which clears descendants), the re-launch clears the previous run's children first, then the new run's ephemeral cleanup deletes the process itself.

### Test Scenarios

- **Ephemeral ad-hoc process (success)**: Create an ephemeral ad-hoc process, launch it, let it complete. Verify the process record is deleted from the DB.
- **Ephemeral ad-hoc process (failure)**: Same but the task raises. Verify it's deleted even though it failed.
- **mark_ephemeral mid-execution**: A normal (non-ephemeral) task that calls `await ctx.mark_ephemeral()` during execution, then completes. Verify it's deleted.
- **Ephemeral parent with children**: An ephemeral parent spawns 3 children. After the parent completes, verify the parent and all children are deleted from the DB.

---

## 10. Ad-hoc Processes

Ad-hoc processes are created at runtime, outside the task generator. They don't participate in resync and must be manually managed.

### `adhoc_define(task, parent_id=None, ephemeral=False)`

Creates a process record and registers its execute function.

- **Root ad-hoc** (`parent_id=None`): Creates a top-level process with `parentId=None`, `rootId=self`, `depth=0`. Sets `adhoc=True`.
- **Child ad-hoc** (`parent_id=<ObjectId>`): Creates a child process under the specified parent, with `depth=parent.depth+1` and `rootId=parent.rootId`. Sets `adhoc=True`.
- The process starts in `idle` state. It is **not** automatically launched.
- Returns the full process document.

### `adhoc_delete(process_id)`

Deletes the process and all its descendants from the database. Removes the execute function from the task registry. No-op if the process doesn't exist.

### Ad-hoc vs. generator-defined processes

| Aspect | Generator-defined | Ad-hoc |
|--------|-------------------|--------|
| Created by | Task generator at init/resync | `adhoc_define()` at runtime |
| Survives resync | Yes (updated) | No (not touched by resync, but not removed either) |
| Removed by resync | Only if removed from generator | Never |
| `adhoc` flag | `False` | `True` |
| Auto-launched | Via cron schedule | Never |
| Can be ephemeral | No (not directly) | Yes, at creation time |

### Test Scenarios

- **Root ad-hoc process**: Define an ad-hoc process at runtime, launch it, verify it runs and completes. Verify `adhoc=True` in the DB.
- **Child ad-hoc process**: During a running parent task, use `adhoc_define(task, parent_id=parent_oid)` to create a child, then launch it. Verify tree structure (parentId, rootId, depth).
- **Ephemeral ad-hoc**: Create an ephemeral ad-hoc process, launch it, verify it's deleted after completion.
- **Ad-hoc survives resync**: Create an ad-hoc process, trigger resync. Verify the ad-hoc process is still present.
- **adhoc_delete**: Create an ad-hoc process with children, then delete it. Verify all are gone from DB.

---

## 11. Cron Scheduling

Tasks with a `schedule` field are automatically launched on a cron schedule via APScheduler.

### Schedule format

Uses APScheduler's `CronTrigger.from_crontab()`, which accepts standard 5-field cron expressions:

```
minute hour day_of_month month day_of_week
```

Examples:
- `"0 9 * * *"` — every day at 9:00 AM
- `"*/5 * * * *"` — every 5 minutes
- `"0 0 1 * *"` — first day of each month at midnight

### How it works

1. During `init()` / `resync()`, the scheduler clears all existing jobs and re-registers from the current task list.
2. Each scheduled task gets a job ID of `sched_{process_id}`.
3. When the cron expression matches, the scheduler calls `launch_fn(process_id)`, which creates a background task to launch the process.
4. The launch follows the normal launch flow (check LAUNCHABLE state, clear results, execute).

### Edge cases

- **APScheduler not installed**: Scheduler degrades gracefully to a no-op. No cron scheduling occurs.
- **Schedule registration failure**: Logged as an error, doesn't crash the application. Other schedules continue.
- **Process already running**: If the cron fires while the process is in an ACTIVE state (running, scheduled, etc.), `launch_process` returns `None` because the state isn't in LAUNCHABLE_STATES. The scheduled run is effectively skipped.

### Test Scenarios

- **Cron-triggered launch**: Define a task with `schedule="* * * * *"` (every minute). Verify it gets launched automatically. After it completes, verify it launches again on the next minute.
- **Cron while already running**: Define a scheduled task that takes longer than the schedule interval. Verify the second cron trigger is silently ignored (doesn't launch a second instance).
- **Schedule updated on resync**: Change a task's schedule and trigger resync. Verify the old job is replaced with the new schedule.

---

## 12. Lifecycle Operations

### Launch

Triggered by: dashboard play button, Redis command, `optio.launch()` / `optio.launch_and_wait()`.

What happens:
1. Looks up the process by `process_id`.
2. If not found or not in LAUNCHABLE state (idle/done/failed/cancelled): returns `None` (no-op).
3. Clears result fields: resets error, timestamps, progress, logs. **Deletes all descendant processes** from previous runs.
4. Transitions: state -> `scheduled` (with log entry) -> `running` (with log entry, `runningSince` timestamp).
5. Creates `ProcessContext`, executes the task function.
6. On completion: state -> `done` (with `doneAt`, `duration`) or `failed` or `cancelled`.

### Dismiss

Triggered by: dashboard dismiss button, Redis command, `optio.dismiss()`.

What happens:
1. Looks up the process by `process_id`.
2. If not in DISMISSABLE state (done/failed/cancelled): no-op.
3. Clears result fields (same as launch: timestamps, error, progress, logs, deletes descendants).
4. Transitions: state -> `idle`.

### Resync

Triggered by: Redis command, `optio.resync()`.

What happens:
1. Calls the task generator to get current task definitions.
2. For each task: upserts the process record (creates or updates).
3. Removes stale root processes (those not in the current task list).
4. Re-registers execute functions in the executor.
5. Re-syncs cron schedules.
6. Optional `clean=True`: deletes **all** process records before re-importing (nuclear option).

### Shutdown

Triggered by: SIGTERM, SIGINT, `optio.shutdown()`.

What happens:
1. Sets `_running = False`.
2. Stops the Redis consumer (if present).
3. Sets the shutdown event.
4. Sets cancellation flags on **all** running processes.
5. Waits up to 5 seconds for processes to exit.
6. Closes the Redis connection.

### Clear result fields (shared by launch and dismiss)

Resets the following on the process record:
- `status.error` -> `None`
- `status.runningSince` -> `None`
- `status.doneAt` -> `None`
- `status.duration` -> `None`
- `status.failedAt` -> `None`
- `status.stoppedAt` -> `None`
- `progress` -> `{percent: 0, message: None}`
- `log` -> `[]`
- Deletes all descendant processes recursively.

### Test Scenarios

- **Launch clears previous results**: Run a task that creates children and logs. Re-launch it. Verify all previous children, logs, progress, and timestamps are gone before the new run starts.
- **Dismiss resets to idle**: Complete a task, dismiss it. Verify state=idle, all result fields cleared, children deleted.
- **Resync adds new task**: Add a new task to the generator, trigger resync. Verify it appears in the DB.
- **Resync removes stale task**: Remove a task from the generator, trigger resync. Verify it's removed from the DB.
- **Resync preserves runtime state**: A task with updated name/params in the generator. Resync while it's idle. Verify name/params are updated but state remains idle.
- **Clean resync**: Trigger resync with `clean=True`. Verify all processes are deleted and re-created from scratch.
- **Graceful shutdown**: Start a long-running task, trigger shutdown. Verify the task receives cancellation and the process ends in cancelled state.
