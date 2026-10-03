# Lost Final-State Writes

**Base revision:** `8b0fcc10` on branch `main` (as of 2026-10-03)

## Summary

A process row can stay in an active state (`running`, `cancel_requested`,
`cancelling`) for as long as the engine keeps running, with no task behind it.
It happens when MongoDB is unreachable at the moment the process ends. The
final-state write fails, and the executor forgets the process anyway. Every
later cancel-and-wait on that row then runs into its ceiling and raises
`asyncio.TimeoutError`. The only thing that clears it today is Rule 1 of
`2026-04-22-process-reconciliation-design.md`, at the next engine start.

This spec adds two rules. Rule 3 retries a final-state write that did not
land. Rule 4 lets the cancel and wait paths recognise a row that has no task
behind it, and settle it instead of waiting for it.

## The incident

On 2026-10-02 the host running excavator's bobcat deployment filled its root
disk. A child of an entity-sync process failed with ENOSPC. Optio cancelled
its sibling and then force-cancelled it. The force-cancel write
(`_write_force_cancelled_state`) raised `ServerSelectionTimeoutError`, because
the Mongo container was crash-looping and its name no longer resolved. The
supervisor loop logged the exception and carried on. The process's
cancellation entry had already been removed by the task's own `finally`, so
nothing ever tried again.

The parent ended up in `cancel_requested` and the sibling in `cancelling`,
eight hours after Mongo came back. Deleting the source calls
`group_cancel_and_wait` on the entity's processes. That call timed out after
55 s on every attempt, so the source could not be deleted until the rows were
fixed by hand.

## Why it happens

1. `Executor._execute_process` removes the process from `_cancellation_flags`
   and `_running_tasks` in a `finally`. That runs whether or not the final
   write reached Mongo. `force_cancel` has the same shape: if its write raises,
   the exception escapes into the supervisor loop, and the entry is already
   gone.
2. `cancel()` moves a `running` row to `cancel_requested`. It then calls
   `request_cancel_with_deadline`, which returns `False` when no task exists.
   The row stays in `cancel_requested`, and nothing deals with the `False`.
   `cancel_and_wait` and `group_cancel_and_wait` then poll that row until
   their ceiling.

## Rule 3 -- Retry a final-state write that did not land

The executor keeps a small in-memory queue of final states that could not be
written (`_unrecorded_finals`, keyed by process OID).

- **Executor exit paths.** Each exit arm of `_execute_process` (done, failed,
  cooperative cancel, no execute function) notes the final state it intends
  to write. It does this as soon as the outcome is known, before any database
  call. Suppose the `finally` is reached without that write having completed.
  Then the intended state goes on the queue. If no outcome was ever
  determined, `failed` goes on the queue instead, with error `Final state
  could not be recorded: <exception>`. That happens, for example, when the
  initial `running` write itself failed.
- **Forced cancel.** The forced `CancelledError` path does not queue anything;
  `force_cancel` owns that write, as it does today. If
  `_write_force_cancelled_state` raises a `PyMongoError`, `force_cancel` queues
  the same forced `failed` state instead of letting the error escape. The same
  applies if the child cascade's `list_direct_children` raises: `force_cancel`
  queues a pending cascade for that OID.
- **Retrying.** The supervisor loop already ticks every 0.5 s. Each tick it
  also calls `Executor.retry_unrecorded_finals()`, which works through the
  queue entries that are due:
  - Conditional update, only if the row is still in `ACTIVE_STATES`. This is
    the same guard `_write_force_cancelled_state` uses. It writes the status
    and `widgetUpstream: None`, plus `autoResumeScheduled: False` when the
    status is `failed`, and `expireAt` when the row has `ttlSeconds`. This is
    the new store helper `finalize_if_active`.
  - Log entry: `State recorded late: <state> (database was unavailable)`.
  - Ephemeral cleanup, as the normal exit path does.
  - A pending cascade is re-run, using the existing `force_cancel` logic.
  - The entry is removed on success, and also when the row is gone or no
    longer active. On a `PyMongoError` the next attempt backs off, doubling
    from 1 s up to 30 s. There is no attempt cap. A warning is logged on the
    first failure, and again when the write finally lands.
