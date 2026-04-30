# Design: `gracefully_terminate_tasks` Helper

**Date:** 2026-04-30
**Base revision:** `370a5ed4003a7f34b4eea86ce5f7d51624ebb2a7` on branch `main` (as of 2026-04-30T11:55:19Z)
**Prerequisite status:** This design assumes the launch-guard work (`Optio.block_launches`, `LaunchBlocked`) and the deadline-driven cancel work (`Optio.cancel_and_wait`, supervisor loop, `OptioConfig.cancel_grace_seconds`) are both available on the base revision above. The integration of those two strands has landed on `main` (see commits `d38e068`, `fc60440`, `c8ea791`, `370a5ed`).
**Scope:** Add a single `Optio.gracefully_terminate_tasks(metadata_filter)` helper that cancels every active process matching a metadata filter and waits for all of them to reach a terminal state. Pure orchestration over existing primitives — no new internal mechanism.

## Motivation

Downstream consumers need to "drain" all in-flight work scoped by a metadata filter before tearing down the underlying resources. The two existing primitives —

- `Optio.cancel_and_wait(process_id)` — deadline-enforced cancel, returns once the process is in a terminal state (or raises `asyncio.TimeoutError` on the internal hard ceiling).
- `Optio.list_processes(metadata=...)` — query active processes by metadata filter.

— compose into the desired behavior, but every caller would write the same loop. This helper centralizes it.

## Public API

