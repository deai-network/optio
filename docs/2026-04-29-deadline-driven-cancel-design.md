# Design: Deadline-Driven Cooperative Cancel

**Date:** 2026-04-29
**Base revision:** `191fd39f5880a7c13f06ed0daa1b174d1cb8703c` on branch `csillag/project-removal` (as of 2026-04-29T18:16:24Z)
**Scope:** Make every `Optio.cancel()` deadline-enforced. Cooperative request first, then a single global grace period, then force-cancel via `asyncio.Task.cancel()` and a conditional Mongo update. Replaces the current "task may stay in `cancelling` indefinitely" behaviour.

## Motivation

Today `Optio.cancel(process_id)` writes `cancel_requested` to Mongo and sets an `asyncio.Event` flag in the executor. The task is expected to check the flag at await points and unwind cooperatively. If the task ignores the flag — busy loop, blocked external call, bare-except clause — the process stays in `cancelling` until the engine restarts (where reconciliation marks it `failed`).

The bug surfaces in two ways:

1. **Operational paralysis.** A user requests a cancel and sees no progress. There is no automatic remediation; only restart.
2. **Downstream features blocked.** Excavator's project-delete flow needs a guaranteed-terminal-state cancel before it can cascade-delete domain data; without that, force-finalize logic has to be duplicated per feature (see `~/deai/excavator/docs/2026-04-29-project-delete-design.md`).

This change moves the discipline from per-feature force-finalize logic into the cancel mechanism itself, so every caller of `cancel()` gets the same termination guarantee for free.

## Public API

Two methods on `Optio`. Existing `cancel()` keeps its name and current non-blocking semantics; a new `cancel_and_wait()` provides the synchronous-feeling path.

```python
class Optio:
    async def cancel(self, process_id: str) -> None:
        """Cooperative cancel + deadline. Non-blocking. Idempotent.

        First call records deadline = now + cancel_grace_seconds, sets the
        cooperative cancel flag, and writes `cancel_requested`/`cancelling`
        to Mongo as before.

        Subsequent calls before the process reaches a terminal state are
        no-ops on the deadline (first wins). The deadline cannot be
        refreshed by repeated calls.
        """

    async def cancel_and_wait(self, process_id: str) -> str | None:
        """Cancel and wait until the process reaches a terminal state.

        Returns the terminal state string ('cancelled', 'failed', 'done',
        ...). Returns None if the process does not exist.

        Raises asyncio.TimeoutError if the process has not reached a
        terminal state within an internal hard ceiling
        (cancel_grace_seconds + 25s, hardcoded — not configurable). The
        ceiling exists only as a backstop against supervisor or DB
        anomalies; under normal conditions the supervisor force-cancels
        non-cooperators well before it.
        """
```

`OptioConfig` gains one field:

```python
@dataclass
class OptioConfig:
    ...
    cancel_grace_seconds: float = 5.0
```

It is set once at `init()` time (via the new keyword argument on `Optio.init`, defaulting to 5.0) and is the same for every cancel during that Optio lifetime. There is no per-task override. System commands; tasks obey.

## Internal mechanism — `Executor`

### Task handle tracking

`Executor` gains a parallel registry to `_cancellation_flags`:

```python
class Executor:
    def __init__(...):
        ...
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
```

Where `_CancelEntry` is a small dataclass:

```python
@dataclass
class _CancelEntry:
    flag: asyncio.Event
    deadline: float | None  # monotonic time; None until cancel() is called
```

The value type of `_cancellation_flags` changes from `asyncio.Event` to `_CancelEntry`. Every existing read of the flag (`flag.set()`, `flag.is_set()`) is updated to access `entry.flag` instead.

`asyncio.current_task()` is captured at the top of `_execute_process`:

