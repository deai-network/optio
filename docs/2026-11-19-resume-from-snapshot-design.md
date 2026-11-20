# Resume from a previous snapshot — design

Date: 2026-11-19. Status: agreed with the owner in discussion on 2026-11-19 ("design is fine now,
we can proceed"). Builds on the resume design (2026-04-24-optio-resume-design.md), the claudecode
resume (2026-05-29-optio-claudecode-resume-design.md), the resurrect flow
(2026-10-07-resurrect-failed-session-design.md) and the manual procedures
(2026-09-13-manual-snapshot-restore.md; excavator's 2026-11-19 manual rollback of a sales3 session).

## Purpose

A resumable process keeps up to 5 snapshots (`SNAPSHOT_RETENTION`), but Resume always continues from
the newest. Going back to an earlier state has been a manual database operation. The owner wants it
in the UI: next to Resume and Restart, "Resume from previous snapshot (<time>)", one per older valid
snapshot.

## Decisions (owner, 2026-11-19)

1. **Delete the newer snapshots.** Resuming from an older snapshot deletes every snapshot newer than
   it (documents and their blobs). The chosen snapshot becomes the newest, so the ordinary resume
   restores it. No override is threaded through the readers of "the latest snapshot", and nothing is
   copied. Going "forward" again afterwards is not possible (accepted, as with Restart).
2. **Behind a confirmation** that names how many newer snapshots it discards, e.g. "Resume from
   19 Nov 13:05? This discards 2 newer snapshots." It adds that what the discarded runs delivered
   stays in the app.
3. **All agent wrappers**, through one shared helper in optio-agents (proposed in the design
   discussion, as part of the design the owner approved).
4. **One launch-controls implementation.** optio-ui's launch controls are rebuilt on vultus (optio-ui
   already peer-depends on vultus-core and vultus-antd): the launch-actions hook that excavator keeps
   today (`useProcessLaunchActions`, vultus `useAction` descriptors drawn by `CombinedActionButton`)
   moves into optio-ui as the single implementation of the launch matrix, the new snapshot items
   included. `LaunchControls` renders with it; excavator imports it and deletes its copy.

## The menu

For a process that is launchable and resumable (`isLaunchable`, `isResumable`):

- **Resume** (primary): the newest snapshot, as today.
- **Resume from previous snapshot (<time>)**: one item per valid snapshot older than the newest, at
  most 4 (retention 5 minus the newest), newest first. `<time>` is the snapshot's `capturedAt` in the
  viewer's locale (date and time to the minute; seconds when two items would read the same). Each is
  behind the confirmation of decision 2.
- **Restart (discard saved state)**: as today.

When the process is resurrectable (a failed run left unsaved work, `isResurrectable`), Resurrect stays
primary and the menu holds "Resume from last snapshot", the previous-snapshot items and Restart; the
previous-snapshot items' confirmation also names the unsaved work they discard, as the existing items'
do. While a resurrect runs, nothing changes (Resurrecting only).

The items appear only when the list (below) has older valid snapshots. While the list loads, or when it
cannot be read, the menu shows Resume and Restart as today (no error in the menu).

Everything the launch gate holds back (e.g. no free seed, a role requirement) holds the new items
back too, with the same reason.

## The launch-actions hook (optio-ui)

`useProcessLaunchActions(process, triggerLaunch, triggerResurrect?, launchGate?, snapshots?)` moves
from excavator to optio-ui, exported. It returns vultus `ActionStatus[]` for `CombinedActionButton`:
Resurrect, Resume, four previous-snapshot slots (invisible when unused, so the number of `useAction`
calls is fixed), Restart, Launch. `triggerLaunch` receives `{ resume?: boolean, resumeFrom?: string }`.
`snapshots` is the list below; absent means no previous-snapshot items. The resurrect cases keep their
current behaviour. optio-ui's `LaunchControls` renders this hook (its `size` / icon options mapped to
what `CombinedActionButton` offers); its callers' props stay compatible. Excavator's process page
imports the hook from optio-ui and passes its gates as today.

A small hook, `useProcessSnapshots(processId, { enabled })`, reads the list through the API client
(react-query). Callers enable it for a launchable, resumable process (excavator's process page: on
the page; optio-ui's lists: for the rows that are resumable).

## The chain

### Listing

- **optio-api**: `GET /processes/:id/snapshots` → `{ snapshots: [{ capturedAt, endState }] }`, newest
  first, valid snapshots only, the newest included (the UI skips it). Authorization: whoever may see
  the process (the access scope that gates the process itself).
- **Engine RPC** (optio-engine-to-api contract): `listSnapshots({ processId })` → the same shape;
  errors as typed reasons (`not-found`, `no-snapshot-support`).
- **optio-core**: looks up the process's task and calls its `list_snapshots` hook (a new optional
  `TaskInstance` field, beside `read_conversation`) with a read-only context. A task without the hook
  answers `no-snapshot-support` (the UI then shows no items).

### Resuming from a snapshot

