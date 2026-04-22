# Process State Reconciliation

**Base revision:** `f6ef0f2` on branch `main` (as of 2026-04-22)

## Summary

optio-core tracks process lifecycle in two places: MongoDB (durable) and
`Executor._cancellation_flags` (in-memory). When a Python server stops without
the tasks unwinding cleanly — SIGKILL, crash, or a cooperating task that
does not respond to cancellation within the shutdown grace period — the
MongoDB record is left in an active state (`scheduled`, `running`,
`cancel_requested`, `cancelling`) with no Python process that can own it.
On the next server start, the dashboard shows the process as running but any
cancel request is a dead letter: `Optio._handle_cancel` calls
`Executor.request_cancel`, which returns `False` because the fresh process's
flag dict is empty.

This spec defines two complementary rules that close the gap.

## Rule 1 — Startup reconciliation

On `Optio.init()`, before `_sync_definitions()` runs, scan the process
collection for any row whose `status.state` is in `ACTIVE_STATES`
(`scheduled`, `running`, `cancel_requested`, `cancelling`). For each match:

- Update `status` to `state="failed"` with
  `error="Process was interrupted by server restart"` and `failedAt=now()`.
- Append a log entry: `"State reconciled: <prev> -> failed (server restart)"`.

By construction no live flag exists in the freshly started process, so any
active-state row is definitively stale. The reconciliation writes via
`update_status`, which does not enforce the state-machine transition table —
this is an administrative reset, not a business transition.

## Rule 2 — Shutdown completeness

In `Optio.shutdown()`, after the cooperative-cancellation grace period,
enumerate any `_cancellation_flags` still present. These belong to tasks that
did not unwind in time. For each, conditionally update the Mongo record (only
if its state is still in `ACTIVE_STATES`) to `state="failed"` with
`error="Task did not exit within shutdown grace period"` and `failedAt=now()`.
The conditional update avoids overwriting a terminal state a task flushed at
the last moment.

The grace period is exposed as an optional `grace_seconds: float = 5.0`
parameter on `shutdown()` so tests can shorten it.

## What this is not

- **Not a state-machine change.** `state_machine.py`'s transition table is
  untouched. Reconciliation writes directly through `update_status` and a
  conditional Mongo update, both administrative operations.
- **Not a fix for orphaned OS subprocesses spawned by tasks.** Tasks that
  spawn external processes are responsible for their own process-group
  management. A separate design (not in this spec) may add core-level support
  for that case.
- **Not a liveness/heartbeat mechanism.** The instance-liveness plan at
  `docs/superpowers/plans/2026-04-14-liveness-heartbeat.md` addresses "is this
  instance up" for discovery/UI purposes. Reconciliation works independently
  of it: the guarantee here is that stale state is cleaned up at the next
  boot regardless of how the previous server stopped.