```python
async def _execute_process(self, proc, execute_fn, ...):
    oid = proc["_id"]
    cancel_flag = asyncio.Event()
    self._cancellation_flags[oid] = _CancelEntry(flag=cancel_flag, deadline=None)
    self._running_tasks[oid] = asyncio.current_task()
    try:
        ... existing body ...
    finally:
        self._cancellation_flags.pop(oid, None)
        self._running_tasks.pop(oid, None)
```

The `try/finally` is mandatory: when force-cancel injects `CancelledError`, the existing `except Exception` clause does not catch it (Python ≥3.8: `CancelledError` extends `BaseException`). The finally clause guarantees registry cleanup regardless of unwind path.

### `force_cancel`

New method:

```python
async def force_cancel(self, oid: ObjectId) -> None:
    """Hard-cancel a process whose cooperative deadline has expired.

    Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
    wait for it to actually unwind, then writes the conditional terminal
    state to Mongo. Used only by the Optio-level supervisor and by
    shutdown.
    """
    task = self._running_tasks.get(oid)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            # Timeout: thread-blocked or stubborn; we proceed regardless.
            # CancelledError: task acknowledged cancellation; proceed.
            # Other exceptions are not our concern here — Mongo update
            # below is the source of truth.
            pass
    await _write_force_cancelled_state(self._db, self._prefix, oid)
```

`_write_force_cancelled_state` is the shared helper that performs the conditional Mongo update + log entry. See "Shared helper" below.

## Internal mechanism — supervisor

`Optio` gains a single supervisor coroutine, started in `run()` and stopped in `shutdown()`.

```python
async def _supervisor_loop(self) -> None:
    """Scan for past-deadline cancellations every 500 ms; force-cancel them."""
    while self._running:
        try:
            now = time.monotonic()
            for oid, entry in list(self._executor._cancellation_flags.items()):
                if entry.deadline is None:
                    continue
                if now < entry.deadline:
                    continue
                # Past deadline — force-cancel. The executor cleans up its
                # own registry entries via the try/finally in _execute_process.
                await self._executor.force_cancel(oid)
        except Exception as e:
            logger.exception(f"Supervisor loop error: {e}")
        await asyncio.sleep(0.5)
```

Lifecycle:

- **Start.** In `Optio.run()`, alongside the heartbeat task: `self._supervisor_task = asyncio.create_task(self._supervisor_loop())`.
- **Stop.** In `Optio.shutdown()`, after the unified cancel-everything sweep completes, the supervisor task is cancelled and awaited:
  ```python
  if self._supervisor_task:
      self._supervisor_task.cancel()
      try:
          await self._supervisor_task
      except asyncio.CancelledError:
          pass
      self._supervisor_task = None
  ```

The 500 ms cadence is hardcoded. Any per-cancel timer would multiply task overhead for no gain; one centralized scanner is simplest.

### Why `asyncio.shield` in `force_cancel`

`force_cancel` is itself called from within the supervisor task. If that supervisor task gets cancelled mid-`force_cancel` (e.g. during shutdown), we still want the in-flight `task.cancel()` + Mongo update to complete cleanly. `asyncio.shield(task)` lets the supervisor's await on the cancelled child task be cancellable while the child task itself proceeds.

## `cancel()` and `cancel_and_wait()` implementation

The existing `Optio._handle_cancel(payload)` (lifecycle.py:457-491) is the canonical implementation of cancel; it is invoked both by the Redis "cancel" command and by the public `Optio.cancel(process_id)` (which currently delegates: `await self._handle_cancel({"processId": process_id})`). The deadline-recording change happens inside `_handle_cancel`, so both call paths get the same behaviour.

Updated `_handle_cancel`:

