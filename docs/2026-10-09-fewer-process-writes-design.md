# Fewer writes per process -- Design

**Base revision:** `660ce909` on `main` (2026-10-09). optio-core unchanged since
`e944c934` (wire 0.5.2).

## Summary

optio-core records each step of a process's life -- the state change, its
"State changed to X" log line, the session id, every progress message and its
log line, the widget-upstream clear -- as separate MongoDB updates on the same
process document. This design writes each step as one update. What is recorded
stays the same: the same fields, the same log entries with their own
timestamps, in the same order, each written when it happens. Nothing is
buffered.

## Why

Measured on the excavator dev stack (2026-10-08/09), one minute of
`gm_processes` changes at about 15 idle sync checks a minute: 906 writes
(816 updates, 45 inserts, 45 deletes), about 60 per check. An idle sync logs
only five short lines; the rest is the per-step plumbing below. Since the
excavator's main MongoDB became a replica set, every write is also an oplog
entry, and the main MongoDB's CPU rose from about 7 to about 18 CPU-s a minute.
(The default write concern `{w: 1}` cut the journal syncs by about 80% without
changing that CPU, so the write count itself is the lever.)

## Today, per process

| Step | Writes | Code |
|---|---|---|
| relaunch: `clear_result_fields` (reset the previous run's fields), status `scheduled`, log "State changed to scheduled" | 3 (+ the previous run's children deleted) | `Executor.launch_process` |
| start: status `running`, log "State changed to running", `$set originatingSessionId` | 3 | `Executor._execute_process` |
| each message-bearing `report_progress`: `update_progress`, then `append_log` | 2 | `ProcessContext._write_progress` |
| percent-only progress | 1 | `ProcessContext._write_progress` |
| end (done / cancelled / failed): status (+ `expireAt`), the event or error log line, `clear_widget_upstream` | 3 | `Executor._execute_process` |
| child: insert (state `scheduled`), parent's "Spawned child: X" line | 2 (two documents) | `Executor.execute_child` |
| dismiss: `clear_result_fields`, status `idle` | 2 | `Optio.dismiss` |

A top-level process with N messages: 9 + 2N writes. A child with N messages:
8 + 2N (insert, parent line, start 3, messages, end 3).

## Change

Each step becomes one update on the process document:

| Step | One update |
|---|---|
| relaunch | `$set` the reset fields of `clear_result_fields`, `status` = scheduled, and `log` = `[<scheduled entry>]` (the reset empties the log, so the scheduled entry is the whole new log) |
| start | `$set status` (running, `runningSince`), `$set originatingSessionId`, `$push log` "State changed to running" |
| message | `$set progress`, `$push log` |
| percent-only | `$set progress` (unchanged) |
| end | `$set status` (+ `expireAt` when the row has `ttlSeconds`), `$set widgetUpstream: null`, `$push log` (the event line, or the error line for `failed`) |
| dismiss | `$set` the reset fields and `status` = idle |

The previous run's children are still deleted first (`delete_descendants`),
and a child's insert and its parent's "Spawned child" line stay two writes (two
documents).

Result: a top-level process 3 + N writes, a child 4 + N. For an excavator idle
check (heartbeat, sync, "Pull from source", two pages; children kept) about 30
writes instead of about 60.

### store.py

- `update_status(db, prefix, oid, status, expire_at=None, *, log=None, set_fields=None)`:
  `log` is a `(level, message)` pair appended in the same update (`$push`, entry
  built like `append_log`'s, timestamp at call time); `set_fields` are extra
  top-level fields `$set` in the same update. Existing callers are unchanged.
- `update_progress(db, prefix, oid, progress, *, log=None)`: same `log` option.
- `relaunch_reset(db, prefix, oid, status, log)` (new): the reset fields of
  `clear_result_fields` plus `status` and `log` = `[entry]`, one update.
  `clear_result_fields` stays for any other caller and keeps deleting
  descendants; `relaunch_reset` does not delete (the caller runs
  `delete_descendants` first).
- `append_log` and `clear_widget_upstream` stay (other callers: lifecycle,
  context, the resurrect paths).

### executor.py

- `launch_process`: `delete_descendants`, then `relaunch_reset(... scheduled,
  ("event", "State changed to scheduled"))`. The `hasUnsavedWork` clear stays
  separate (it happens only when set).
- `_execute_process` start: one `update_status(running, log=("event", "State
  changed to running"), set_fields={"originatingSessionId": ...})`.
- `_execute_process` ends: `update_status(final, expire_at, log=..., set_fields=
  {"widgetUpstream": None})` in each of the three terminal arms (done /
  cancelled after return; cancelled by CancelledError; failed). The separate
  `clear_widget_upstream` calls go. `final_recorded` is set after that one
  update, as today after the status write: the log line can no longer be lost
  separately from its status.
- The no-execute-fn branch: same fold (it writes a failed status today without
  a log line or widget clear; it gets the widget clear in the same update, no
  new log line).

### context.py

- `_write_progress(progress, level)`: one `update_progress(..., log=(level,
  message) if progress.message else None)`. The flush order, the throttle,
  avalanche coalescing and the "(N messages dropped)" line are unchanged.

### lifecycle.py

- `dismiss`: `delete_descendants`, then one update: the reset fields and status
  `idle` (`relaunch_reset` with `log=None` leaves the log empty, as today).

## Unchanged

- The document schema and every field's meaning; optio-api and optio-ui read
  the same data. A reader polling between two updates no longer sees a status
  without its log line, or a log line without its progress.
- Log entries: same levels, messages and timestamps, in the same order.
- Rule 3 of `docs/2026-10-03-lost-final-state-writes-design.md` (the parked
  final state and its retry), percent-only coalescing, the flush interval,
  avalanche handling, the cancel and force-cancel paths' semantics.

## Not in scope

- The rare paths keep their separate writes: the reconcile of interrupted
  processes at `init()`, settling an orphaned row, the late final write after a
  Mongo outage, resurrect, `set_widget_*`, the resume flags.
- optio-api's stream pollers (list, tree, session events). Moving them to
  change streams is a separate design.
- Any change to what applications log (excavator's own message tidy is done in
  excavator).

## Testing

In `packages/optio-core/tests/` (the real-Mongo test setup):

- A counting wrapper on the process collection (around `update_one`,
  `insert_one`, `delete_*`) for one process run:
  - a top-level run with no messages: 3 updates (relaunch, start, end) -- today 9;
  - with two messages: 5 updates;
  - a child with one message: insert + 3 updates on the child + 1 on the
    parent.
- The resulting documents: the log entries and their order (`scheduled`,
  `running`, the messages, `done`), `status` fields, `originatingSessionId`,
  `widgetUpstream` null, `expireAt` set when `ttlSeconds` is.
- Each terminal arm (done, failed with its error line, cancelled cooperatively,
  cancelled by CancelledError) writes status, log line and widget clear
  together.
- Dismiss: one update after the descendants' delete; the log empty, status
  idle.
- The existing suite (cancel propagation, lost final states, resume, widget,
  progress throttle and avalanche tests) passes unchanged.
- Tests follow optio's rule: no wall-clock dependence.

## Measurement

On the excavator dev stack, the one-minute change-stream count of
`gm_processes` before and after (today 906 writes a minute at about 15 idle
checks), and the main MongoDB's CPU per minute.

## Rollout

An optio-core patch release (wire release with optio-contracts, per the
release cookbook), then excavator's optio dependency bump. Nothing to migrate.
