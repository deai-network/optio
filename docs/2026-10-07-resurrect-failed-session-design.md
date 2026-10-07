# Resurrect a failed session

Status: design, awaiting the owner's review. Branch `csillag/resurrect` (from `main`
d775a7d5, which has the optio-api access scope/authorize hooks). Excavator facts checked
against excavator `main` dcf02767.

## Problem

A claudecode process that fails while its work is not yet saved loses that work, even
though the work is still on the host. It happened twice on excavator's bobcat
deployment (2026-10-03 and 2026-10-06): the snapshot capture after a stop ran past the
30 s cancel grace, `_force_cancel` wrote `failed` ("Task did not unwind within
cancellation grace period"), and the abandoned task never reached
`cleanup_taskdir`. What was left:

- the workdir on the host, intact (784 MB, about six hours of work newer than the last
  snapshot);
- the session blob in GridFS (the capture stores it first, then deletes `home/.claude`
  from the workdir), referenced by no snapshot record;
- a partial workdir blob (GridFS chunks without an `fs.files` document).

Both times the process was recovered by hand following
`docs/2026-09-13-manual-snapshot-restore.md`. Today's UI makes it worse: Resume is
offered (an older snapshot exists), and Resume wipes the workdir and continues from the
older snapshot, silently discarding the unsaved work.

## Goal

A **Resurrect** action on the launch button that saves the work left on the host and
then resumes from it, in one click.

Owner decisions (2026-10-07):

1. Resurrect saves, then resumes immediately.
2. When Resurrect is possible it is the primary action; the menu keeps "Resume from last
   snapshot" and "Restart", each behind a confirmation that the unsaved work is
   discarded.
3. Only optio-claudecode implements it now; the generic parts are agent-agnostic so the
   other agents can opt in later.
4. It covers every failure that leaves the workdir behind: interrupted saves (cancel
   grace or shutdown grace exceeded), engine restart or crash while running ("Process
   was interrupted by server restart"), OOM, a save that raised.
5. It is a **separate command** (own API route and engine RPC), not a launch option.
6. While it runs, the process keeps its state; progress shows the work; launching is
   locked.
7. Processes running today (old code) must be resurrectable after the upgrade; setting
   their flag by hand in the database is acceptable.

Out of scope: the slow capture itself (separate investigation); the other six agents;
making workdirs survive container recreation (for the deploy that ships this, failed
workdirs are backed up and restored by hand).

## Design

### Two process fields

- `supportsResurrect` (capability): true when the task definition provides a resurrect
  hook. Written by task sync like `supportsResume`.
- `hasUnsavedWork` (per instance): true while the host workdir holds work newer than the
  last snapshot. Set and cleared by the agent through two new `ProcessContext` methods,
  `mark_unsaved_work()` / `clear_unsaved_work()` (no-ops with a warning when the task has
  no resurrect hook, like `mark_has_saved_state`). Additionally cleared by optio-core when
  a launch starts (a launch rebuilds the workdir, so whatever was there is gone) and when
  a resurrect finds nothing to save. Nothing else touches it: force-cancel, shutdown
  reconciliation and the server-restart reset leave it as it is. That is the point.

Absent fields read as false (no migration).

The UI offers Resurrect when `supportsResurrect && hasUnsavedWork` and the state is
launchable (`LAUNCHABLE_STATES`).

### Task hook

`TaskInstance.resurrect: Callable[[ProcessContext], Awaitable[None]] | None = None`.

Contract for the hook:

- it runs outside `execute`, with a `ProcessContext` for the same process (blobs,
  progress, log, `mark_has_saved_state`), `resume=False`;
- on success it has stored a complete snapshot, called `mark_has_saved_state()`, and
  removed the host leftovers (the task directory);
- it raises `optio_core.NothingToResurrect(reason)` when nothing is left to save (no
  workdir, empty workdir, no session state);
- any other exception means "failed, may be retried".

### Command flow (optio-core)

`Optio.resurrect(process_id, *, session_id)`, exposed as engine RPC `resurrect` and
called by the API. Fire-and-forget like `launch`:

1. Synchronous checks, each with a typed reason: `shutting-down`, `not-found`,
   `no-resurrect-support` (no hook registered), `not-resurrectable` (state not
   launchable, or `hasUnsavedWork` false), `resurrect-in-progress`, `launch-blocked`
   (it ends in a launch, so launch blocks apply).
2. Mark the process as resurrecting in an in-memory set; while it is there, `launch`
   returns `not-launchable` and a second `resurrect` returns `resurrect-in-progress`.
3. Return `ok` with the process; the rest runs in a background task:
   - log "Resurrect requested", progress "Resurrecting: saving the unsaved work…";
   - run the hook;
   - success: clear `hasUnsavedWork`, log "Resurrected: unsaved work saved", then
     `launch(process_id, resume=True, session_id)` (normal path: scheduled -> running);
   - `NothingToResurrect`: clear `hasUnsavedWork`, log the reason, clear progress; the
     button falls back to Resume/Restart;
   - other exception: log the error, clear progress, keep `hasUnsavedWork` (retry
     possible).
   - always: remove the process from the resurrecting set.
4. Shutdown cancels a running resurrect like a running task; the flag stays set.

The state stays the failed state throughout; no state-machine change.

### claudecode

**Setting the flag.** `run_claudecode_session` calls `ctx.mark_unsaved_work()` at the
point where claude is live (where `launched_handle` is assigned; before that the workdir
holds nothing that the last snapshot lacks). `_capture_snapshot` calls
`ctx.clear_unsaved_work()` after `mark_has_saved_state()`.

**A failed save keeps the workdir.** Today a capture that raises is followed by
`cleanup_taskdir` ("proceeding with workdir wipe"). New: if the capture raised, the task
directory is kept (log line says so) and the flag stays set, so Resurrect can retry.

**Cleaning up an interrupted save.** The save order stays as it is. One addition: before
streaming the workdir blob, `_capture_snapshot` records
`{processId, sessionBlobId, workdirBlobId, startedAt}` in a small collection
`{prefix}_claudecode_pending_captures` (upsert by processId); the workdir blob is opened
with that pre-generated id (`ProcessContext.store_blob` gains an optional `file_id`,
backed by motor's `open_upload_stream_with_id`). After the snapshot record is
inserted, the pending record is deleted. A force-killed capture leaves the record
behind, which tells the resurrect hook exactly which partial blob to delete.

**The resurrect hook** (`create_claudecode_task` passes it; built from the same config,
host via `_build_host`):

1. Connect. If the workdir is missing or empty, raise `NothingToResurrect("workdir no
   longer on the host")`.
2. Stop leftovers of the old run: the tmux/ttyd/claude tree on the task's socket
   (`teardown_session_tree(aggressive=True)`, as crash-orphan rescue does) and stray
   `tail -F <workdir>/optio.log` readers.
3. If a pending-capture record exists: delete its partial workdir blob (chunks too; the
   `fs.files` document may not exist), delete the record.
4. Save:
   - `home/.claude` present (crash while running, or the save failed before the session
     step): normal `_capture_snapshot(end_state="resurrected")`.
   - `home/.claude` absent (the save was cut off after the session step, which is the
     case we hit; also every process that fails on today's code): use the newest
     session blob of this process (`fs.files` metadata `processId`, `name: "session"`)
     uploaded after the latest snapshot and referenced by no snapshot; archive the
     workdir and insert the snapshot with it. If there is no such blob, raise
     `NothingToResurrect("session state not found")`.
   For this, `_capture_snapshot` is split: the session half and the
   workdir-plus-record half become separate functions; the normal capture calls both,
   the fallback calls only the second with the existing session blob id. No behaviour
   change for the normal capture.
5. `cleanup_taskdir`, disconnect.

The existing crash-orphan rescue at the start of a resume stays as it is.

### Wire and API

- optio-contracts: `ProcessSchema` gets `supportsResurrect?` and `hasUnsavedWork?`.
  New frontend route `POST /processes/:id/resurrect` (query `InstanceQuerySchema`, body
  `{ sessionId? }`, responses 200 `ProcessSchema`, 404/409 error body with the reasons
  above). New engine RPC `resurrect({ processId, sessionId? })` with the same result
  shape as `launch`. Regenerate `optio_core/_generated`.
- optio-core `_engine_service`: `resurrect` handler; both fields in `_PROCESS_WIRE_KEYS`.
- optio-api: `resurrectProcess` handler and the route in all four adapters (express,
  fastify, nextjs-app, nextjs-pages). Access works like launch under the hooks from
  d775a7d5 (`docs/2026-10-07-optio-api-access-scope-design.md`): the process is looked up
  within `scope` (out of scope = not found), then `authorize` is asked with a new
  `OptioAction` value `'resurrect'` and the process; false gives 403 and the engine is
  not called. Without hooks nothing changes. `stream-poller` passes both fields like
  `hasSavedState`.

### UI (optio-ui)

- `useProcessActions().resurrect(processId)`.
- `LaunchControls` gets `onResurrect?`. New first case: `supportsResurrect &&
  hasUnsavedWork && onResurrect` and launchable -> split button, primary **Resurrect**
  (tooltip "Save the work left by the failed run, then resume"); menu "Resume from last
  snapshot" (only when `supportsResume && hasSavedState`) and "Restart", each opening a
  confirmation ("This discards the unsaved work left by the failed run.").
- `ProcessItem`, `ProcessList`, `ProcessTreeView` pass `onResurrect` through.
- optio-dashboard wires it. Strings use `t(key, { defaultValue })` like the existing
  ones.

### Consumers

- excavator (checked on `main` dcf02767):
  - bump the optio packages (the API also needs the optio-api release that carries the
    access hooks; excavator `main` already passes them since 6efee8af);
  - `packages/api/src/auth/optio-access.ts` `optioAuthorize`: add `'resurrect'` to the
    `launch`/`cancel`/`dismiss` case so it honours `metadata.requiredRoles` (it would
    otherwise fall to `default: true`);
  - wire `onResurrect` next to the five `onLaunch` sites (`ProcessesPage`,
    `SourceProcesses`, `SourceConfig`, `TargetConfig`, `EntityOverview`);
    `ProcessDetailPage` passes only `onCancel` to its `ProcessTreeView`, so it gets
    no launch controls and needs nothing;
  - `free_style/task.py` `_finalize_ti` mutates the TaskInstance in place (params,
    `auto_resume`, wrapped `execute`), so the `resurrect` hook set by
    `create_claudecode_task` survives; the hook bypasses the state-tracking wrapper, and
    the resume it triggers goes through it.
- The unmerged Postgres process plane (`postgres-process-plane`, aoe:8d60e48e899b)
  needs the two new fields when it rebases.

### Processes running today

After the upgraded engine is deployed: task sync sets `supportsResurrect`. For a process
that failed on the old code, set `hasUnsavedWork: true` on its `gm_processes` record by
hand; Resurrect then takes the session-blob fallback. Its partial workdir blob has no
pending record and is deleted by hand (as on 2026-10-06). The deploy itself recreates
bobcat's engine container, so failed workdirs are backed up before and copied back
after it, by hand.

## Error handling summary

| situation | result |
|---|---|
| workdir gone / empty | `NothingToResurrect`, flag cleared, Resume/Restart remain |
| no session state anywhere | `NothingToResurrect`, flag cleared |
| save raises (mongo, disk, credentials guard) | error logged, flag kept, retry possible |
| engine shuts down during resurrect | task cancelled, flag kept |
| launch after a successful save is blocked | logged; process stays resumable (Resume) |
| second click while running | `resurrect-in-progress` |

## Testing

TDD in each package; optio tests on superego against a private mongo/redis
(`MONGO_URL=mongodb://127.0.0.1:27217`, `REDIS_URL=redis://127.0.0.1:6579`).

- optio-core: RPC precondition reasons; lock against launch and double resurrect;
  success clears the flag and launches resume; `NothingToResurrect` clears the flag;
  other errors keep it; launch clears the flag; `supportsResurrect` from task sync;
  wire keys.
- optio-claudecode (fake host + real GridFS, existing test patterns): flag set when
  live and cleared after capture; failed capture keeps the taskdir; pending-capture
  record lifecycle; hook with `home/.claude` present; hook with only an orphan session
  blob (the 2026-10-06 shape: session blob + partial workdir chunks + pending record);
  workdir gone; no session blob; snapshot restored by a following resume.
- optio-contracts / optio-api: route, RPC schema, reason-to-status mapping, adapters;
  scope (out-of-scope process = 404, engine not called) and `authorize` with action
  `'resurrect'` (false = 403, engine not called), alongside the existing access tests.
- optio-ui: `LaunchControls` cases (Resurrect primary, confirmations, no Resurrect when
  the flag or capability is missing).
- End to end on superego: a claudecode process force-failed during capture (slow
  archive injected), resurrected from the dashboard, resumed with the work intact.

## Releases

optio-contracts + optio-core (wire, lockstep), optio-api, optio-ui, optio-claudecode,
optio-dashboard, per `docs/release-cookbook.md` (npm and PyPI). Then excavator.
