# Auto-Resume Tasks on Engine Restart

This spec was written against the following baseline:

**Base revision:** `919b5fb649a365cc23d724ca14c22e2ca299d8f0` on branch `main` (as of 2026-06-06T03:26:24Z)

## Summary

When the optio engine is killed (for any reason), it shuts down gracefully: every running
background process is told to cancel, giving each a chance to save durable state. A
long-running task (e.g. a free-style analysis) that saved its state can be resumed later —
but resume is **manual today**. Someone has to remember to do it.

This feature makes resume automatic for opted-in tasks. A task definition gains an
`auto_resume` flag. When the engine shuts down, processes of such tasks (top-level only) are
stamped "scheduled for auto-restart" on their process document. On the next engine start, a
one-shot timer waits a configurable delay (default 5 minutes) and then re-launches every
stamped process that genuinely saved its state. The delay exists to let the environment
settle — in dev mode, code edits trigger rapid restart bursts, and we do not want to thrash
re-launches.

The feature spans two repositories:
- **optio** (`packages/optio-core`, `packages/optio-contracts`, `packages/optio-ui`) — all
  the machinery: the flag, the validation, the stamping, the timer, the contract field, the
  UI indicator.
- **excavator** (`packages/engine`) — opts the two free-style analysis tasks into the flag.

## Goals

- A task can declare `auto_resume=True` and, after an engine restart, have its interrupted
  top-level processes resumed automatically without human action.
- Only processes that **gracefully saved their state** are auto-resumed. Processes that were
  force-killed (failed to stand down within the grace window, no saved state) are **not**
  resumed — they remain `failed` for a human to inspect.
- The re-launch is deferred by a configurable delay so rapid restart bursts (dev mode) do not
  cause thrashing.
- The pending-auto-resume status is visible in the UI across every place a process is shown.

## Non-Goals

- Resuming non-top-level (child) processes automatically. Only roots (`depth==0`) are eligible.
- Resuming force-killed processes from scratch. (Considered and explicitly rejected — see
  Decision D1.)
- Any new mid-run "restart on demand" mechanism. That is a separate, unrelated feature
  (`csillag/restart-on-demand`, opencode-agent-initiated `RESTART` via a log keyword). The
  naming proximity is coincidental; keep the two concepts distinct.

## Background — relevant existing machinery

(All file:line references are against the base revision in optio unless stated otherwise.)

- **Shutdown** — `optio-core/src/optio_core/lifecycle.py:935` `Optio.shutdown()`. Iterates all
  active processes in `_executor._cancellation_flags`, sets each cancel flag with a deadline
  (grace = `cancel_grace_seconds`, default 5s), waits for drain, then runs a force-cancel pass
  (`lifecycle.py:991`) over any stragglers.