```python
async def _handle_cancel(self, payload: dict) -> None:
    process_id = payload.get("processId")
    if not process_id:
        return
    proc = await get_process_by_process_id(...)
    if proc is None:
        return
    current_state = proc["status"]["state"]
    if current_state not in CANCELLABLE_STATES:
        return  # already terminal or already cancelling

    # Existing flow: scheduled goes directly to cancelled; running goes
    # via cancel_requested → cancelling.
    if current_state == "scheduled":
        await update_status(..., ProcessStatus(state="cancelled", ...))
        return

    await update_status(..., ProcessStatus(state="cancel_requested"))
    found = self._executor.request_cancel_with_deadline(
        proc["_id"],
        deadline=time.monotonic() + self._config.cancel_grace_seconds,
    )
    if found:
        await update_status(..., ProcessStatus(state="cancelling"))
```

Public `Optio.cancel()` keeps its delegation to `_handle_cancel`. No additional changes there.

The Executor gains `request_cancel_with_deadline(oid, deadline)`:

```python
def request_cancel_with_deadline(self, oid, deadline) -> bool:
    entry = self._cancellation_flags.get(oid)
    if entry is None:
        return False
    entry.flag.set()
    if entry.deadline is None:        # first-wins: do not refresh
        entry.deadline = deadline
    return True
```

The existing `request_cancel(oid)` is removed; `cancel()` is the only caller and now passes a deadline.

`Optio.cancel_and_wait()`:

```python
async def cancel_and_wait(self, process_id: str) -> str | None:
    proc = await get_process_by_process_id(...)
    if proc is None:
        return None

    await self.cancel(process_id)

    ceiling = self._config.cancel_grace_seconds + 25.0
    deadline = time.monotonic() + ceiling
    while True:
        proc = await get_process_by_process_id(...)
        if proc is None:
            return None
        state = proc["status"]["state"]
        if state not in ACTIVE_STATES:
            return state
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError(
                f"Process {process_id} did not reach terminal state within {ceiling}s"
            )
        await asyncio.sleep(0.1)
```

The 25 s buffer above `cancel_grace_seconds` is generous on purpose: it covers force-cancel's 2 s bounded wait, supervisor's 500 ms scan jitter, Mongo write latency, and any reasonable unwind. The ceiling exists strictly as a backstop against supervisor bugs / DB stalls. Under normal operation the loop terminates in `cancel_grace_seconds + ~1 s`.

## Shared helper

`_force_finalize_stuck_processes` (currently in `lifecycle.py`) factors into a small private function:

```python
async def _write_force_cancelled_state(
    db: AsyncIOMotorDatabase, prefix: str, oid: ObjectId
) -> bool:
    """Conditional Mongo update: if the process is still in ACTIVE_STATES,
    set status to 'failed' with the canonical force-cancel error, clear
    widgetUpstream, and append a log entry. Returns True if updated.

    The conditional is load-bearing: a task that won the race to a terminal
    state owns its own state transition; we never overwrite it.
    """
    coll = db[f"{prefix}_processes"]
    now = datetime.now(timezone.utc)
    error_msg = "Task did not unwind within cancellation grace period"
    status_doc = ProcessStatus(
        state="failed", error=error_msg, failed_at=now,
    ).to_dict()
    result = await coll.update_one(
        {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
        {"$set": {"status": status_doc, "widgetUpstream": None}},
    )
    if result.modified_count:
        await append_log(
            db, prefix, oid,
            "event",
            "State forced: running -> failed (cancellation grace period exceeded)",
        )
        return True
    return False
```

Both `Executor.force_cancel` and the unified `shutdown()` path call this helper. There is no other code path that writes a forced terminal state.

The error string `"Task did not unwind within cancellation grace period"` is canonical: ops dashboards search for it to find tasks that need to be retrofitted to honour the cooperative flag.

## `shutdown()` unified

The current shutdown does its own bulk-flag-set + grace + force-finalize. After this change:

