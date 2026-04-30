# Design: `group_cancel` / `group_cancel_and_wait` Helpers

**Date:** 2026-04-30
**Base revision:** `370a5ed4003a7f34b4eea86ce5f7d51624ebb2a7` on branch `main` (as of 2026-04-30T11:55:19Z)
**Prerequisite status:** This design assumes the launch-guard work (`Optio.block_launches`, `LaunchBlocked`) and the deadline-driven cancel work (`Optio.cancel_and_wait`, supervisor loop, `OptioConfig.cancel_grace_seconds`) are both available on the base revision above. The integration of those two strands has landed on `main` (see commits `d38e068`, `fc60440`, `c8ea791`, `370a5ed`).
**Scope:** Add two `Optio` helpers — `group_cancel(metadata_filter)` (fire-and-forget) and `group_cancel_and_wait(metadata_filter)` (waits for terminal state) — that cancel every active process matching a metadata filter. Pure orchestration over existing primitives; no new internal mechanism.

## Motivation

Downstream consumers need to "drain" all in-flight work scoped by a metadata filter. Two distinct use cases exist:

1. **Caller wants to know when the work is fully terminated.** Teardown handlers that need to release resources only after every matching process has reached a terminal state. Maps to `group_cancel_and_wait`.
2. **Caller wants to issue cancels and move on.** Notably, a task that wants to cancel its own scope cannot wait for itself — it would deadlock until its own force-cancel deadline fires. Self-cancel is a natural use case for the fire-and-forget variant. Maps to `group_cancel`.

This pair mirrors the existing single-pid pair `cancel(pid)` / `cancel_and_wait(pid)`. The naming convention extends cleanly to group operations.

## Public API