- **Force-cancel** — `optio-core/src/optio_core/_force_cancel.py:30`
  `_write_force_cancelled_state()`. Writes terminal state `failed` ("Task did not unwind within
  cancellation grace period") for processes still in `ACTIVE_STATES`.
- **Init reconcile** — `lifecycle.py:210`. On `init()`, any process still in `ACTIVE_STATES`
  (i.e. left `running` from a hard crash) is marked `failed` with "Process was interrupted by
  server restart". Graceful savers are already in `cancelled`, so this transition does not
  touch them.
- **Task sync** — `lifecycle.py:220`. Syncs task definitions from the generator into the
  registry. Natural home for config validation.
- **Run / startup** — `lifecycle.py:881` `Optio.run()`. Registers signal handlers, starts the
  scheduler, heartbeat, and supervisor loops, then blocks on the shutdown event. Natural home
  for a one-shot startup timer.
- **Launch / resume** — `lifecycle.py:387` `Optio.launch(process_id, resume=False, *, session_id)`.
  Checks `LAUNCHABLE_STATES` (includes `cancelled`), and when `resume=True` validates
  `supportsResume==True`. Fire-and-forget.
- **Task definition** — `optio-core/src/optio_core/models.py:63` `TaskInstance` dataclass.
  Already carries `supports_resume: bool = False`, `cancellable`, `ttl_seconds`,
  `auto_cancel_children`, etc.
- **Process doc** — Python upsert in `optio-core/src/optio_core/store.py`; TS contract in
  `optio-contracts/src/schemas/process.ts:41`. Already carries `supportsResume`,
  `hasSavedState`, `depth`, `status.state`.
- **UI status chokepoint** — `optio-ui/src/components/ProcessStatusBadge.tsx:61`. Every
  excavator process display (SourcesTable, SourceOverview, ProcessDetailPage, RecentSourceActivity,
  ProcessTreeView, ProcessItem) renders state through this single component.
- **Excavator opt-in sites** — `excavator/packages/engine/src/engine/free_style/task.py:174`
  (opencode) and `.../free_style/claudecode_task.py:76` (claudecode). Both already set
  `supports_resume=True` and implement saved-state/resume hooks.

## Design

### 1. Task flag (definition layer)

Add a field to `TaskInstance` (optio-core `models.py:63`):

```python
auto_resume: bool = False
```

Semantics: "if a top-level process of this task is interrupted by an engine shutdown and it
saved its state, resume it automatically after the next engine start (post-delay)."

### 2. Validation (sync layer)

At task-sync (`lifecycle.py:220`), when registering definitions:

- If `auto_resume == True` and `supports_resume == False` → **raise, hard-failing engine
  startup.** You cannot resume a task that does not support resume; this combination is a
  configuration error and must be caught loudly, not silently ignored.

The **top-level-only** restriction is **not** validated here. A task *definition* does not know
the *depth* of its future process instances (a task may run as a root in one place and a child
in another). The restriction is therefore enforced at stamp-time (§4), where the concrete
process depth is known.

### 3. Process-document stamp

Add a new field `autoResumeScheduled: bool` (default `false`) to:

- the Python process document and its `store.py` upsert,
- the TS `ProcessSchema` (`optio-contracts/src/schemas/process.ts`),
- the `ProcessStateLike` helper interface (`optio-ui/src/process-state.ts`).

This field is the **authority** for what gets auto-resumed — not a re-derivation from the live
task config at boot time (the config may have changed; see Decision D4).

### 4. Shutdown — stamping (`lifecycle.py:968`)

While `shutdown()` signals cancellation to each active process, before/independent of whether
the process drains in time:

- Look up the process's `TaskInstance` by `processId` in the registry.
- If `task.auto_resume == True` **AND `process.depth == 0`** → set `autoResumeScheduled = true`
  on the process document.

Outcomes after the grace window:
- **Graceful saver** → transitions to `cancelled`, with `hasSavedState == true`. Stamp remains.
  Eligible for auto-resume.
- **Non-saver** → force-cancelled to `failed` (this shutdown's force-cancel pass, or next boot's
  init reconcile). Stamp is cleared by §5. Never auto-resumed.

### 5. Failed transition clears the stamp (single rule)

**Any** transition of a process to `failed` clears `autoResumeScheduled`. This single rule
covers both paths that produce a force-killed terminal state:

- the shutdown force-cancel pass (`_force_cancel.py:30` `_write_force_cancelled_state()`), and
- the next-boot init reconcile (`lifecycle.py:210`).

Implementing the clear at these two write sites (rather than scattering it) keeps the invariant
"a `failed` process is never stamped" enforced where `failed` is actually written.

### 6. Startup timer (`run()`)

Add a **one-shot** timer, scheduled once when `run()` starts, firing a single time after a
configurable delay:

- New engine config value `auto_resume_delay_seconds`, default **300**, alongside the existing
  `cancel_grace_seconds`. Dev can shorten or zero it.
- On fire, query the process store for documents where:
  `autoResumeScheduled == true AND status.state == "cancelled" AND hasSavedState == true`.
- For each match: call `launch(resume=True)`, then clear `autoResumeScheduled`.
- If a match is not actually launchable (e.g. blocked by a persistent launch-block / perma-ban,
  or no longer in a launchable state): log it, clear the stamp, and skip. No retry loop.

The timer fires exactly once per engine run. If the engine shuts down again before it fires,
the stamps simply persist and the next run's timer picks them up (§8).

### 7. Manual launch clears the stamp

In `launch()` (`lifecycle.py:387`): if the process being launched carries
`autoResumeScheduled == true`, clear it as part of the launch. A human who resumes the process
before the timer fires has pre-empted the auto-resume; the timer must not double-launch it.

### 8. Edge cases

- **Second shutdown before the timer fires.** Stamps are persisted on the documents and survive.
  The next engine run's one-shot timer picks them up. No special handling needed.
- **Config changed away from `auto_resume` between shutdown and boot.** The stamp is the
  authority (§3); the process is still resumed. This is correct: the intent was recorded at
  shutdown time.
- **Dev-mode thrash.** Rapid restart bursts each leave stamps intact and re-arm a fresh
  one-shot timer; only when a run survives `auto_resume_delay_seconds` does the resume actually
  happen. The delay absorbs the bursts.
- **Timer fires with zero eligible processes.** No-op.
- **Multiple eligible roots.** All are launched (fire-and-forget, via the existing executor
  path, which handles concurrency).

### 9. UI

In `ProcessStatusBadge` (`optio-ui/src/components/ProcessStatusBadge.tsx:61`) — the single
component every process display routes through:

- When `autoResumeScheduled == true`, render a **secondary indicator** next to the main status
  tag: a stopwatch/clock icon (Ant Design `FieldTimeOutlined` or `ClockCircleOutlined`) with a
  tooltip explaining the process is scheduled for auto-restart.
- This is **not** a new `status.state` value. The process remains genuinely `cancelled`;
  auto-resume is an orthogonal annotation. Modeling it as a state would corrupt the state
  machine and lose the `cancelled` information.

Because every excavator process surface (dashboard activity, sources table, source overview
cards, process detail header, process tree nodes, process list items) renders through this one
badge, the indicator appears everywhere with no per-consumer edits.

### 10. Excavator opt-in

Set `auto_resume=True` at exactly two sites, both of which already set `supports_resume=True`
and implement saved-state/resume hooks:

- `excavator/packages/engine/src/engine/free_style/task.py:174` (opencode analysis)
- `excavator/packages/engine/src/engine/free_style/claudecode_task.py:76` (claudecode analysis)

No other excavator task opts in.

## Data flow

1. Task author sets `auto_resume=True` on a `TaskInstance` (excavator).
2. At engine start, task-sync validates the flag against `supports_resume` (§2).
3. A user launches the task as a top-level process; it runs.
4. Engine shutdown: the process is told to cancel and, being a root of an `auto_resume` task,
   its doc is stamped `autoResumeScheduled=true` (§4).
5. The process either saves state → `cancelled` (stamp kept), or is force-killed → `failed`
   (stamp cleared, §5).
6. Next engine start: the one-shot timer arms for `auto_resume_delay_seconds` (§6).
7. Timer fires: eligible stamped+cancelled+saved processes are `launch(resume=True)`-ed and
   un-stamped.
8. Throughout, the UI shows a stopwatch indicator on any stamped process (§9).

## Decisions

- **D1 — Force-killed processes are not auto-resumed.** Only graceful savers (`cancelled` +
  `hasSavedState`) are resumed. A force-killed process has no saved state; restarting it from
  scratch is treated as a real failure for a human to decide on, not an automatic action.
- **D2 — Incoherent config hard-fails the engine.** `auto_resume=True` without
  `supports_resume=True` raises at sync time rather than warning-and-ignoring.
- **D3 — Top-level only, enforced at stamp-time.** The restriction lives where process depth is
  known (shutdown), not at definition validation.
- **D4 — The stamp is the authority for resume.** Eligibility at timer-fire is read from the
  persisted `autoResumeScheduled` field, not re-derived from live task config.
- **D5 — `autoResumeScheduled` is an annotation, not a lifecycle state.** It rides alongside
  `cancelled`; it is not a new `status.state` value.
- **D6 — Configurable delay, default 300s, one-shot per run.** Absorbs dev-mode restart bursts.

## Testing

**optio-core**
- Validation: a task with `auto_resume=True, supports_resume=False` makes engine startup raise.
- Shutdown stamping: a root process of an `auto_resume` task gets `autoResumeScheduled=true`;
  a **child** process of the same task does **not**; a process of a non-`auto_resume` task does
  not.
- Failed clears stamp: a stamped process force-cancelled at shutdown, and a stamped process
  reconciled to `failed` at init, both end with `autoResumeScheduled=false`.
- Timer resume: a stamped + `cancelled` + `hasSavedState` process is `launch(resume=True)`-ed
  after the configured delay and un-stamped; a stamped but `failed` process is not; a stamped
  process blocked by a launch-block is skipped, logged, and un-stamped (no retry).
- Manual launch: launching a stamped process clears `autoResumeScheduled`.
- Delay config: `auto_resume_delay_seconds` is honored (use a small value in tests).

**excavator**
- The two free-style analysis tasks carry `auto_resume=True`; no other task does.

**optio-ui**
- `ProcessStatusBadge` renders the stopwatch indicator + tooltip when
  `autoResumeScheduled==true`, and does not when false/absent.