```python
async def shutdown(self, grace_seconds: float | None = None) -> None:
    logger.info("Shutdown requested")
    self._running = False

    # 1. Heartbeat
    if self._heartbeat_task:
        self._heartbeat_task.cancel()
        ...

    # 2. Consumer
    if self._consumer:
        self._consumer.stop()
    if hasattr(self, '_shutdown_event'):
        self._shutdown_event.set()

    # 3. Cancel everything via the unified mechanism.
    grace = grace_seconds if grace_seconds is not None else self._config.cancel_grace_seconds
    if self._executor:
        oids_to_wait = list(self._executor._cancellation_flags.keys())
        for oid in oids_to_wait:
            entry = self._executor._cancellation_flags.get(oid)
            if entry is None:
                continue
            entry.flag.set()
            if entry.deadline is None:
                entry.deadline = time.monotonic() + grace

        # Let the supervisor handle force-cancel; we just wait until all
        # entries drain or the ceiling hits.
        deadline_ceiling = time.monotonic() + grace + 5.0
        while self._executor._cancellation_flags and time.monotonic() < deadline_ceiling:
            await asyncio.sleep(0.1)

    # 4. Stop supervisor
    if self._supervisor_task:
        self._supervisor_task.cancel()
        try:
            await self._supervisor_task
        except asyncio.CancelledError:
            pass
        self._supervisor_task = None

    # 5. Scheduler, Redis (existing)
    ...
```

Notes:

- The `grace_seconds` keyword argument on `shutdown()` is kept for backwards compatibility. When not provided, it defaults to `cancel_grace_seconds`. When provided, it overrides it for that one shutdown call's deadline only.
- `_force_finalize_stuck_processes` is removed; its callers point at the unified mechanism. Any direct callers in tests or external integrations need to be updated.
- The shutdown sweep does **not** call `Optio.cancel(pid)` per process. That public method touches Mongo state, and shutdown wants to be fast. Instead it sets the flag + deadline directly on each entry; the supervisor's normal scan handles force-cancel; the conditional Mongo update converges state. Same correctness, fewer Mongo round-trips.

## State semantics

- Cooperative completion (task respects flag, returns within deadline): terminal state `cancelled`, no error.
- Force-cancelled (deadline exceeded, `Task.cancel()` issued, conditional update wins): terminal state `failed`, `status.error = "Task did not unwind within cancellation grace period"`.
- Force-cancel race (cooperative end happens to fire after deadline but before our conditional update): the cooperative path's terminal write transitions out of `ACTIVE_STATES`; our conditional update sees the wrong state and no-ops; the cooperative `cancelled` state stands.

No new state is introduced. Process list views and ops dashboards observe force-cancelled tasks as failures with a distinctive, canonical error string — easy to search, filter, and alert on.

## Behaviour with `asyncio.to_thread` and other thread-blocked tasks

`Task.cancel()` does not interrupt a thread running synchronous code. The Python coroutine's await on `asyncio.to_thread(...)` will not return until the underlying thread function finishes; only then does the cancellation take effect.

For excavator:

- The conditional Mongo update inside `force_cancel` succeeds regardless — state moves to `failed` immediately. The process record is correct from the database's perspective.
- The orphaned thread continues until it returns. Anything it writes to Mongo afterwards lands in collections that may have been emptied (project-delete cascade) or in the now-`failed` process record (where `clear_widget_upstream` etc. may overwrite it).