```python
class Optio:
    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state.

        Behaviour:
          1. (If block_new_launches=True) register a launch guard for
             `metadata_filter` that stays active for the duration of this
             call (i.e. through the leak sweep, then released on return).
          2. Snapshot active processes matching the filter.
          3. Issue `cancel(process_id)` for each in parallel.
          4. (If block_new_launches=True) leak sweep: after a 100 ms settling
             delay, re-list and cancel any matching pids that completed their
             upsert after the snapshot.
          5. Return.

        Use this for self-cancel: a task whose own metadata matches the
        filter can call `group_cancel` safely — the cooperative flag is set
        and the call returns, then the task unwinds at its next yield /
        cancel-check.

        Args:
            metadata_filter: Required. Non-empty AND-equality dict scoping
                the work to cancel. `{}` / `None` is rejected (use
                `Optio.shutdown()` to drain everything).
            block_new_launches: When True, equivalent to wrapping the call
                in `async with self.block_launches(metadata_filter): ...`.
                The guard is active for the duration of this call (snapshot,
                cancel issuance, leak sweep) and released on return.

        Raises ValueError if `metadata_filter` is None or empty.
        """

    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter` and wait
        for all of them to reach a terminal state.

        **Do not call from inside a task whose metadata matches the filter.**
        Your task will be force-cancelled when its own cancel deadline fires,
        and this call will not return normally. Use `group_cancel` instead
        for self-cancel.

        Behaviour: identical to `group_cancel` for steps 1–4, then waits
        until every snapshotted (and leak-swept) pid reaches a terminal
        state. Cooperative tasks unwind within `cancel_grace_seconds`;
        stubborn tasks are force-cancelled by the supervisor.

        Returns once all in-scope pids are terminal.

        Raises asyncio.TimeoutError if any pid has not reached terminal
        within `cancel_grace_seconds + 25s` (hardcoded internal ceiling,
        same backstop as `cancel_and_wait`).
        Raises ValueError if `metadata_filter` is None or empty.
        """
```

Both exported from `optio_core/__init__.py` alongside the other public methods.

The signature deliberately requires a non-empty `metadata_filter`. Passing `{}` would match everything; if a caller really wants "drain all active tasks," they should call `Optio.shutdown()` instead.

**Why an optional `block_new_launches` flag instead of two callsites?** The "drain + block" pattern is the primary use case for teardown handlers, and the flag avoids the small but real race window between `async with self.block_launches(filter):` and the helper's own snapshot if a caller forgets to nest them in the right order. With the flag, the launch guard is registered *before* the snapshot, in one call, with one filter — the order is correct by construction.

## Implementation sketch

The two methods share the snapshot + cancel + leak-sweep logic. Extract it into a private `_group_cancel_issue`; the two public methods compose on top.

```python
import time
import asyncio
from contextlib import AsyncExitStack

async def _group_cancel_issue(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool,
) -> list[str]:
    """Snapshot, cancel, optionally leak-sweep. Returns the list of
    process_ids that were cancelled (snapshot + leaked).

    Caller is responsible for the launch guard's AsyncExitStack — this
    helper assumes the guard is already active when called with
    block_new_launches=True.
    """
    # 1. Snapshot active processes matching the filter.
    procs = await self.list_processes(metadata=metadata_filter)
    active = [p for p in procs if p["status"]["state"] in ACTIVE_STATES]

    # 2. Issue cancellations in parallel. cancel() is non-blocking — it
    #    sets the cooperative flag + deadline and returns; it does not
    #    await termination. Per-pid Mongo writes are independent, so
    #    concurrency is safe. cancel() is also idempotent if a process is
    #    already in `cancel_requested` / `cancelling` / terminal —
    #    first-wins on the deadline.
    #
    #    Error semantics: gather (with default return_exceptions=False)
    #    matches the sequential-loop semantics — if any cancel raises,
    #    the helper aborts; pids whose cancel writes already landed are
    #    cancelled, others are untouched. The spec defines no rollback.
    if active:
        await asyncio.gather(*(self.cancel(p["processId"]) for p in active))

    pending_ids = [p["processId"] for p in active]

    # 3. Leak sweep (only with block_new_launches=True). Catches launches
    #    that passed `_check_launch_blocks` *before* the guard registered
    #    but completed their `upsert_process` *after* our snapshot — i.e.
    #    launches that are neither rejected by the guard nor included in
    #    the wait set yet. After a 100 ms settling delay, all such
    #    in-flight upserts have landed under normal scheduling, so a
    #    single re-list catches the population (which is finite and fixed
    #    at the moment the guard activated). Cancel the leaked pids and
    #    fold them into the returned id list.
    if block_new_launches:
        await asyncio.sleep(0.1)
        latest = await self.list_processes(metadata=metadata_filter)
        known = set(pending_ids)
        leaked = [
            p for p in latest
            if p["status"]["state"] in ACTIVE_STATES
            and p["processId"] not in known
        ]
        if leaked:
            await asyncio.gather(
                *(self.cancel(p["processId"]) for p in leaked)
            )
            pending_ids.extend(p["processId"] for p in leaked)

    return pending_ids


async def group_cancel(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
) -> None:
    if not metadata_filter:
        raise ValueError(
            "group_cancel requires a non-empty metadata_filter; "
            "use Optio.shutdown() to drain everything."
        )
    async with AsyncExitStack() as stack:
        if block_new_launches:
            await stack.enter_async_context(self.block_launches(metadata_filter))
        await self._group_cancel_issue(metadata_filter, block_new_launches)


async def group_cancel_and_wait(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
) -> None:
    if not metadata_filter:
        raise ValueError(
            "group_cancel_and_wait requires a non-empty metadata_filter; "
            "use Optio.shutdown() to drain everything."
        )
    async with AsyncExitStack() as stack:
        if block_new_launches:
            await stack.enter_async_context(self.block_launches(metadata_filter))
        pending = await self._group_cancel_issue(metadata_filter, block_new_launches)
        if not pending:
            return

        # Wait for every in-scope pid to reach a terminal state. Use the
        # same internal ceiling as cancel_and_wait for consistency:
        # cooperative tasks finish well inside the grace window; the
        # supervisor force-cancels stubborn ones; the ceiling is a
        # backstop against supervisor or DB anomalies.
        #
        # Strategy: single forward-walking pointer over `pending`. Each
        # tick checks the current pid only; advances on terminal, sleeps
        # on active. Helper's contract is "wait for ALL terminal," so
        # total wall time = max(t_i) regardless of check order, and this
        # gives 1 Mongo round-trip per tick in the steady state instead
        # of N. When the current pid flips terminal, advance through any
        # already-terminal successors in the same tick (each advance is
        # one find_one, no extra wall time).
        ceiling = self._config.cancel_grace_seconds + 25.0
        deadline = time.monotonic() + ceiling
        i = 0
        while i < len(pending):
            proc = await self.get_process(pending[i])
            if proc is None or proc["status"]["state"] not in ACTIVE_STATES:
                i += 1
                continue
            if time.monotonic() >= deadline:
                remaining = len(pending) - i
                raise asyncio.TimeoutError(
                    f"group_cancel_and_wait: {remaining} process(es) "
                    f"did not reach a terminal state within {ceiling}s "
                    f"(filter={metadata_filter})"
                )
            await asyncio.sleep(0.1)

        # AsyncExitStack lifts the launch guard (if any) on exit — both on
        # normal return and on exception (e.g. asyncio.TimeoutError above).
```

Notes:

- The snapshot is taken **once** per call (after the optional launch guard is registered, before any cancellation). New processes that arrive after the snapshot — outside of the bucket-(c) leak sweep — are deliberately not part of the cancel set. This is correct: the helper exists to terminate work that was running *at the moment of invocation*; preventing new work is the launch guard's job.
- With `block_new_launches=True`, launches matching the filter fall into one of three buckets: (a) upserted before the snapshot — included; (b) called `_check_launch_blocks` after the guard registered — rejected with `LaunchBlocked`; (c) raced past the check before the guard registered but completed their upsert after the snapshot. The leak-sweep step addresses bucket (c): after a 100 ms settling delay (long enough for any in-flight `upsert_process` started before the guard activated to land under normal scheduler/Mongo latencies), the helper re-lists and folds any new in-scope pids into the cancel set. The population of bucket (c) is finite and fixed at the moment the guard activated, so a single sweep is sufficient under normal conditions. Pathological exception: if the event loop is saturated or a Mongo write hangs >100 ms, a leak can still slip past the sweep — callers needing absolute drain guarantees should hold an outer `block_launches` with a wider scope (e.g. covering both the helper and any upstream work that could spawn matching launches).
- Polling cadence (100 ms) and ceiling buffer (+25 s) match `cancel_and_wait` for consistency. The internal mechanism differs (`cancel_and_wait` polls one fixed pid; `group_cancel_and_wait` walks a list of pids with a forward-only pointer) but observable behavior — when the call returns, what raises, on what timer — is identical.
- `group_cancel_and_wait` relies on `cancel()` having deadline-driven force-cancel semantics. Without that prerequisite, a stubborn task would keep the pointer parked on it until the ceiling fires — these helpers should not be merged before the deadline-driven cancel mechanism lands.

## State semantics

Cooperative tasks → terminal state `cancelled`. Force-cancelled (deadline exceeded) → terminal state `failed` with the canonical error string `"Task did not unwind within cancellation grace period"` (set by the deadline-cancel supervisor / `_write_force_cancelled_state`). `group_cancel_and_wait` returns once every in-scope pid is terminal. `group_cancel` returns as soon as the cancel writes have landed (and, with `block_new_launches=True`, after the leak sweep) — terminal state may not yet be reached when it returns.

Neither helper sets state itself; they only cancel and (in the waiter's case) poll.

## Edge cases

- **No active processes match.** The snapshot is empty. `group_cancel` returns immediately (after the optional leak sweep, which may add bucket-(c) pids); `group_cancel_and_wait` returns immediately as well unless the leak sweep adds pids, in which case it waits for them.
- **Process record deleted mid-wait.** `get_process` returns `None`. Treat as terminal (advance pointer past it). Applies to `group_cancel_and_wait` only.
- **Caller cancels the helper coroutine.** `await asyncio.sleep(0.1)` is the cancellation point; the helper unwinds promptly. The cancellations it has already issued continue independently — caller cancellation does not roll them back.
- **Process moves between active states (e.g. `running` → `cancel_requested` → `cancelling`).** Still active; pointer keeps polling. (Waiter only.)
- **Snapshot pid already terminal at first check.** Pointer advances on the first read with no sleep. Free. (Waiter only.)
- **Self-cancel via `group_cancel`.** A task whose own metadata matches the filter can call `group_cancel` — cancel flag is set on its own row, the method returns, then the task unwinds at its next cancel-check. Self-cancel via `group_cancel_and_wait` is undefined-and-unsupported (will deadlock until force-cancel fires, then raise CancelledError out of the call).

## Tests

New test file: `packages/optio-core/tests/test_group_cancel.py`.

### Shared cases (test both helpers)

1. **No active processes.** Filter matches nothing; both helpers return without error. (`group_cancel_and_wait` returns immediately; `group_cancel` returns immediately. With `block_new_launches=True` add a brand-new no-op launch and confirm the leak sweep adds it to the cancel set.)
2. **Out-of-scope tasks untouched.** Two tasks: one matches the filter, one doesn't. Both helpers cancel only the first; the second is still in its prior state when the helper returns.
3. **Empty/None filter rejected.** `group_cancel(None)`, `group_cancel({})`, `group_cancel_and_wait(None)`, `group_cancel_and_wait({})` all raise `ValueError`. With and without `block_new_launches=True`.
4. **`block_new_launches=True` rejects new launches during the call.** Start a cooperative task matching the filter; call the helper with `block_new_launches=True`; from a separate coroutine, attempt `Optio.launch_and_wait(other_pid)` for a task whose metadata also matches the filter — assert `LaunchBlocked` is raised. After the helper returns, the same launch call succeeds (guard lifted).
5. **`block_new_launches=False` does NOT register a launch guard.** Capture `set(self._launch_blocks.keys())` before the call; assert the same set after (i.e. no token added/leaked). Run for both helpers.
6. **Leak-sweep catches a bucket-(c) launch.** With `block_new_launches=True`, simulate a launch that passed `_check_launch_blocks` before the guard registered but completes its `upsert_process` *after* the helper's initial snapshot. (Achievable by injecting an `await asyncio.sleep(0)` between `_check_launch_blocks` and `upsert_process` for that specific call so the helper can interleave.) Assert the helper still cancels the leaked pid. For `group_cancel_and_wait`, also assert it is terminal before the call returns.
7. **Leak-sweep is a no-op when there is nothing to leak.** With `block_new_launches=True` and no concurrent in-flight launches, the leak sweep adds zero pids.

### `group_cancel`-specific cases

8. **Returns before terminal state.** Start a cooperative task with a `cancel_grace_seconds`-sized sleep before the cancel-check. Call `group_cancel(filter)` and verify it returns *before* the task is in `cancelled` state. (Demonstrates fire-and-forget semantics.)
9. **Self-cancel.** Inside a task whose metadata matches the filter, call `group_cancel(filter)`. Assert: the call returns; the task continues until its next `await` / cancel-check; then unwinds cooperatively to `cancelled` (or, if it ignores the flag, `failed` after the deadline). No deadlock, no `asyncio.TimeoutError` from the helper.

### `group_cancel_and_wait`-specific cases

10. **All cooperative.** Several tasks running, all respect `ctx.cancellation_flag`. Helper returns within `cancel_grace_seconds`. All processes end in `cancelled`.
11. **Mixed cooperative + stubborn.** Some tasks ignore the flag (busy-loop). Helper returns within `cancel_grace_seconds + small force-cancel buffer`. Cooperative tasks end `cancelled`, stubborn tasks end `failed` with the canonical error string.
12. **Internal ceiling backstop.** Patch the deadline-cancel supervisor to no-op (so a stubborn task is never force-finalized). Helper raises `asyncio.TimeoutError` once the internal ceiling expires.
13. **`block_new_launches=True` lifts the guard on exception.** Force the helper to raise `asyncio.TimeoutError` (same setup as case 12) with `block_new_launches=True`. After the raise, capture-and-compare `_launch_blocks` keys to confirm no token leaked.
14. **New launches during the wait are NOT included (default behaviour).** Start a task matching the filter, call `group_cancel_and_wait(filter)` with `block_new_launches=False`, then *during the wait* start a second matching task. Verify the helper returns once the *original* task is terminal, even though the second is still running. (Snapshot semantics with no leak sweep.)

## Migration impact

- **Public API.** Pure addition: two new methods on `Optio`, both exported from `optio_core/__init__.py`. No existing caller breaks.
- **Wire protocol.** No changes.
- **Internal types.** No changes.
- **Performance.** `group_cancel` issues one `list_processes` + N parallel cancel writes (and, with `block_new_launches=True`, one extra `list_processes` + a 100 ms sleep). `group_cancel_and_wait` adds the wait loop: one Mongo `find_one` per 100 ms tick in the steady state (single forward-walking pointer over `pending`), worst-case +N reads total as the pointer advances through terminal pids. Negligible.

## Out of scope

- **Re-cancelling new arrivals under `block_new_launches=False`.** When the guard is off, post-snapshot launches are deliberately the caller's responsibility — snapshot semantics. Pass `block_new_launches=True` (or hold an outer `block_launches`) to prevent new arrivals.
- **Customizable ceiling / poll cadence.** Hardcoded for consistency with `cancel_and_wait`.
- **Returning a result list of `(process_id, terminal_state)` pairs.** Could be added later if a caller needs it; for now the simpler `-> None` shape is sufficient (callers can `list_processes` after the call to inspect state).
- **Detecting `group_cancel_and_wait` self-call at runtime.** The docstring warns; no runtime check. If a caller violates it, they get a `CancelledError` once their own deadline fires. Adding runtime detection would require plumbing the current task's process_id into the helper, which is not currently exposed on the public surface — defer until a real caller needs it.

## Usage examples

**Teardown handler (drain + block, wait for completion):**

```python
await optio_core.group_cancel_and_wait(
    {"some_metadata_key": some_value},
    block_new_launches=True,
)
```

**Self-cancel from inside a task (fire-and-forget):**

```python
async def my_task(ctx):
    ...
    if some_condition:
        # Cancel this task's whole group, including itself. Returns
        # immediately; this task unwinds at its next yield point.
        await optio_core.group_cancel({"team": "alpha"})
    ...
```

**Outer guard with longer-than-call lifetime:**

```python
async with optio_core.block_launches({"team": "alpha"}):
    await optio_core.group_cancel_and_wait({"team": "alpha"})
    # ... do other shutdown work; new launches still blocked ...
```
