# Cancel Propagation — Design

**Status:** Draft
**Date:** 2026-05-11
**Owner:** Kristof Csillag

## Problem

When `optio_core.cancel(process_id)` is called on a parent process that has running children, today's behavior is:

1. The parent's `status.state` is flipped to `cancel_requested` and its cooperative cancellation flag is set.
2. The parent's `should_continue()` starts returning False.
3. **Children get no signal.** Their own cancellation flags are untouched. Their DB state stays `running`/`scheduled`.
4. The parent's execute function is suspended inside `await ctx.run_child(...)` or `parallel_group.__aexit__` — those `await`s block until each child finishes naturally.
5. Each child runs to natural completion (done/failed) before the parent's `await` returns.
6. Only then does the parent observe the cancel signal, exit cooperatively, and reach `cancelled`.

Practical consequences:

- The parent stays in `cancel_requested`/`cancelling` for as long as its slowest descendant runs. From the user's perspective, cancel "did nothing."
- When the parent's grace deadline expires, the supervisor force-cancels only the parent (writes `failed`, calls `task.cancel()` on the parent's asyncio task). The child's asyncio task is unrelated; it keeps running. Eventually the child writes its own terminal `done`, leaving an orphaned subtree where a `failed` parent owns a `done` child.
- `store.cancel_children` exists in the code (added at an unknown point) but is **not called anywhere** — dead code, evidence of an earlier abandoned attempt at this work.