This is a known limitation. Mitigations are out of scope for this spec; flagged for downstream specs that touch hot paths (e.g. project-delete's race B documentation).

## Testing

New test file: `~/deai/optio/packages/optio-core/tests/test_deadline_cancel.py`.

Required test cases:

1. **Cooperative cancel.** Task respects `ctx.cancellation_flag`, returns within deadline. Final state is `cancelled`. No error string.
2. **Stubborn cancel.** Task ignores the flag (busy loop with `await asyncio.sleep(0.01)`). After `cancel_grace_seconds`, the supervisor force-cancels. Final state is `failed`, `status.error` contains the canonical phrase. The asyncio Task object reports `task.cancelled() is True`.
3. **Re-entry idempotency.** Two `cancel(pid)` calls separated by 1 s; second call does not refresh the deadline. Total time to terminal state ≤ first deadline + force-cancel buffer.
4. **`cancel_and_wait` returns terminal state.** Both cooperative (`'cancelled'`) and stubborn (`'failed'`) cases.
5. **`cancel_and_wait` on missing process_id returns None.**
6. **`cancel_and_wait` raises TimeoutError.** Achieved by patching `force_cancel` to no-op while leaving the process active. The internal ceiling fires; `asyncio.TimeoutError` is raised. Verifies the backstop.
7. **Already-terminal short-circuit.** `cancel(pid)` on a `done` or `failed` process is a no-op. `cancel_and_wait(pid)` returns the existing terminal state immediately.
8. **Shutdown unification.** Mixed cooperative + stubborn tasks. After `shutdown()`, all are in terminal state. Cooperators end `cancelled`; stubborn end `failed` with the canonical error.
9. **Shutdown override.** `shutdown(grace_seconds=0.5)` honours the smaller grace; tasks unwound within 0.5 s end `cancelled`, others end `failed`.
10. **`asyncio.to_thread`-blocked task.** Document the limitation. Test asserts the Mongo state reaches `failed` within the grace window; does not assert thread death. (The thread is allowed to be running when the test ends — pytest's event loop teardown will outlive it briefly. Use a thread that returns after a short sleep so the thread eventually exits before the test process exits.)

Existing tests of `Optio.cancel()` and `Optio.shutdown()` are reviewed and updated where they relied on the old "cancelling forever" behaviour. The renamed/removed `_force_finalize_stuck_processes` may break a test or two; those are updated to assert the same behavioural property via `_write_force_cancelled_state` or via the supervisor-driven path.

## Migration impact

- **Public API.** `OptioConfig.cancel_grace_seconds` is added with a default; no caller breaks. `Optio.cancel()` semantics change strictly more correctly — no caller relied on the previous bug. New `Optio.cancel_and_wait()` is purely additive.
- **Wire protocol.** No changes to the Redis stream payloads, command names, or Mongo schema. No DB migration.
- **Internal types.** `Executor._cancellation_flags` value type changes from `asyncio.Event` to `_CancelEntry`. Any external code that touched this private dict (none expected) breaks.
- **Removed symbols.** `_force_finalize_stuck_processes` removed; replaced with `_write_force_cancelled_state` plus the supervisor-driven path. `Executor.request_cancel(oid)` removed in favour of `request_cancel_with_deadline(oid, deadline)`.

Downstream consumers known to be affected:

- `~/deai/excavator/docs/2026-04-29-project-delete-design.md` (project-removal feature) — relies on this spec landing first. Cross-references this design's path.

## Out of scope

- **Per-task grace overrides.** Explicitly rejected. Tasks do not set their own deadlines; the system imposes them.
- **Launch guard / blocking new launches during teardown windows.** A separate optio-core spec, separate branch, sequenced after this one.
- **Multi-engine semantics.** The supervisor and `force_cancel` only operate on tasks the local engine is running. In a multi-engine deployment, a cancel arriving at the wrong engine still updates Mongo correctly (state machine + conditional update), but force-cancel of the asyncio Task only happens locally. Excavator runs a single engine today; multi-engine is a future spec.
- **Notification of `cancel_and_wait` callers via push.** The current design uses 100 ms polling. A future change could replace polling with a per-process `asyncio.Event` set by the executor on terminal transition; out of scope here.

## Cross-repo coordination

This spec lives in `~/deai/optio/docs/`. Consumers in other repos:

- **Excavator project-delete.** Waits for this to land + version pin bump in `packages/engine/pyproject.toml`. After that, excavator's `gracefully_terminate_tasks` becomes a thin wrapper relying on this mechanism.
- **Other optio-core consumers** (none currently in the deai monorepo): no breakage; the API surface is additive.