- **No behaviour change on the healthy path.** When writes succeed, nothing is
  queued and no extra database calls are made. Exceptions propagate exactly
  as they do today; queuing happens in addition to that, not instead of it.

The queue lives in memory. If the engine stops before the write lands, Rule 1
covers the row at the next start, as it does today.

## Rule 4 -- Settle a row that has no task behind it

**Invariant.** `_execute_process` registers the cancellation entry before it
writes `running`. It writes the final state before it removes the entry.
`force_cancel` keeps its OID in a new `_force_cancelling` set until its own
write is done. So suppose a row is in `running`, `cancel_requested` or
`cancelling`, and the executor holds no entry, no task, and no force-cancel in
progress for its OID. That row's final write was lost.

`scheduled` is excluded. `launch_process` writes it before registering the
task, and `cancel()` already settles `scheduled` atomically to `cancelled`.

New `Optio._settle_if_orphaned(proc) -> bool`:

- If the OID is on Rule 3's queue, it writes that state now, since it is the
  more accurate one.
- Otherwise it updates the row to `failed`, conditionally on the state still
  being the one observed. The error is `Process had no running task in this
  engine; its final state was never recorded`. The log entry is
  `State reconciled: <prev> -> failed (no running task)`. It also clears
  `widgetUpstream` and `autoResumeScheduled`, and sets `expireAt`, through
  the same `finalize_if_active` helper.

It is called from three places:

- `cancel()`, when `request_cancel_with_deadline` reports no entry, for the
  row it has just moved to `cancel_requested`.
- The poll loop of `cancel_and_wait`, for the row being waited on.
- The poll loop of `group_cancel_and_wait`, for each pending row that is
  still active.

Settled rows count as terminal for the waiter. `group_cancel_and_wait` then
proceeds to `purge_records` as usual.

**Assumption: one engine per (database, prefix).** Rule 1 already makes the
same assumption. If two engines shared a prefix, a waiter in one would see
the other's live rows as orphans.

## Tests

No test depends on wall-clock time. Waits are on conditions, with a 60 s
hang ceiling.

- **Rule 3.** Patch the store write so the final write raises
  `AutoReconnect` a fixed number of times. Cover done, failed, cooperative
  cancel, forced cancel, and a failed initial `running` write. In each case
  the row reaches the intended state, with the late-recorded log entry.
  Also, while the failures last, the retry does not overwrite a row that has
  since been moved out of the active states.
- **Rule 4.** Insert rows directly in `running`, `cancel_requested` and
  `cancelling`, with no task. `cancel_and_wait` and `group_cancel_and_wait`
  return a terminal state and do not raise `TimeoutError`; grace is set small,
  so the old code fails by raising. The rows are `failed` with the
  no-running-task error.
- **Negative.** A live task under cancel is never settled as an orphan; it
  still ends `cancelled`. A `scheduled` row is never settled.
- **Healthy path.** The existing suites stay green.

## What this is not

- **Not a periodic orphan sweep.** Rows nobody cancels or waits on are left
  for Rule 3, if this engine queued them, or for Rule 1 at the next start.
- **Not a change to excavator.** Excavator's delete path showing a generic 500
  after 55 s is a separate issue in that repo.
- **Not a state-machine change.** Like Rules 1 and 2, these are
  administrative writes through the store, not business transitions.

## Porting note

Branch `postgres-process-plane` (not yet merged) rewrites `executor.py`,
`_force_cancel.py` and `lifecycle.py` behind a `ProcessStore` interface. The
writes here go only through the existing store helpers (`update_status`,
`append_log`, `clear_widget_upstream`, the conditional update used by
`_write_force_cancelled_state`). So they map one-to-one onto that interface,
and the conflict should be mechanical.