The `survive_cancel` flag on `run_child`/`parallel_group` governs the upward direction (whether a child's cancellation causes the parent to bail). It is unrelated to downward propagation. There is no existing downward-propagation mechanism.

## Goals

1. Cancelling a parent process must propagate to its active descendants by default.
2. A task may opt out of automatic propagation when it needs to coordinate its own shutdown of children (e.g., ordered cleanup, deliverable finalization). The escape hatch is bounded by the existing force-cancel grace.
3. Force-cancel always propagates, with no opt-out at any level.
4. The cancel grace budget is shared across the subtree: descendants do not get a fresh grace clock when propagation reaches them.
5. The existing upward-propagation paths (child cancel/fail → parent abnormal) must produce the same end-state in the tree as an explicit `cancel(parent)`.

## Non-goals

- Changing the externally visible HTTP API surface (`POST /processes/:prefix/:id/cancel` semantics, request/response shape).
- UI affordances for `auto_cancel_children` (no current use case for user-editable values).
- Cross-instance reconciliation (separately handled by `_cancel_stale_processes` / `lifecycle_reconciliation`).
- Remote-runner concerns: optio does not have a remote-runner concept; every descendant is in the local executor's `_running_tasks`.

## Protocol

### Direction 1 — Downward propagation (cooperative)

`lifecycle.cancel(process_id)` performs the standard local cancel (sets state to `cancel_requested`, arms the cooperative flag with a deadline), then:

- Looks up the process's `TaskInstance` in `executor._task_registry`.
- Reads `task.auto_cancel_children` (new field, default `True`). If the task is not in the registry, assume `True` — degrade safely toward propagation, not toward orphans.
- If `True`, queries the DB for direct children in `ACTIVE_STATES` and recursively calls `self.cancel(child_process_id, inherit_deadline=...)`.

Recursion processes one level at a time; each recursive call re-applies the per-level `auto_cancel_children` flag. Opt-out cleanly scopes per node.

### Direction 2 — Upward propagation (α)

When a child terminates in `cancelled` (and parent's `survive_cancel=False`) or `failed` (and `survive_failure=False`), the executor sets the parent's cancellation flag (existing behavior). Under this design, the executor additionally invokes a `notify_parent_abnormal(parent_process_id)` callback, wired to `lifecycle.cancel`. This funnels the upward signal through the same `cancel` entry point used by external callers, so the parent's downward propagation rules (and per-task `auto_cancel_children` flag) apply uniformly.

### Direction 3 — Opt-out (`auto_cancel_children=False`)

The task owns cleanup of its own children. When `cancel(parent)` arrives:

- Parent receives `cancel_requested` and its cancellation flag is set as normal.
- Parent's children are not auto-cancelled.
- Parent's execute function is expected to react to the cancel and orchestrate its own shutdown — including manually calling cancel on children, finalizing deliverables, etc.
- Parent may continue to spawn new children inside this window (see Race & Edge Cases).
- The cancel grace deadline is still binding. If parent does not reach a terminal state in time, the force-cancel cascade catches both the parent and any remaining live descendants.

### Direction 4 — Force-cancel cascade

`executor.force_cancel(oid)` is modified to recurse over direct active children unconditionally. No `auto_cancel_children` check. After the existing local steps (`task.cancel()` + conditional `_write_force_cancelled_state`), the executor queries DB for direct children in `ACTIVE_STATES` and recursively force-cancels each. Subtree depth is bounded by tree depth; total work is O(descendants), not O(descendants²).

The query is read at force time. New children spawned in the opt-out window are still in the DB and are caught by this walk.

### Shared cancel grace

`lifecycle.cancel` accepts an internal-use `inherit_deadline: float | None` kwarg. External callers omit it; the entry point computes `time.monotonic() + cancel_grace_seconds`. Recursive calls pass this deadline through. Every entry in `executor._cancellation_flags` produced by a single cancel sweep shares the same monotonic deadline. The 500ms supervisor loop observes them all expire simultaneously and issues `force_cancel` on each. Combined with the force-cancel cascade, all descendants reach terminal at the same logical moment.

## API surface changes

| Surface | Change |
|---|---|
| `TaskInstance` | New field `auto_cancel_children: bool = True` |
| `lifecycle.cancel` | Recurses to active direct children when this process's TaskInstance has `auto_cancel_children=True`. Accepts internal-use `inherit_deadline` kwarg |
| `executor.force_cancel` | Recurses to direct active children unconditionally |
| `executor.execute_child` | After abnormal child terminal, invokes `notify_parent_abnormal(parent_pid)` callback if wired |
| `executor.__init__` | Accepts optional `notify_parent_abnormal: Callable[[str], Awaitable[None]]` |
| `executor._execute_process` | At end-of-execute when end_state ≠ `done`, issues cooperative cancel on still-active direct children (orphan safety net for crash-during-opt-out path) |
| `ctx.run_child` | When parent has `auto_cancel_children=True` and cancellation flag is set, refuse: log event, return `"cancelled"`, no child doc created |
| `store` | New helper `list_direct_children(db, prefix, parent_oid, *, states=None)` |

The HTTP cancel endpoint signature, request shape, and response shape do not change. Children's status flips propagate to clients via the existing tree SSE stream.

## Behavior changes visible to users

1. `cancel(parent)` propagates downward to all active descendants by default (the headline change).
2. `parallel_group(survive_failure=False)` becomes fail-fast: first child failure auto-cancels siblings via α. Previously waited for every sibling to complete before raising.
3. `parallel_group(survive_cancel=False)` becomes equivalent: first child cancellation auto-cancels siblings.
4. Cancel grace is now shared across the subtree (single budget from root cancel time).

## Audit — existing tasks and factories

Tasks and factories that spawn children (`ctx.run_child` / `ctx.parallel_group`):

**optio repo (demo only):**

- `packages/optio-demo/src/optio_demo/tasks/festival.py` — `Default True`.
- `packages/optio-demo/src/optio_demo/tasks/home.py` — `Default True`.
- `packages/optio-demo/src/optio_demo/tasks/heist.py` — `Default True`.
- `packages/optio-demo/src/optio_demo/tasks/terraforming.py` — `Default True`.

**excavator repo (production):**

- `packages/engine/src/engine/tasks/full_sync.py` — sequential pipeline; `Default True`.
- `packages/engine/src/engine/tasks/total_sync.py` — sequential; `Default True`.
- `packages/engine/src/engine/tasks/total_metadata_sync.py` — parallel; `Default True`.
- `packages/engine/src/engine/tasks/total_content_sync.py` — parallel; `Default True`.
- `packages/engine/src/engine/analyzers/dummy.py` — phased; `Default True`.
- `packages/engine/src/engine/analyzers/tools.py` — wraps browser-use / Crawl4AI as visibility children; `Default True`. (Browser agent may not cooperate quickly with cancellation; orthogonal to this spec.)

**excavator factories that do not spawn children (flag value moot, use default for forward consistency):**

- `packages/optio-recipe-runner/src/optio_recipe_runner/factory.py` — `Default True`.
- `packages/engine/src/engine/free_style/task.py::make_free_style_task` — `Default True`. (The task already handles `asyncio.CancelledError` to write terminal `analysisState`; no descendants today.)

No existing task currently requires `auto_cancel_children=False`. The flag is introduced for forward stability of the API.

## Race & edge cases

| # | Scenario | Resolution |
|---|---|---|
| R1 | Concurrent `cancel(X)` calls (external + α) | Mongo state transition is the serialization point. First flip wins; others return `not-cancellable`. Both observe the same final state |
| R2 | α invokes `cancel(parent)` while parent's recursion is in-flight from an external cancel | Same as R1 |
| R3 | Supervisor's per-entry force_cancel races with cascade force_cancel on the same descendant | `task.cancel()` idempotent; `_write_force_cancelled_state` is conditional on `state in ACTIVE_STATES` — first writer wins; `_cancellation_flags.pop(oid, None)` is no-op if missing |
| R4 | Supervisor scans entry post-force_cancel before task-finally pops it | Re-issues force_cancel; conditional write returns False; bounded duplicate work |
| R5 | Auto-propagate parent: child spawned by parent's execute fn after cancellation flag is set | `run_child` checks the flag at function entry; if set, returns `"cancelled"` immediately without inserting any DB doc. Logs an event on the parent for traceability |
| R6 | Opt-out parent: child spawned during opt-out window | Allowed. Caught by the force-cancel cascade's DB walk at force time |
| R7 | TaskInstance not in registry at cancel time (e.g., task removed from generator after launch) | Default to `auto_cancel_children=True`. Fail safe toward propagation, not orphan |
| R8 | Grandparent's cancel reaches an opt-out parent mid-tree | Grandparent's recursion stops at opt-out parent. Opt-out parent's children rejoin propagation only at force step via the cascade |
| R9 | Opt-out parent's execute fn raises mid-handling without cleaning up children | `_execute_process` finally writes terminal `failed`; the end-of-execute orphan safety net cancels still-active direct children |

## Test plan

New file `packages/optio-core/tests/test_cancel_propagation.py`. Scenarios:

1. **Basic downward** — A has children B, C running. `cancel(A)` results in B, C, A all reaching `cancelled`.
2. **Opt-out shields children** — A with `auto_cancel_children=False` has running children B, C. `cancel(A)` leaves B, C active until A's execute fn cancels them or finishes.
3. **Recursion honors per-level flag** — A→B→C. B opts out. `cancel(A)` cancels A and B; C remains active.
4. **Upward via α — sibling auto-cancel** — A has children B, C running. External `cancel(B)`. Expect B cancelled, A's downward propagation triggered, C cancelled, A cancelled.
5. **Failure-driven α** — A's `parallel_group(survive_failure=False)` with B (will fail soon), C (long-running). B fails → C cancelled quickly; no longer waits for C.
6. **Force-cancel cascade — auto-propagate path** — Cancel A; B does not cooperate (ignores `should_continue`). After grace, A and B both force-cancelled at the same logical moment.
7. **Force-cancel cascade — opt-out path** — A with `auto_cancel_children=False`. Cancel A. A's execute fn does not cancel B. After grace, A force-cancelled; the cascade catches B → force-cancelled.
8. **Force-cancel cascade — child spawned during opt-out window** — A opts out. Post-`cancel(A)`, A's execute fn spawns D. A does not finish within grace. Force-cancel's DB walk catches D along with A and any other live descendant.
9. **Shared deadline** — verify B, C, D entries created under `cancel(A)` carry equal `entry.deadline` monotonic values.
10. **Idempotency** — concurrent `cancel(A)` calls. One returns ok; others return `not-cancellable`. No state corruption.
11. **Race R5** — auto-propagate parent invokes `run_child` after cancellation flag is set: `run_child` returns `"cancelled"` without creating a child doc. No execute call. Parent log records the refusal event.
12. **Orphan safety net** — opt-out parent's execute fn raises after spawning B without cancelling. Parent ends `failed`. End-of-execute hook cancels B cooperatively.
13. **Existing test regressions** — `test_executor.py`, `test_group_cancel.py`, `test_deadline_cancel.py`, `test_lifecycle_reconciliation.py`, `test_resync_cancel_stale.py`. Particular attention to deadline math.

## Documentation impact

- Root `AGENTS.md` (`packages/optio-core` section): update cancel/state-machine narrative to describe propagation and the `auto_cancel_children` flag.
- `packages/optio-core/AGENTS.md`: update local API reference for `cancel`, `force_cancel`, `TaskInstance`.
- Both updates land in the same commit that introduces the behavior change.

## Out of scope / follow-up

- UI/dashboard exposure of `auto_cancel_children` — no current use case.
- HTTP contract surface — no change.
- Excavator-side flag review for tasks that may later want shielded shutdown (e.g., free-style opencode session if it acquires children).