```python
class Optio:
    async def gracefully_terminate_tasks(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter` and wait for
        all of them to reach a terminal state.

        Behaviour:
          1. (If block_new_launches=True) register a launch guard for
             `metadata_filter` that stays active for the duration of this
             call. New launches matching the filter raise LaunchBlocked
             until the helper returns.
          2. Snapshot active processes matching the filter (state ∈ ACTIVE_STATES).
          3. Issue `cancel(process_id)` for each — sets the cooperative flag and
             records the deadline. Non-blocking.
          4. Wait until every process in the snapshot reaches a terminal state.
             Cooperative tasks unwind within `cancel_grace_seconds`. Stubborn
             tasks are force-cancelled by the supervisor (see deadline-driven
             cancel design).
          5. (If block_new_launches=True) the launch guard is lifted on
             return (or on exception, via the context manager protocol).

        Returns once all snapshotted processes are terminal.

        Raises asyncio.TimeoutError if any snapshotted process has not reached
        a terminal state within an internal hard ceiling
        (`cancel_grace_seconds + 25s`, hardcoded — same backstop as
        `cancel_and_wait`).

        Args:
            metadata_filter: Required. The flat AND-equality dict that
                identifies the scope of work to drain. Empty/None is rejected
                (use Optio.shutdown() to drain everything).
            block_new_launches: When True, equivalent to wrapping the call
                in `async with self.block_launches(metadata_filter): ...`.
                Convenience for the common teardown pattern where the caller
                wants both "cancel everything in scope" AND "prevent new
                arrivals during the cancel." Default False so the helper
                has no implicit side effect on launch admission; callers
                who only want to drain existing work without affecting
                future launches keep the default.
        """
```

Exported from `optio_core/__init__.py` alongside the other public methods.

The signature deliberately requires a non-empty `metadata_filter` (it must be a `ProcessMetadataFilter`, i.e. a `dict[str, Any]`). Passing `{}` would match everything; if a caller really wants "drain all active tasks," they should call `Optio.shutdown()` instead. The helper raises `ValueError` if `metadata_filter` is `None` or empty.

**Why an optional flag instead of two callsites?** The "drain + block" pattern is the primary use case for teardown handlers, and the flag avoids the small but real race window between `async with self.block_launches(filter):` and the helper's own snapshot if a caller forgets to nest them in the right order. With the flag, the launch guard is registered *before* the snapshot, in one call, with one filter — the order is correct by construction.

## Implementation sketch

```python
import time
import asyncio
from contextlib import AsyncExitStack

async def gracefully_terminate_tasks(
    self,
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
) -> None:
    if not metadata_filter:
        raise ValueError(
            "gracefully_terminate_tasks requires a non-empty metadata_filter; "
            "use Optio.shutdown() to drain everything."
        )

    async with AsyncExitStack() as stack:
        # 1. (Optional) Register the launch guard before the snapshot so that
        #    any new launch arriving between now and step 2 is rejected, not
        #    silently included in or excluded from the wait set.
        if block_new_launches:
            await stack.enter_async_context(self.block_launches(metadata_filter))

        # 2. Snapshot active processes matching the filter.
        procs = await self.list_processes(metadata=metadata_filter)
        active = [
            p for p in procs
            if p["status"]["state"] in ACTIVE_STATES
        ]
        if not active:
            return

        # 3. Issue cancellations. cancel() is idempotent if a process is already
        #    in `cancel_requested` / `cancelling` / terminal — first-wins on the
        #    deadline, so this loop is safe even for processes someone else
        #    already cancelled.
        for proc in active:
            await self.cancel(proc["processId"])

        # 4. Wait for every snapshotted process to reach a terminal state.
        #    Use the same internal ceiling as cancel_and_wait so behavior is
        #    consistent: cooperative tasks finish well inside the grace window;
        #    the supervisor force-cancels stubborn ones; the ceiling exists only
        #    as a backstop against supervisor or DB anomalies.
        ceiling = self._config.cancel_grace_seconds + 25.0
        deadline = time.monotonic() + ceiling
        pending_ids = {p["processId"] for p in active}
        while pending_ids:
            # Refresh state for the still-pending processes.
            still_pending = set()
            for pid in pending_ids:
                proc = await self.get_process(pid)
                if proc is None:
                    continue  # process record was deleted out from under us
                if proc["status"]["state"] in ACTIVE_STATES:
                    still_pending.add(pid)
            pending_ids = still_pending
            if not pending_ids:
                return
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError(
                    f"gracefully_terminate_tasks: {len(pending_ids)} process(es) "
                    f"did not reach a terminal state within {ceiling}s "
                    f"(filter={metadata_filter})"
                )
            await asyncio.sleep(0.1)

        # AsyncExitStack lifts the launch guard (if any) on exit — both on
        # normal return and on exception (e.g. asyncio.TimeoutError above).
```

The `AsyncExitStack` is purely for tidy lifetime management of the conditional launch guard. With `block_new_launches=False`, the stack is empty and the helper behaves as before.

Notes:

- The snapshot is taken **once** at step 2 (after the optional launch guard is registered, before any cancellation). New processes that arrive after the snapshot (via launches not blocked by a launch guard, e.g. when `block_new_launches=False` or when callers compose their own different guard) are deliberately not part of the wait set. This is correct: the helper exists to terminate work that was running *at the moment of invocation*; preventing new work is the launch guard's job. With `block_new_launches=True` the helper itself ensures no such window exists for the helper's own metadata filter.
- With `block_new_launches=True`, the launch guard is entered **before** the snapshot. Any launch matching the filter that was racing to start at the moment the helper was called either (a) reached the registry and is included in the snapshot, or (b) is rejected by the now-active guard. There is no third bucket of "started during teardown" launches.
- Polling cadence (100 ms) and ceiling buffer (+25 s) match `cancel_and_wait` for consistency.
- The function relies on `cancel()` having deadline-driven force-cancel semantics. Without that prerequisite, a stubborn task would keep `pending_ids` non-empty until the ceiling fires — `gracefully_terminate_tasks` should not be merged before the deadline-driven cancel mechanism lands.

## State semantics

Cooperative tasks → terminal state `cancelled`. Force-cancelled (deadline exceeded) → terminal state `failed` with the canonical error string `"Task did not unwind within cancellation grace period"` (set by the deadline-cancel supervisor / `_write_force_cancelled_state`). Either way, the helper returns once they're terminal.

The helper itself never sets state; it only cancels and waits.

## Edge cases

- **No active processes match.** Returns immediately (step 1's `active` list is empty).
- **Process record deleted mid-wait.** `get_process` returns `None`. Treat as terminal (drop from `pending_ids`).
- **Caller cancels the helper coroutine.** `await asyncio.sleep(0.1)` is the cancellation point; the helper unwinds promptly. The cancellations it has already issued continue independently — caller cancellation does not roll them back.
- **Process moves between active states (e.g. `running` → `cancel_requested` → `cancelling`).** All counted as still-pending; helper keeps polling.

## Tests

New test file: `packages/optio-core/tests/test_gracefully_terminate_tasks.py`.

Required cases:

1. **No active processes.** Filter matches nothing; helper returns immediately, no errors.
2. **All cooperative.** Several tasks running, all respect `ctx.cancellation_flag`. Helper returns within `cancel_grace_seconds`. All processes end in `cancelled`.
3. **Mixed cooperative + stubborn.** Some tasks ignore the flag (busy-loop). Helper returns within `cancel_grace_seconds + small force-cancel buffer`. Cooperative tasks end `cancelled`, stubborn tasks end `failed` with the canonical error string.
4. **Out-of-scope tasks untouched.** Two tasks: one matches the filter, one doesn't. Helper cancels only the first; the second is still in its prior state when the helper returns.
5. **Empty/None filter rejected.** `gracefully_terminate_tasks(None)` and `gracefully_terminate_tasks({})` both raise `ValueError`. Both with and without `block_new_launches=True`.
6. **Internal ceiling backstop.** Patch the deadline-cancel supervisor to no-op (so a stubborn task is never force-finalized). The helper raises `asyncio.TimeoutError` once the internal ceiling expires. Verifies the backstop, not normal operation.
7. **New launches during the wait are NOT included (default behaviour).** Start a task matching the filter, call `gracefully_terminate_tasks(filter)` with the default `block_new_launches=False`, then *during the wait* start a second matching task. Verify the helper returns once the *original* task is terminal, even though the second is still running. (Snapshot semantics.)
8. **`block_new_launches=True` rejects new launches during the call.** Start a cooperative task matching the filter; call `gracefully_terminate_tasks(filter, block_new_launches=True)`; from a separate coroutine, attempt `Optio.launch_and_wait(other_pid)` for a task whose metadata also matches the filter — assert `LaunchBlocked` is raised. After the helper returns, the same `Optio.launch_and_wait(...)` call succeeds (guard lifted).
9. **`block_new_launches=True` lifts the guard on exception.** Force the helper to raise `asyncio.TimeoutError` (same setup as case 6) with `block_new_launches=True`. After the raise, verify `Optio._launch_blocks` is empty (the guard was lifted) — i.e. the AsyncExitStack honoured the unwind.
10. **`block_new_launches=False` does NOT register a launch guard.** Verify `Optio._launch_blocks` remains empty during a `block_new_launches=False` call (i.e. the optional path is truly conditional).

## Migration impact

- **Public API.** Pure addition: one new method on `Optio`, exported from `optio_core/__init__.py`. No existing caller breaks.
- **Wire protocol.** No changes.
- **Internal types.** No changes.
- **Performance.** Helper polls every 100 ms with one Mongo `find_one` per still-pending process. Negligible.

## Out of scope

- **Re-cancelling new arrivals.** The helper does not re-snapshot during the wait. Use the launch guard to prevent new arrivals.
- **Cancelling tasks that match the filter only after the snapshot.** Same as above.
- **Customizable ceiling / poll cadence.** Hardcoded for consistency with `cancel_and_wait`.
- **Returning a result list of (process_id, terminal_state) pairs.** Could be added later if a caller needs it; for now the simpler `-> None` shape is sufficient (callers can `list_processes` after the call to inspect state).

## Usage example

A typical teardown handler that wants both "drain existing work in this scope" and "prevent new work from starting during the drain" collapses to one call:

```python
await optio_core.gracefully_terminate_tasks(
    {"some_metadata_key": some_value},
    block_new_launches=True,
)
```

The equivalent two-call form (`async with optio_core.block_launches(filter):` wrapping `await optio_core.gracefully_terminate_tasks(filter)`) remains valid for callers that want different lifetimes for the guard vs. the drain.