- **Contracts**: the frontend → API launch body gains `resumeFrom?: string` (an ISO 8601 timestamp,
  the snapshot's `capturedAt` as the list gave it) beside `resume`; the API → engine launch RPC gains
  the same field. `resumeFrom` without `resume: true` is refused (`invalid-request`).
- **optio-core** (`launch`): with `resumeFrom`, after the existing resume checks (supportsResume,
  hasSavedState, launchable, not resurrecting), it calls the task's `discard_snapshots_after` hook
  (new optional `TaskInstance` field) with the timestamp, then launches with `resume=True` as today.
  Refusals, each a typed launch reason the UI shows like the existing ones:
  - `no-snapshot-support`: the task has no hook;
  - `snapshot-not-found`: no valid snapshot with exactly that `capturedAt` (millisecond precision);
  - `capture-pending`: the wrapper holds an unfinished capture for the process (a Resurrect would make
    a newer snapshot; resolve it first);
  - the existing ones (`not-launchable`, `no-resume-support`, ...).
  Nothing is deleted when a check fails. If the launch fails after the deletion, the chosen snapshot
  is the newest, so a later Resume restores it.
- **Auto-resume, Resurrect, the conversation archive**: unchanged; they read the newest snapshot,
  which is the chosen one after the deletion.

### The wrappers (optio-agents helper)

One module in optio-agents, used by every wrapper (claudecode, opencode, codex, cursor, grok,
kimicode, antigravity), parameterized by the wrapper's snapshot collection, its required blob fields
and its optional ones:

- `list_snapshots(db, collection, process_id, required_blob_fields)` → `[{capturedAt, endState}]`,
  newest first, keeping only documents whose required blobs exist in GridFS ("valid").
- `discard_snapshots_after(db, collection, process_id, captured_at, blob_fields)` → refuses when no
  valid document has exactly `captured_at`; otherwise deletes the newer documents and their blobs (all
  blob fields they name), and returns how many it deleted.

Each wrapper wires both into its task's new hooks, with its own collection and fields, and its own
pending-capture check (claudecode: `{prefix}_claudecode_pending_captures`; wrappers without one have
none). The wrappers' capture, prune and restore code is unchanged.

## Known behaviour (documented, not changed)

- The snapshot carries its own model pick (claudecode: `home/.claude/optio-picks.json` inside the
  session blob). One without a pick resumes on the configuration's current default model.
- What the discarded runs delivered (files the app received through on_deliverable) stays; the resumed
  agent may deliver again.
- The conversation view after the resume continues from the chosen snapshot's record. The newer turns
  are gone with the discarded snapshots (every launch resets the process log, so it does not keep them
  either).
- Restoring replaces the host workdir with the snapshot's.
- A crash that left a claudecode session behind on the host (its tmux/claude tree outliving the
  engine, or the marker of a rescue cut off before its capture recorded anything): a plain Resume
  first saves that state into a new snapshot (crash-orphan rescue) and resumes from it. A resume
  from a previous snapshot stops it without saving, logs "An orphaned session was stopped without
  capture: resuming from the snapshot of <capturedAt>" (warning), and restores the chosen snapshot.
  The orphan itself is visible on the host only; a crash after claude came up leaves
  `hasUnsavedWork`, so the process shows as resurrectable and the item's confirmation names the
  unsaved work it discards (Resurrect saves it instead). A cut-off capture's pending record refuses
  the resume from a snapshot (`capture-pending`).
- A newest document that has lost a blob: the list starts at the newest valid snapshot, which the UI
  takes for the one Resume reads. It is not offered as an item, and picking the next item deletes it
  too, under a count one lower than what is deleted.
- Two documents with the same `capturedAt` millisecond are both kept by a discard naming it (no
  tie-break).
- A crash or shutdown between deleting the newer documents and deleting their blobs, or a failed blob
  delete (logged as a warning), leaves those blobs orphaned. No data is lost and the chosen snapshot is
  the newest; nothing sweeps them.
- A discard hook error other than `capture-pending` and `snapshot-not-found` is no typed refusal: the
  launch route answers a bare 500 (optio-core logs the failure line on the process first).
- The launch, resume-from and resurrect guards are per engine process: two engines on one prefix are
  not guarded against each other.

## Testing

- optio-agents helper: listing (valid only, order, required vs optional blobs), discarding (exact
  timestamp, deletes newer documents and their blobs, keeps older, refuses unknown timestamps, deletes
  nothing on refusal).
- Each wrapper: its hooks are wired with its collection and fields (one test per wrapper).
- optio-core: listing dispatch (hook, no hook), launch with `resumeFrom` (each refusal; success calls
  the hook then launches with resume; no deletion on refusal; the order of checks).
- optio-api: the route (shape, scope, errors), the launch body passing `resumeFrom` through.
- optio-ui: the hook's matrix (all existing cases unchanged; previous-snapshot items shown only for
  older valid snapshots, at most 4, behind the confirmation with the count; the resurrect case's
  confirmation; the launch gate), `LaunchControls` rendering it, `useProcessSnapshots`.
- excavator (after the optio release): the process page uses optio-ui's hook; the excavator copy and
  its tests go.

## Out of scope

- Viewing an older snapshot's conversation before resuming.
- Keeping the discarded snapshots (no "forward" after going back).
- Changing retention.
