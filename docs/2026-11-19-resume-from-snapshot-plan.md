# Resume from a previous snapshot: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Next to Resume and Restart, the launch menu of a suspended process offers "Resume from previous snapshot (<time>)" for each older valid snapshot; choosing one deletes the newer snapshots and resumes as usual.

**Architecture:** Two new optional `TaskInstance` hooks: `list_snapshots` (read-only) and `discard_snapshots_after`. optio-core dispatches them: an engine RPC `listSnapshots`, and `launch(..., resume_from=)` deletes the newer snapshots after every launch check has passed and then runs an ordinary resume. One optio-agents module implements both hooks for all seven wrappers; each wrapper declares its snapshot layout. optio-api adds `GET /processes/:id/snapshots` and passes `resumeFrom` through on launch. optio-ui gets the launch-actions hook from excavator (vultus `useAction`, a fixed set of slots); `LaunchControls` renders it.

**Tech Stack:** Python 3.11+ (motor, GridFS, pytest, pytest-asyncio), TypeScript (zod, ts-rest, clamator codegen, vitest), React + antd 6 + vultus-core / vultus-antd (optio-ui).

**Spec:** `docs/2026-11-19-resume-from-snapshot-design.md` (owner decisions, 2026-11-19). Code facts and watch-outs: `/home/csillag/db-writing-work/scratch/resume-from-snapshot-notes.md`.

## Questions for the controller

1. **Resume from a snapshot when `hasSavedState` is false.** Today a plain resume without saved state is quietly turned into a fresh run (`lifecycle.py` `_launch_record`, "Downgrade rather than reject"). For `resume_from`, that would ignore the snapshot the user picked. The plan refuses instead, with `not-launchable` (the reason a kept factory launch's resume gets without saved state, `_relaunch_refusal`), and deletes nothing. Confirm this, or name another reason.
2. **Task order.** optio-agents depends on optio-core. The helper raises core's new `SnapshotNotFound`, and the claudecode hook raises `CapturePending`, in the same way a resurrect hook raises `NothingToResurrect`. So the core task has to come first: core, then the helper, then the wrappers, then contracts/codegen/engine RPC, then api, then ui, then docs. The brief put the helper first.
3. **What LaunchControls looks like.** vultus-antd's `CombinedActionButton` has no icon-only mode. Every `LaunchControls` (list rows, tree nodes, the detail view's suspended row) therefore changes from green icon-only text buttons (a play icon and a chevron) to a labelled split button ("Resume" and "⋯"). Some details change with it:
   - A non-empty `denyReason` disables every action and shows its reason. The split button stays, and the opener shows the reason on each item. Today you get a single disabled play icon.
   - In the resurrect menu, Restart reads "Restart (discard saved state)".
   - A confirmation's OK button carries the action's label, not "Discard and continue".

   Spec decision 4 implies all of this, and the owner usually judges such changes on before/after screenshots. Should Task 9 go ahead as planned, or should `CombinedActionButton` get an icon-only option in vultus-antd first?

## Findings and decisions taken here (derived from the spec and the code)

- **F1 Invalid newer documents.** `discard_snapshots_after` deletes every document newer than the chosen one, valid or not. An invalid newest document would otherwise be the one Resume reads (`load_latest_snapshot` does not check blobs). The confirmation's count comes from the list, which holds valid snapshots only. So when a newer document has lost a blob, the count can be lower than what is deleted.
- **F2 Blob fields.** claudecode's `migratedFromWorkdirBlobId` is the migration's transient field naming an old workdir blob. It counts as an optional blob field: deleting its document would otherwise orphan that blob for good. The spec asks for "all blob fields they name".
- **F3 Timestamp wire form.**
  - The helper writes `capturedAt` as `YYYY-MM-DDTHH:MM:SS.mmmZ`: UTC, exactly three fraction digits, since BSON datetimes hold milliseconds.
  - The contracts carry it as `z.string().min(1)`, not `.datetime()`. Codegen would turn a `.datetime()` field into a Python datetime, and the exact spelling would be lost.
  - The helper accepts any ISO 8601 spelling that has an offset. These match nothing and are refused with `snapshot-not-found`: a string without an offset, one with non-zero digits below the millisecond, and one that is not ISO at all.
- **F4 HTTP statuses.**
  - Launch: `invalid-request` → 400. `no-snapshot-support`, `snapshot-not-found` and `capture-pending` → 409, so 404 keeps meaning "no such process".
  - `GET /processes/:id/snapshots`: `not-found` and `no-snapshot-support` → 404, as the conversation route's `no-conversation-support`.
  - The new route is not added to `apiToFrontendRouteErrorReasons`, because its errors are never shown (the same as `getConversation`). The launch route's reasons grow by four through `LaunchFailureReason`.
- **F5 Order of the checks in `launch` with `resume_from`.** shutting-down → `invalid-request` (no `resume=True`) → not-found → the factory and kept-launch refusals as today → not-launchable (resurrecting) → not-launchable (state) → no-resume-support → not-launchable (no saved state, Q1) → launch-blocked → no-snapshot-support → the hook's own `capture-pending` (claudecode), then `snapshot-not-found`. Only after all of these does anything get deleted, and then comes the launch.
- **F6 Contexts.** The list hook gets a `ReadOnlyProcessContext`, as the spec says. The discard hook writes, so it gets the detached `ProcessContext` that `Optio.resurrect` uses (`Optio._detached_context`).
- **F7 Process log.** A successful `resume_from` appends one event line before the launch: `Resuming from the snapshot of <capturedAt>: <n> newer snapshot(s) discarded`.
- **F8 Details of the UI hook.**
  - Action ids stay excavator's: `process.resurrect:<id>`, `process.resume:<id>`, `process.restart:<id>`, `process.launch:<id>`. Excavator's `useChatRunActions` filters on `process.restart:`. The new slots are `process.resumeFrom:<id>:<0..3>`.
  - Texts are `t(key, { defaultValue })`, with excavator's English (`en.json`) as the defaults, so excavator's labels do not change.
  - `<time>` is formatted in the i18n language (`i18n.resolvedLanguage ?? i18n.language`), with `dateStyle: 'medium'` and `timeStyle: 'short'`. Items whose short form collides get `timeStyle: 'medium'`, which shows seconds.
  - While a resurrect runs, the Resurrect action's icon is the spinner slot (`optio:process:<id>:resurrecting`), as in LaunchControls today. Excavator's page gains that icon in place of the medicine box.
- **F9 `useProcessSnapshots(processId, { enabled, runEndedAt? })`.** `runEndedAt` is added to the options and becomes part of the query key, so each end of a run reads the list again. `useProcessConversation` keys its record the same way.
- **F10 What LaunchControls keeps.**
  - The task-factory branch and the kept-launch branch (Resume and Remove) are unchanged.
  - A task's `warning` is still asked before a fresh launch. It moves from a Popconfirm to a confirm modal in LaunchControls' own `triggerLaunch`, as excavator's page does it.
  - Without `onResurrect`, a resurrecting process still shows the disabled Resurrecting button: LaunchControls passes a no-op `triggerResurrect` only while `isResurrecting(process)`.
- **F11 Noticed, out of scope.** The express and nextjs adapters drop the launch body's `sessionId` (they pass `null`); fastify forwards it. This plan does not touch that.

## Global Constraints

Values from the spec (verbatim):
- Menu: "Resume" (primary, the newest snapshot), then "Resume from previous snapshot (<time>)" (one item per valid snapshot older than the newest, at most 4, newest first), then "Restart (discard saved state)". In the resurrect case, "Resurrect" (primary), then "Resume from last snapshot", then the previous-snapshot items, then Restart. While a resurrect runs, only Resurrecting is shown.
- Confirmation, e.g. "Resume from 19 Nov 13:05? This discards 2 newer snapshots.", followed by the sentence "What the discarded runs delivered stays in the app." In the resurrect case it also names the unsaved work it discards.
- The items appear only when the list holds older valid snapshots. While the list loads or cannot be read, the menu shows Resume and Restart as today, with no error.
- The launch gate holds the new items back with the same reason.
- `GET /processes/:id/snapshots` → `{ snapshots: [{ capturedAt, endState }] }`: newest first, valid snapshots only, the newest included (the UI skips it). Access: whoever may see the process.
- Engine RPC `listSnapshots({ processId })` → the same shape. Failure reasons, exactly: `not-found`, `no-snapshot-support`.
- Launch body and engine launch params: `resumeFrom?: string` beside `resume`. `resumeFrom` without `resume: true` → `invalid-request`.
- New launch reasons, exactly: `invalid-request`, `no-snapshot-support`, `snapshot-not-found`, `capture-pending`. Nothing is deleted when a check fails.
- TaskInstance hooks `list_snapshots` and `discard_snapshots_after`. Helper `list_snapshots(db, collection, process_id, required_blob_fields)` and `discard_snapshots_after(db, collection, process_id, captured_at, ...)`.
- `useProcessLaunchActions(process, triggerLaunch, triggerResurrect?, launchGate?, snapshots?)` → `ActionStatus[]`, in this order: Resurrect, Resume, four previous-snapshot slots, Restart, Launch. `triggerLaunch` gets `{ resume?: boolean, resumeFrom?: string }`.
- Unchanged: retention (`SNAPSHOT_RETENTION` 5), every wrapper's capture, prune and restore code, the readers of "the latest snapshot", auto-resume, Resurrect, and the conversation archive.
- In every task's tests, `TS` is `"2026-11-19T13:05:12.345Z"`: one listed `capturedAt` in the wire form of F3.

Repo rules (optio `AGENTS.md`):
- No `Co-Authored-By` or other self-credit lines in commits. Commit messages follow `feat(<pkg>): ...` and are written to a file with the Write tool (`/home/csillag/db-writing-work/scratch/agents/rfs-t<N>-commit.txt`).
- Stage explicit paths only. Never stage `pnpm-lock.yaml`: it is modified locally on purpose. Another agent has uncommitted work in `packages/optio-conversation-ui`, which must not be touched.
- Commit procedure, per task: `git -C /home/csillag/db-writing-work/optio add <abs paths>`, then `git -C /home/csillag/db-writing-work/optio commit -F <msg file> -- <the same abs paths>`. The path-limited commit keeps out anything another agent has staged.
- When a package's public API, exports, props, hooks or contract endpoints change, update that package's `AGENTS.md` and the matching lines of the root `AGENTS.md` **in the same commit**. Task 10 then covers the documents outside the packages.
- No test may depend on wall-clock time. Wait for a condition with a 60 s ceiling (`waitFor(..., { timeout: 60_000 })`, `_wait_until(..., 60.0)`), never sleep-then-assert.
- The controller picks the branch. Nothing in this plan pushes, publishes or releases.

Test commands on this host. Write each abbreviation out literally in the actual call; the shell rule forbids variables.
- `PYTEST <pkg> <file>` = `/home/csillag/db-writing-work/scratch/agents/optio-env.sh /home/csillag/db-writing-work/optio/.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/home/csillag/db-writing-work/scratch/optio-tmp/pytest-<pkg> /home/csillag/db-writing-work/optio/packages/<pkg>/tests/<file>`. `optio-env.sh` sets `TMPDIR` to `scratch/optio-tmp` and `MONGO_URL` to `mongodb://127.0.0.1:27118/?directConnection=true&serverSelectionTimeoutMS=5000`. The tests use throwaway databases. Never point anything at `127.0.0.1:27017`.
- `VITEST <pkg> <file>` = `/home/csillag/db-writing-work/scratch/agents/optio-env.sh /home/csillag/db-writing-work/scratch/shims/pnpm --dir /home/csillag/db-writing-work/optio/packages/<pkg> exec vitest run /home/csillag/db-writing-work/optio/packages/<pkg>/src/__tests__/<file>`. Run one file at a time.
- `TSC <pkg>` = `/home/csillag/db-writing-work/scratch/shims/pnpm --dir /home/csillag/db-writing-work/optio/packages/<pkg> exec tsc --noEmit -p /home/csillag/db-writing-work/optio/packages/<pkg>/tsconfig.json`.
- `BUILD_CONTRACTS` = `/home/csillag/db-writing-work/scratch/shims/pnpm --dir /home/csillag/db-writing-work/optio/packages/optio-contracts run build`. optio-api and optio-ui read optio-contracts' `dist`.
- `CODEGEN` = `/home/csillag/db-writing-work/scratch/agents/optio-env.sh make -C /home/csillag/db-writing-work/optio codegen CLAMATOR=/home/csillag/db-writing-work/optio/node_modules/.bin/clamator-codegen`.
- Run only the touched test files a task names, plus the existing files it lists. No full suite.
- Shell: the controller's brief carries the full shell rule. In short: one plain command per call, literal absolute paths, no environment assignments, no code on the command line, and `git -C` for git.

## Review Focus

1. **Deleting blobs.** Only blobs that a deleted (newer) document names *and* that no remaining document of the process names, in any blob field. Never an older or shared blob. Documents go first, then blobs, as in prune. (Task 2: `test_discard_keeps_a_blob_a_kept_snapshot_names`, `test_discard_deletes_newer_docs_and_their_blobs`.)
2. **Exact matching at millisecond precision across ISO strings and BSON datetimes.** Every ISO spelling of the instant matches. No offset, non-zero sub-millisecond digits, another millisecond, or garbage match nothing. Naive and tz-aware Mongo clients give the same answers. (Task 2: `test_discard_matches_any_iso_spelling_of_the_instant`, `test_discard_refusals_delete_nothing`, `test_tz_aware_client`.)
3. **No deletion on refusal.** Every core refusal is decided before the hook runs, and the helper's and claudecode's refusals delete nothing. (Task 1: `test_refusals_before_the_hook`, `test_check_order`. Task 2: `test_discard_refusals_delete_nothing`. Task 3: `test_capture_pending_refuses_and_deletes_nothing`.)
4. **The resurrect case.** Its previous-snapshot items sit between "Resume from last snapshot" and Restart. Their confirmation names the count, the deliveries and the unsaved work. While a resurrect runs, only Resurrecting is shown. (Task 8: `resurrect case: ...`, `resurrecting: Resurrecting only`. Task 9: the LaunchControls resurrect tests.)
5. **A fixed number of `useAction` calls.** Always 8 statuses, rerendered across null, empty and full lists without a hook-order error. (Task 8: `a fixed number of statuses and hooks`.)
6. **Access scope of the new route.** A process outside the scope, or hidden by visibility, gets 404 without the engine being called. `authorize` is never asked, and the engine is asked by `_id`. (Task 6: `getProcessSnapshots` tests, and the snapshot case of the adapters' test.)

---

### Task 1: optio-core: the hook fields, the refusals, the list dispatch, launch with `resume_from`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
  - `TaskInstance`: two fields after `read_conversation`.
  - `LaunchOutcome.reason`: four new values.
  - New `ListSnapshotsOutcome` after `ResurrectOutcome`.
- Modify: `packages/optio-core/src/optio_core/exceptions.py`: `SnapshotNotFound`, `CapturePending`.
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`: `launch`, `_launch_record`, new `list_snapshots`.
- Modify: `packages/optio-core/src/optio_core/__init__.py`: exports, and `list_snapshots = _instance.list_snapshots`.
- Modify: `packages/optio-core/AGENTS.md`: a section "TaskInstance.list_snapshots / discard_snapshots_after" after "TaskInstance.resurrect"; `Optio.list_snapshots`; the launch reasons and `resume_from`.
- Modify: `AGENTS.md` (root): in "Public API", the `launch` line (signature, reasons, order) and a `list_snapshots` line; in "TaskInstance Fields", the two fields.
- Test: create `packages/optio-core/tests/test_resume_from_snapshot.py`.

**Interfaces:**
- Produces:
  - `TaskInstance.list_snapshots: Callable[[ReadOnlyProcessContext], Awaitable[list[dict]]] | None = None`. Returns `[{"capturedAt": str, "endState": str}]`, newest first.
  - `TaskInstance.discard_snapshots_after: Callable[[ProcessContext, str], Awaitable[int]] | None = None`. Deletes the snapshots newer than the given `capturedAt` and returns how many it deleted. Raises `SnapshotNotFound` or `CapturePending`, having deleted nothing.
  - `optio_core.SnapshotNotFound(Exception)` and `optio_core.CapturePending(Exception)`.
  - `optio_core.ListSnapshotsOutcome(ok: bool, reason: Literal["not-found", "no-snapshot-support"] | None = None, snapshots: list[dict] | None = None)`, a frozen dataclass.
  - `Optio.list_snapshots(process_id: str) -> ListSnapshotsOutcome`, also as `optio_core.list_snapshots`.
  - `Optio.launch(process_id, resume=False, *, session_id, params=None, ephemeral=None, metadata=None, resume_from: str | None = None) -> LaunchOutcome`.
  - `LaunchOutcome.reason` gains `"invalid-request"`, `"no-snapshot-support"`, `"snapshot-not-found"`, `"capture-pending"`.

- [ ] **Step 1: Write the failing tests** in `test_resume_from_snapshot.py`.

  Helper: `_setup(mongo_db, prefix, *, list_hook=None, discard_hook=None, state="cancelled", saved=True, supports_resume=True, metadata=None)`. It follows `_setup` in `tests/test_resurrect.py`: `adhoc_define` a `TaskInstance` with the hooks, `$set` `status.state` and `hasSavedState`, and replace `fw._executor.launch_process` with a spy. The spy appends `("launch", resume)` to an `events` list that the hooks share; the discard hook appends `("discard", captured_at)`.

  Tests (`TS` as in Global Constraints):
  - `test_list_snapshots_not_found`: `await fw.list_snapshots("missing") == ListSnapshotsOutcome(ok=False, reason="not-found")`.
  - `test_list_snapshots_without_hook`: `reason == "no-snapshot-support"`.
  - `test_list_snapshots_runs_the_hook_on_a_read_only_context`:
    - the hook returns `[{"capturedAt": TS, "endState": "done"}]`;
    - the outcome is `ok=True` with exactly that list;
    - the hook's `ctx.process_id == "r1"`;
    - `ctx.report_progress` raises `AttributeError`.
  - `test_resume_from_without_resume_is_invalid_request`: `launch("r1", resume=False, session_id=None, resume_from=TS)` gives reason `"invalid-request"`, and `events == []`.
  - `test_refusals_before_the_hook`, parametrized. In each case `events == []`:
    - state `running` → `not-launchable`;
    - `fw._resurrecting[oid]` set → `not-launchable`;
    - `supportsResume` false on the record → `no-resume-support`;
    - `hasSavedState` false → `not-launchable` (Q1);
    - `async with fw.block_launches({"project": "p1"})` on metadata `{"project": "p1"}` → `launch-blocked`.
  - `test_no_hook_is_no_snapshot_support`: the task has `list_snapshots` but no `discard_snapshots_after` → `no-snapshot-support`, no launch.
  - `test_hook_refusals_map_to_reasons`, parametrized: the hook raises `SnapshotNotFound(TS)` → `snapshot-not-found`; it raises `CapturePending("p")` → `capture-pending`. In both cases no `("launch", ...)` event.
  - `test_success_discards_then_resumes`:
    - the hook returns 2;
    - `outcome.ok`;
    - `events == [("discard", TS), ("launch", True)]`;
    - the hook's ctx is a `ProcessContext` with `process_id == "r1"`;
    - the log holds `"Resuming from the snapshot of 2026-11-19T13:05:12.345Z: 2 newer snapshots discarded"` (singular "snapshot" for 1).
  - `test_check_order`:
    - a running process without hooks, `resume=False, resume_from=TS` → `invalid-request`;
    - the same with `resume=True` → `not-launchable`;
    - an idle, saved, blocked process without hooks → `launch-blocked`.
  - `test_plain_resume_does_not_call_the_hook`:
    - `launch(resume=True)` without `resume_from` → `events == [("launch", True)]`;
    - with `hasSavedState` false it is still downgraded (`("launch", False)`).
  - `test_exports`: `from optio_core import SnapshotNotFound, CapturePending, ListSnapshotsOutcome, list_snapshots`.

- [ ] **Step 2: Run** `PYTEST optio-core test_resume_from_snapshot.py`. Expected: FAIL (ImportError on `SnapshotNotFound`).

- [ ] **Step 3: Implement.**
  - In `launch`: the `invalid-request` check (`resume_from is not None and not resume`) goes right after the shutting-down check. Thread `resume_from` into `_launch_record(proc, resume, session_id, resume_from=None)` on the plain path and on the kept-launch path. `_launch_child` passes none.
  - In `_launch_record`, with `resume_from`:
    - no saved state → `not-launchable` (no downgrade);
    - after the launch-block check: no task, or no `discard_snapshots_after` → `no-snapshot-support`;
    - await the hook with `self._detached_context(proc, session_id=session_id)`, mapping the two exceptions to their reasons and letting any other exception propagate;
    - append the F7 log line (`append_log(..., "event", ...)`), then launch with `resume=True` as today.
  - `list_snapshots`: `_resolve`, then the task from `self._executor._task_registry`, then `await hook(ReadOnlyProcessContext(self._detached_context(proc)))`.
  - Update the docstrings of `launch` (the reasons and their order, F5) and of `LaunchOutcome`.
  - In `packages/optio-core/AGENTS.md`, put the spec's "Known behaviour" about the deletion in the hooks section: what the discarded runs delivered stays, and going "forward" again is not possible.

- [ ] **Step 4: Run** `PYTEST optio-core <file>` for `test_resume_from_snapshot.py`, `test_resurrect.py`, `test_outcomes.py`, `test_launch_guard.py`, `test_task_factory.py`, `test_conversation_archive.py` and `test_models.py`, one at a time. Expected: all PASS.

- [ ] **Step 5: Commit** these paths: `models.py`, `exceptions.py`, `lifecycle.py`, `__init__.py` (under `packages/optio-core/src/optio_core/`), the new test, `packages/optio-core/AGENTS.md` and `AGENTS.md`. Message: `feat(optio-core): snapshot list and discard hooks; launch(resume_from=) resumes from an older snapshot`.

---

### Task 2: optio-agents: the snapshot-history helper

**Files:**
- Create: `packages/optio-agents/src/optio_agents/snapshots.py` (a submodule; the package `__init__` is not changed).
- Modify: `packages/optio-agents/AGENTS.md`: a section "Snapshot history (`optio_agents.snapshots`)". It carries the API below, F1, F3, and the spec's "Known behaviour":
  - deliveries stay;
  - the conversation view continues from the chosen snapshot's record;
  - the restore replaces the host workdir.
- Test: create `packages/optio-agents/tests/test_snapshots.py`.

**Interfaces:**
- Consumes: Task 1 (`optio_core.SnapshotNotFound`, `optio_core.CapturePending`, the hook types).
- Produces:
  - `SnapshotLayout(suffix: str, required_blob_fields: tuple[str, ...], optional_blob_fields: tuple[str, ...] = ())`, a frozen dataclass; `.collection(prefix) -> str` is `f"{prefix}{suffix}"`.
  - `async list_snapshots(db, collection: str, process_id: str, required_blob_fields: Sequence[str]) -> list[dict]`.
  - `async discard_snapshots_after(db, collection: str, process_id: str, captured_at: str, required_blob_fields: Sequence[str], optional_blob_fields: Sequence[str] = ()) -> int`.
  - `snapshot_hooks(layout: SnapshotLayout, *, capture_pending: Callable[[ProcessContext], Awaitable[bool]] | None = None) -> SnapshotHooks`. `SnapshotHooks` is a `NamedTuple(list_snapshots, discard_snapshots_after)` whose members have core's hook signatures. The discard hook awaits `capture_pending(ctx)` first and raises `CapturePending` when it is true, before any read or deletion.

**Algorithm:**
- *Valid* means every required field is set and names an id that has an `fs.files` document. One `$in` query covers every required id of the process.
- `capturedAt` out: as UTC (a naive datetime is UTC), formatted `YYYY-MM-DDTHH:MM:SS.mmmZ`. `endState` as stored, `""` if absent.
- Parsing `captured_at`: `datetime.fromisoformat`. It must carry an offset and `microsecond % 1000 == 0`; anything else matches nothing. All comparisons are done in Python on aware UTC datetimes, never in a query.
- Discard:
  1. Load every document of the process (its `capturedAt` and every blob field).
  2. The chosen document is a valid one whose `capturedAt` equals the instant. None → `SnapshotNotFound(captured_at)`.
  3. *Newer* are all documents with a later `capturedAt`, valid or not (F1).
  4. `delete_many` them by `_id`.
  5. For each blob id a newer document names in any blob field, and that no remaining document of the process names in any blob field: `AsyncIOMotorGridFSBucket(db).delete(id)`. Ignore `NoFile`; log any other error as a warning without raising.
  6. Return the number of newer documents.

- [ ] **Step 0: Install editable:** `/home/csillag/db-writing-work/scratch/agents/optio-env.sh /home/csillag/db-writing-work/optio/.venv/bin/python -m pip install -e /home/csillag/db-writing-work/optio/packages/optio-host -e /home/csillag/db-writing-work/optio/packages/optio-agents`.

- [ ] **Step 1: Write the failing tests.**

  Helper: `_snap(db, coll, pid, at, *, end="done", fields=("sessionBlobId", "workdirBlobId"), missing=(), extra=None)`. It uploads one real GridFS blob per field (`upload_from_stream`), inserts the document with `capturedAt=at`, and then deletes the blobs named in `missing`. `extra` adds fields as given; that is how a test makes two documents name the same blob.

  Tests (collection `"t_x_session_snapshots"`, process `"p"`):
  - `test_list_valid_only_newest_first`: documents at 10:00, 11:00 and 12:00 on 2026-11-19 UTC; 11:00 has `missing=("workdirBlobId",)`. Expect `[{"capturedAt": "2026-11-19T12:00:00.000Z", ...}, {"capturedAt": "2026-11-19T10:00:00.000Z", ...}]` with their `endState`s.
  - `test_missing_optional_blob_keeps_a_snapshot_valid`: a `conversationBlobId` naming no blob is still listed when only `sessionBlobId` and `workdirBlobId` are required.
  - `test_list_ignores_other_processes_and_is_empty_without_snapshots`.
  - `test_captured_at_is_millisecond_utc`: inserted at `datetime(2026, 11, 19, 13, 5, 12, 345678, tzinfo=UTC)`, listed as `"2026-11-19T13:05:12.345Z"`.
  - `test_discard_deletes_newer_docs_and_their_blobs`:
    - A (10:00), B (11:00) and C (12:00); C also has `conversationBlobId` as an optional field;
    - `discard_snapshots_after(..., "2026-11-19T10:00:00.000Z", ("sessionBlobId", "workdirBlobId"), ("conversationBlobId",))` returns 2;
    - only A remains;
    - B's and C's blob ids have no `fs.files` and no `fs.chunks` left;
    - A's blobs remain.
  - `test_discard_matches_any_iso_spelling_of_the_instant`: with a fresh fixture each time, `"…T13:05:12.345Z"`, `"…T13:05:12.345+00:00"` and `"…T14:05:12.345+01:00"` each match the document at 13:05:12.345.
  - `test_discard_refusals_delete_nothing`, parametrized: `"2026-11-19T13:05:13.345Z"`, `"2026-11-19T13:05:12Z"`, `"2026-11-19T13:05:12.3451Z"`, `"2026-11-19T13:05:12.345"`, `"yesterday"`, and the instant of an invalid document. Each raises `SnapshotNotFound`, and the document count and the `fs.files` count are unchanged.
  - `test_discard_keeps_a_blob_a_kept_snapshot_names`: the newer C names A's `workdirBlobId` in its `conversationBlobId`. After `discard(A)`, that blob still exists and C's own blobs are gone.
  - `test_discard_deletes_invalid_newer_documents_too`: B (newer, `missing=("workdirBlobId",)`); `discard(A) == 1` and B is gone.
  - `test_discard_of_the_newest_deletes_nothing`: returns 0.
  - `test_tz_aware_client`: `mongo_db.client.get_database(mongo_db.name, codec_options=CodecOptions(tz_aware=True))` gives the same list and the same discard result.
  - `test_hooks_follow_the_layout_and_check_the_pending_capture_first`:
    - layout `SnapshotLayout("_x_session_snapshots", ("workdirBlobId",))`;
    - `ctx = SimpleNamespace(process_id="p", _db=mongo_db, _prefix="t")`;
    - `hooks.list_snapshots(ctx)` reads `t_x_session_snapshots`;
    - with `capture_pending` returning True, `hooks.discard_snapshots_after(ctx, ts)` raises `CapturePending`, the spy was awaited once with `ctx`, and nothing is deleted;
    - with `capture_pending=None` the same call discards.

- [ ] **Step 2: Run** `PYTEST optio-agents test_snapshots.py`. Expected: FAIL (ModuleNotFoundError `optio_agents.snapshots`).
- [ ] **Step 3: Implement** `optio_agents/snapshots.py` as above, and write the AGENTS.md section.
- [ ] **Step 4: Run** `PYTEST optio-agents test_snapshots.py`, then `PYTEST optio-agents test_package_exports.py`. Expected: PASS.
- [ ] **Step 5: Commit** the module, the test and `packages/optio-agents/AGENTS.md`. Message: `feat(optio-agents): snapshot history helper: list the valid snapshots, discard the newer ones and their own blobs`.

---

### Task 3: optio-claudecode: wire the hooks, with its pending-capture check

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/snapshots.py`: `SNAPSHOT_LAYOUT` after `SNAPSHOT_RETENTION`.
- Modify: `packages/optio-claudecode/src/optio_claudecode/pending_captures.py`: `async has_pending_capture(ctx) -> bool`, which is `load_pending_capture(ctx._db, ctx._prefix, ctx.process_id) is not None`.
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`: in `create_claudecode_task`, `list_snapshots` and `discard_snapshots_after` come from `snapshot_hooks(SNAPSHOT_LAYOUT, capture_pending=has_pending_capture)` when `config.supports_resume`, else both are None (as `resurrect`).
- Modify: `packages/optio-claudecode/AGENTS.md`: a part "Snapshot history" under "Snapshot capture and unsaved work". It covers the layout, the `capture-pending` refusal ("Resurrect, or a plain Resume, settles it first"), and the spec's note that the model pick lives inside the session blob (`home/.claude/optio-picks.json`), so a snapshot without a pick resumes on the configuration's current default model.
- Test: `packages/optio-claudecode/tests/test_snapshots.py` (append).

**Interfaces:**
- Consumes: Task 2 (`SnapshotLayout`, `snapshot_hooks`).
- Produces: `SNAPSHOT_LAYOUT = SnapshotLayout("_claudecode_session_snapshots", required_blob_fields=("sessionBlobId", "workdirBlobId"), optional_blob_fields=("conversationBlobId", "migratedFromWorkdirBlobId"))`. The suffix is `SESSION_SNAPSHOT_COLLECTION_SUFFIX`, and F2 explains `migratedFromWorkdirBlobId`.

- [ ] **Step 0: Install every wrapper and optio-agents-all editable.** They are needed for this task, Task 4 and codegen's agents-ui step: `/home/csillag/db-writing-work/scratch/agents/optio-env.sh /home/csillag/db-writing-work/optio/.venv/bin/python -m pip install -e /home/csillag/db-writing-work/optio/packages/optio-claudecode -e /home/csillag/db-writing-work/optio/packages/optio-opencode -e /home/csillag/db-writing-work/optio/packages/optio-codex -e /home/csillag/db-writing-work/optio/packages/optio-cursor -e /home/csillag/db-writing-work/optio/packages/optio-grok -e /home/csillag/db-writing-work/optio/packages/optio-kimicode -e /home/csillag/db-writing-work/optio/packages/optio-antigravity -e /home/csillag/db-writing-work/optio/packages/optio-agents-all`.

- [ ] **Step 1: Write the failing tests.** Build the config as in `tests/test_resurrect_hook.py` near line 472.
  - `test_snapshot_layout`: exactly the values above.
  - `test_task_wires_snapshot_hooks`: with `supports_resume=True` both hooks are set; with `supports_resume=False` both are None.
  - `test_hooks_list_and_discard_claudecode_snapshots`:
    - two snapshots via `insert_snapshot`, with real session, workdir and conversation blobs;
    - set their `capturedAt` with `update_one` to 10:00 and 11:00 so they differ;
    - `ctx = SimpleNamespace(process_id="p", _db=mongo_db, _prefix="t")`;
    - `await task.list_snapshots(ctx)` lists both, newest first;
    - `await task.discard_snapshots_after(ctx, "<10:00 ISO>") == 1`;
    - the 11:00 snapshot's three blobs are gone.
  - `test_capture_pending_refuses_and_deletes_nothing`: after `record_pending_capture(...)` for `"p"`, the discard raises `CapturePending`, and both documents and all blobs remain.
- [ ] **Step 2: Run** `PYTEST optio-claudecode test_snapshots.py`. Expected: FAIL (ImportError `SNAPSHOT_LAYOUT`).
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `PYTEST optio-claudecode test_snapshots.py`, then `PYTEST optio-claudecode test_resurrect_hook.py`, then `PYTEST optio-claudecode test_conversation_record.py`. Expected: PASS.
- [ ] **Step 5: Commit** `snapshots.py`, `pending_captures.py` and `session.py` (under `packages/optio-claudecode/src/optio_claudecode/`), `packages/optio-claudecode/tests/test_snapshots.py` and `packages/optio-claudecode/AGENTS.md`. Message: `feat(optio-claudecode): snapshot history hooks; an unfinished capture refuses a resume from an older snapshot`.

---

### Task 4: the other six wrappers: declare the layout and wire the hooks

**Files** (for each `W` in opencode, codex, cursor, grok, kimicode, antigravity):
- Modify: `packages/optio-W/src/optio_W/snapshots.py`: `SNAPSHOT_LAYOUT`.
- Modify: `packages/optio-W/src/optio_W/session.py`: `create_W_task` passes `list_snapshots` and `discard_snapshots_after` from `snapshot_hooks(SNAPSHOT_LAYOUT)` when `config.supports_resume`, else None.
- Modify: `packages/optio-W/AGENTS.md`: one line where it describes its snapshot collection (opencode: "Snapshot collection"), else a short "Snapshot history" section.
- Test: `packages/optio-W/tests/test_snapshots.py` (append).

**Interfaces:**
- Consumes: Task 2.
- Produces the layouts. None of these wrappers has a pending-capture store (`capture_pending=None`) or any optional blob field.

| Wrapper | Suffix | Required blob fields |
|---|---|---|
| opencode | `_opencode_session_snapshots` | `sessionBlobId`, `workdirBlobId` |
| codex | `_codex_session_snapshots` | `workdirBlobId` |
| cursor | `_cursor_session_snapshots` | `workdirBlobId` |
| grok | `_grok_session_snapshots` | `workdirBlobId` |
| kimicode | `_kimicode_session_snapshots` | `sessionBlobId`, `workdirBlobId` |
| antigravity | `_antigravity_session_snapshots` | `workdirBlobId` |

- [ ] **Step 1: Write the failing tests**, three per wrapper.
  - `test_snapshot_layout`: exactly the row above.
  - `test_task_wires_snapshot_hooks`: set with `supports_resume=True`, None with False. Take the minimal config from that wrapper's `tests/test_input_wiring.py` (codex, cursor, grok, antigravity), `tests/test_sanity.py` (opencode) or `tests/test_session_iframe.py` (kimicode).
  - `test_hooks_list_and_discard`: two snapshots through the wrapper's own `insert_snapshot` with real blobs for its required fields, `capturedAt` set to 10:00 and 11:00. List gives two entries; discarding at 10:00 returns 1 and deletes the 11:00 blobs.
- [ ] **Step 2: Run** `PYTEST optio-<W> test_snapshots.py` for each of the six. Expected: FAIL (ImportError `SNAPSHOT_LAYOUT`).
- [ ] **Step 3: Implement** the six layouts and the wirings.
- [ ] **Step 4: Run** `PYTEST optio-<W> test_snapshots.py` for each of the six, then `PYTEST optio-opencode test_sanity.py` and `PYTEST optio-codex test_input_wiring.py`. Expected: PASS.
- [ ] **Step 5: Commit** the 6 × 4 paths. Message: `feat(optio-opencode,optio-codex,optio-cursor,optio-grok,optio-kimicode,optio-antigravity): snapshot history hooks`.

---

### Task 5: contracts, codegen, engine RPC

**Files:**
- Modify: `packages/optio-contracts/src/engine-failure-reasons.ts`:
  - `LaunchFailureReason` appends `'invalid-request'`, `'no-snapshot-support'`, `'snapshot-not-found'`, `'capture-pending'`, each with a one-line comment;
  - new `ListSnapshotsFailureReason = z.enum(['not-found', 'no-snapshot-support'])` and its type.
- Modify: `packages/optio-contracts/src/schemas/process.ts`: `ProcessSnapshotSchema = z.object({ capturedAt: z.string(), endState: z.string() })` and `type ProcessSnapshot`.
- Modify: `packages/optio-contracts/src/optio-engine-to-api.ts`:
  - `launch.params.resumeFrom: z.string().min(1).optional()`;
  - `listSnapshots: defineMethod({ params: z.object({ processId: ProcessIdParam }), result: discriminatedUnion('ok', [{ ok: true, snapshots: z.array(ProcessSnapshotSchema) }, { ok: false, reason: ListSnapshotsFailureReason }]) })`.
- Modify: `packages/optio-contracts/src/api-to-frontend.ts`:
  - launch body `resumeFrom: z.string().min(1).optional()`;
  - route `getSnapshots` (GET `/processes/:id/snapshots`, `pathParams` as `getConversation`, `query: InstanceQuerySchema`, `200: z.object({ snapshots: z.array(ProcessSnapshotSchema) })`, `404: { reason: ListSnapshotsFailureReason, message }`).
  - `route-error-reasons.ts` stays unchanged (F4).
- Modify: `packages/optio-contracts/src/index.ts`: export `ProcessSnapshotSchema`, `type ProcessSnapshot`, `ListSnapshotsFailureReason`.
- Regenerate: `packages/optio-api/src/_generated/*` and `packages/optio-core/src/optio_core/_generated/*`.
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`: `launch` passes `resume_from=params.resume_from`; new `list_snapshots(params: ListSnapshotsParams) -> ListSnapshotsResult`.
- Modify: `packages/optio-contracts/AGENTS.md` and `packages/optio-core/AGENTS.md` (engine RPC). In the root `AGENTS.md`: the "Contract Endpoints" table (the `getSnapshots` row, `resumeFrom` on launch) and "Engine RPC contract" (`listSnapshots`, `resumeFrom`).
- Test: `packages/optio-contracts/src/__tests__/optio-engine-contract.test.ts`; create `packages/optio-contracts/src/__tests__/snapshots-contract.test.ts`; `packages/optio-core/tests/test_engine_service.py`.

**Interfaces:**
- Consumes: Task 1 (`Optio.launch(resume_from=)`, `Optio.list_snapshots`, `ListSnapshotsOutcome`).
- Produces:
  - zod `ListSnapshotsFailureReason`, `ProcessSnapshotSchema`;
  - the frontend route `processes.getSnapshots`; the launch body's `resumeFrom`;
  - engine RPC `listSnapshots`; Python `ListSnapshotsParams` and `ListSnapshotsResult` in `optio_core._generated.optio_engine`; `LaunchParams.resume_from`;
  - `OptioEngineService.list_snapshots`;
  - in the generated TS engine client, `engine.listSnapshots({ processId })`.

- [ ] **Step 1: Write the failing tests.**
  - Engine contract:
    - `listSnapshots.params.parse({ processId: 'p1' })`;
    - `result.parse({ ok: true, snapshots: [{ capturedAt: '2026-11-19T13:05:12.345Z', endState: 'done' }] })`;
    - `result.parse({ ok: false, reason: 'no-snapshot-support' })`;
    - `result.safeParse({ ok: false, reason: 'bogus' }).success === false`;
    - `ListSnapshotsFailureReason.options` equals `['not-found', 'no-snapshot-support']`;
    - `launch.params` accepts `{ processId: 'p1', sessionId: null, resume: true, resumeFrom: TS }`;
    - `LaunchFailureReason.options` contains the four new reasons.
  - `snapshots-contract.test.ts`:
    - `processesContract.getSnapshots.method === 'GET'` and its path is `'/processes/:id/snapshots'`;
    - its 200 schema parses `{ snapshots: [{ capturedAt: TS, endState: 'cancelled' }] }`;
    - the launch body schema parses `{ resume: true, resumeFrom: TS }` and refuses `{ resumeFrom: '' }`.
  - Engine service:
    - `test_launch_forwards_resume_from`: `LaunchParams.model_validate({"processId": "p1", "sessionId": None, "resume": True, "resumeFrom": TS})` → `launch` is awaited with `resume_from=TS`;
    - the existing exact-call assertions (`assert_awaited_once_with(...)`) gain `resume_from=None`;
    - the parametrize of `test_launch_answers_every_reason_optio_core_returns` gains the four reasons;
    - `test_list_snapshots_ok`: `fake_optio.list_snapshots = AsyncMock(return_value=ListSnapshotsOutcome(ok=True, snapshots=[...]))` → `res.root.ok` and `res.root.snapshots[0].captured_at == TS`;
    - `test_list_snapshots_reason`, parametrized over both reasons.
- [ ] **Step 2: Run** `VITEST optio-contracts optio-engine-contract.test.ts` and `VITEST optio-contracts snapshots-contract.test.ts` (expected FAIL: `listSnapshots` undefined), then `PYTEST optio-core test_engine_service.py` (expected FAIL: ImportError `ListSnapshotsParams`).
- [ ] **Step 3: Implement** the contract changes, then run `BUILD_CONTRACTS`, then `CODEGEN`. Check with `git -C /home/csillag/db-writing-work/optio status --short` that the generated files changed. Then the engine service. If codegen also rewrites `packages/optio-agents-ui` without content changes, do not stage it.
- [ ] **Step 4: Run** `VITEST optio-contracts` with each of `optio-engine-contract.test.ts`, `snapshots-contract.test.ts`, `route-error-reasons.test.ts`, `process-schema.test.ts`; then `PYTEST optio-core test_engine_service.py` and `PYTEST optio-core test_resume_from_snapshot.py`. Expected: PASS.
- [ ] **Step 5: Commit** these paths:
  - the five contract sources and the new test under `packages/optio-contracts/src`;
  - the generated files under `packages/optio-api/src/_generated` and `packages/optio-core/src/optio_core/_generated` (each changed file by its path);
  - `_engine_service.py` and `test_engine_service.py`;
  - `packages/optio-contracts/AGENTS.md`, `packages/optio-core/AGENTS.md` and `AGENTS.md`.

  Message: `feat(optio-contracts,optio-core): resumeFrom on launch, the listSnapshots RPC and the GET /processes/:id/snapshots contract`.

---

### Task 6: optio-api: the snapshots route and `resumeFrom` on launch

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`:
  - new `getProcessSnapshots`;
  - `launchProcess` takes `resumeFrom` as a trailing optional parameter;
  - `LAUNCH_STATUS` and `MESSAGES` cover the four new reasons.
- Modify: `packages/optio-api/src/adapters/express.ts`, `fastify.ts`, `nextjs-app.ts`, `nextjs-pages.ts`: a `getSnapshots` handler next to `getConversation`, and `body?.resumeFrom` passed to `launchProcess`.
- Modify: `packages/optio-api/AGENTS.md` (handlers, routes, status mapping), `packages/optio-api/README.md` where it lists routes, and the root `AGENTS.md` ("TypeScript: optio-api", Handlers).
- Test: `packages/optio-api/src/__tests__/handlers-access.test.ts`, `packages/optio-api/src/__tests__/adapters-access.test.ts`.

**Interfaces:**
- Consumes: Task 5 (`engine.listSnapshots`, `ListSnapshotsFailureReason`, `ProcessSnapshot`, the new `LaunchFailureReason` values).
- Produces:
  - `getProcessSnapshots(ctx, query, id, access = UNRESTRICTED): Promise<SnapshotsResult>`, where `SnapshotsResult = { status: 200; body: { snapshots: ProcessSnapshot[] } } | { status: 404; body: { reason: ListSnapshotsFailureReasonType; message: string } }`.
  - `launchProcess(ctx, query, id, resume, sessionId, access, params, ephemeral, stamp, resumeFrom?: string)`.
- Values:
  - statuses `invalid-request` 400; `no-snapshot-support`, `snapshot-not-found`, `capture-pending` 409;
  - messages `'resumeFrom needs resume: true'`, `'This task keeps no snapshot history'`, `'No valid snapshot was taken at that time'`, `'An unfinished snapshot capture of this process must be resolved first (Resurrect or Resume)'`;
  - snapshot route messages `not-found` → `'Process not found'`, `no-snapshot-support` → `'This task keeps no snapshot history'`.

- [ ] **Step 1: Write the failing tests.**
  - In `handlers-access.test.ts`, `makeEngine()` gains `listSnapshots: vi.fn(() => ({ ok: true, snapshots: [SNAP] }))` with `SNAP = { capturedAt: '2026-11-19T13:05:12.345Z', endState: 'done' }`. `describe('getProcessSnapshots')`:
    - `'answers the engine's list for an in-scope process, asking it by _id, without authorize'`: `insertTree(1)`; `getProcessSnapshots(makeCtx(engine), { prefix: PREFIX }, 'root-1', scoped(1))` → `{ status: 200, body: { snapshots: [SNAP] } }`; the engine is called with `{ processId: rootId.toHexString() }`; `access.authorize` is not called.
    - `'outside the scope or hidden: 404 not-found, the engine not called'`: another customer's root `_id`, its `processId` and its child under `scoped(1)`, and a process hidden by `visible(...)`.
    - `'the engine's reasons answer 404 with the reason'`: `no-snapshot-support` and `not-found`.
    - `'unrestricted: a missing id is 404 not-found; the engine is asked by _id'`.
  - `describe('launchProcess: resumeFrom')`:
    - `'forwards resumeFrom to the engine'`: the engine's launch is called with `expect.objectContaining({ resume: true, resumeFrom: SNAP.capturedAt })`.
    - `'no resumeFrom key without it'`: `engine.launch.mock.calls[0][0]` has no `resumeFrom` property.
    - `'maps the new reasons'`: `invalid-request` → 400; the other three → 409; `body.reason` is passed through.
  - In `adapters-access.test.ts`, for all four adapters (its loop), with `listSnapshots` in its engine:
    - `'serves the snapshot list of an in-scope process; 404s another customer's'`: `GET /api/processes/${mineId}/snapshots?${q}` → 200 with `{ snapshots: [SNAP] }`; `otherId` → 404, and `engine.listSnapshots` is not called for it.
    - `'forwards resumeFrom on launch'`: POST launch with body `{ resume: true, resumeFrom: SNAP.capturedAt }` → the engine's launch is called with it.
- [ ] **Step 2: Run** `VITEST optio-api handlers-access.test.ts` and `VITEST optio-api adapters-access.test.ts`. Expected: FAIL (`getProcessSnapshots` not exported; route 404).
- [ ] **Step 3: Implement.** `getProcessSnapshots` uses `findReadableProcess(processReads(db, prefix, access.visibility), id, access)`, the lookup that gates `GET /processes/:id` (scope and visibility; no `authorize`). It then calls `engine.listSnapshots({ processId: doc._id.toString() })`. `launchProcess` adds `...(resumeFrom !== undefined ? { resumeFrom } : {})` to the engine call, as it does with `metadata`.
- [ ] **Step 4: Run** `VITEST optio-api handlers-access.test.ts`, `VITEST optio-api adapters-access.test.ts`, `VITEST optio-api handlers.test.ts`, `VITEST optio-api fastify-access.test.ts`, then `TSC optio-api`. Expected: PASS, no type errors.
- [ ] **Step 5: Commit** `handlers.ts`, the four adapters, the two tests, `packages/optio-api/AGENTS.md`, `packages/optio-api/README.md` (if changed) and `AGENTS.md`. Message: `feat(optio-api): GET /processes/:id/snapshots under the process's access scope; launch passes resumeFrom`.

---

### Task 7: optio-ui: `useProcessSnapshots`, launch options, error texts

**Files:**
- Create: `packages/optio-ui/src/hooks/useProcessSnapshots.ts`.
- Modify: `packages/optio-ui/src/hooks/useProcessActions.ts`: `LaunchOptions.resumeFrom?: string`; `launchBody` adds `resumeFrom` when it is set.
- Modify: `packages/optio-ui/src/error-routes.ts`: `launchReasons` gains `errors.processes.launch.invalid-request`, `.no-snapshot-support`, `.snapshot-not-found`, `.capture-pending`.
- Modify: `packages/optio-ui/src/index.ts`: export `useProcessSnapshots`.
- Modify: `packages/optio-ui/AGENTS.md` (Hooks: `useProcessSnapshots`, and `useProcessActions`' `LaunchOptions`; i18n keys: the four error keys) and the root `AGENTS.md` (optio-ui Hooks and i18n Keys).
- Test: create `packages/optio-ui/src/__tests__/useProcessSnapshots.test.tsx`; modify `packages/optio-ui/src/__tests__/useProcessActions.test.tsx`.

**Interfaces:**
- Consumes: Task 5 (`processes.getSnapshots`, `ProcessSnapshot`).
- Produces:
  - `useProcessSnapshots(processId: string | undefined, opts: { enabled: boolean; runEndedAt?: string | null }): ProcessSnapshot[] | undefined`. It returns undefined while disabled, loading or failed.
  - `api.processes.getSnapshots.useQuery` with `queryKey: ['process-snapshots', database, prefix, processId, runEndedAt ?? null]`, `queryData: { params: { id }, query: { database, prefix } }`, `enabled: !!processId && opts.enabled`, `staleTime: Infinity`, `retry: false`.
  - `LaunchOptions.resumeFrom`.

- [ ] **Step 1: Write the failing tests.**
  - `useProcessSnapshots.test.tsx` uses `useProcessConversation.test.tsx`'s pattern: a real `OptioProvider` with `useInstanceDiscovery` mocked, a stubbed `fetch` matching `/\/snapshots(\?|$)/`, and `waitFor` with `{ timeout: 60_000 }`.
    - `'returns the list on a 200'` → `[S1, S2]`.
    - `'a 404 answers undefined, no error thrown'`.
    - `'disabled, or without an id: no request'`: the fetch spy is never called with a snapshots URL.
    - `'a new runEndedAt reads the list again'`: rerendering with another `runEndedAt` fetches a second time.
  - `useProcessActions.test.tsx`, `'launch sends resumeFrom beside resume'`: the mutation body is `{ sessionId: <id>, resume: true, resumeFrom: TS }`.
- [ ] **Step 2: Run** `VITEST optio-ui useProcessSnapshots.test.tsx` and `VITEST optio-ui useProcessActions.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** both files, then `TSC optio-ui`. The `Record<LaunchFailureReason, ...>` in error-routes fails the typecheck until the four keys exist. Expected: PASS.
- [ ] **Step 5: Commit** the hook, `useProcessActions.ts`, `error-routes.ts`, `index.ts`, the two tests, `packages/optio-ui/AGENTS.md` and `AGENTS.md`. Message: `feat(optio-ui): useProcessSnapshots; launch options carry resumeFrom; error keys for the new launch refusals`.

---

### Task 8: optio-ui: `useProcessLaunchActions`, moved from excavator, with the previous-snapshot slots

**Files:**
- Create: `packages/optio-ui/src/hooks/useProcessLaunchActions.tsx`. Port excavator's `packages/frontend/src/shared/hooks/useProcessLaunchActions.tsx` (in `/home/csillag/db-writing-work/excavator`) with the same behaviour: matrix, ids, gate, resurrect confirmations. Add the four slots and F8.
- Create: `packages/optio-ui/src/snapshot-time.ts`: `formatSnapshotTimes`.
- Modify: `packages/optio-ui/src/index.ts`: export `useProcessLaunchActions` and `type LaunchOpts`.
- Modify: `packages/optio-ui/AGENTS.md` (Hooks: `useProcessLaunchActions`; i18n keys from the table below) and the root `AGENTS.md` (the same).
- Test: create `packages/optio-ui/src/__tests__/useProcessLaunchActions.test.tsx` (port excavator's `packages/frontend/src/shared/hooks/__tests__/useProcessLaunchActions.test.tsx`, its 8 cases with their expectations verbatim, plus the new cases); create `packages/optio-ui/src/__tests__/snapshot-time.test.ts`.

**Interfaces:**
- Consumes: Task 5 (`ProcessSnapshot`); `isLaunchable`, `isResumable`, `isResurrectable`, `isResurrecting` from `process-state.ts`; vultus-core `useAction`, `Decision`, `ActionStatus`, `ConfirmationSpec`, `useVultusComponents`, `useSpinnerKey`.
- Produces:
  - `type LaunchOpts = { resume?: boolean; resumeFrom?: string } | undefined`.
  - `useProcessLaunchActions(process: (ProcessStateLike & { _id: string }) | null | undefined, triggerLaunch: (opts?: LaunchOpts) => void, triggerResurrect?: () => void, launchGate: Decision = true, snapshots?: readonly ProcessSnapshot[]): ActionStatus[]`. The result is always 8 entries: resurrect, resume, slot 0 to 3, restart, launch.
  - `formatSnapshotTimes(capturedAts: readonly string[], locale?: string, timeZone?: string): string[]`. It uses `Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone })`, and `timeStyle: 'medium'` for every entry whose short form equals another entry's.
- Slots:
  - `older = snapshots?.slice(1, 5) ?? []`. Slot `s` shows `older[s]`, has `s + 1` newer snapshots, and its id is `process.resumeFrom:${id}:${s}`.
  - Visible when `older[s]` exists, `!resurrecting`, and `launchable && resumable`. That covers the resurrect case too, where the slots sit after "Resume from last snapshot".
  - `variant: 'default'`, icon `<HistoryOutlined />`, `enabled: launchGate`.
  - `fire: () => triggerLaunch({ resume: true, resumeFrom: older[s].capturedAt })`.
  - Confirmation: `{ kind: 'cascade-modal', title: <confirm text>, content: <the deliveries sentence; in the resurrect case followed by the discardUnsavedWork sentence> }`.
- While resurrecting, the Resurrect action's icon is the lent `Spinner` (`spinnerKey` from `useSpinnerKey(\`optio:process:${id}:resurrecting\`)`, `size="small"`), else `<LoadingOutlined spin />`. Otherwise it is `<MedicineBoxOutlined />`.
- Texts: `t(key, { ...values, defaultValue })`. Interpolation passes `interpolation: { escapeValue: false }`. `<time>` comes from `formatSnapshotTimes(older.map(c), i18n.resolvedLanguage ?? i18n.language)`.

| Key | Default |
|---|---|
| `processes.resurrect` | `Resurrect` |
| `processes.resurrecting` | `Resurrecting…` |
| `processes.resurrectingReason` | `Saving the work the failed run left behind; the process resumes afterwards.` |
| `processes.resume` | `Resume` |
| `processes.resumeFromSnapshot` | `Resume from last snapshot` |
| `processes.restart` | `Restart (discard saved state)` |
| `processes.launch` | `Launch` |
| `processes.discardUnsavedWork` | `This discards the unsaved work the failed run left behind.` |
| `processes.resumeFromPrevious` | `Resume from previous snapshot ({{time}})` |
| `processes.resumeFromPreviousConfirm` (plural via `count`; `defaultValue_one` / `defaultValue_other`) | `Resume from {{time}}? This discards {{count}} newer snapshot.` / `Resume from {{time}}? This discards {{count}} newer snapshots.` |
| `processes.resumeFromPreviousKeepsDeliveries` | `What the discarded runs delivered stays in the app.` |

- [ ] **Step 1: Write the failing tests.** `renderHook` takes a wrapper of `I18nextProvider` with an `en` instance whose resources are empty, so the labels are the defaults.

  Fixtures:
  - `RESUMABLE = { _id: 'p1', status: { state: 'cancelled' }, supportsResume: true, hasSavedState: true }`;
  - `SNAPS`: six entries at 13:05, 12:05, 11:05, 10:05, 09:05 and 08:05 on 2026-11-19Z, each `endState: 'cancelled'`;
  - `time(i) = formatSnapshotTimes(SNAPS.slice(1, 5).map((s) => s.capturedAt), 'en')[i]`.

  New cases:
  - `'offers one item per older snapshot, newest first, at most four'`: visible labels equal `['Resume', ...[0, 1, 2, 3].map((i) => \`Resume from previous snapshot (${time(i)})\`), 'Restart (discard saved state)']`; `result.current.length === 8`.
  - `'no items without older snapshots'`: for `undefined`, `[]` and `[SNAPS[0]]`, the labels are `['Resume', 'Restart (discard saved state)']`.
  - `'an item resumes from its snapshot after a confirmation naming the newer ones'`:
    - slot 1 has `confirmation.kind === 'cascade-modal'` and `confirmation.title === \`Resume from ${time(1)}? This discards 2 newer snapshots.\``;
    - the rendered `content`'s `textContent` contains `'What the discarded runs delivered stays in the app.'` and not `'unsaved work'`;
    - `fire()` → the launch mock is called with `{ resume: true, resumeFrom: SNAPS[2].capturedAt }`;
    - slot 0's title ends `'This discards 1 newer snapshot.'`.
  - `'resurrect case: items between Resume from last snapshot and Restart, confirmation names the unsaved work'`, with excavator's `FAILED_WITH_UNSAVED_WORK` and `SNAPS`:
    - labels `['Resurrect', 'Resume from last snapshot', <4 items>, 'Restart (discard saved state)']`;
    - each item's content text contains both the deliveries sentence and `'This discards the unsaved work the failed run left behind.'`.
  - `'resurrecting: Resurrecting only'`, with `SNAPS`: labels `['Resurrecting…']`.
  - `'the gate holds the items back with its reason'`, with `deny('No free seed')`: every visible action, the items included, is disabled with reason `'No free seed'`.
  - `'no items for a process that is not launchable or not resumable'`: running → none visible; `hasSavedState: false` → `['Launch']`.
  - `'a fixed number of statuses and hooks'`:
    - `renderHook((p) => useProcessLaunchActions(p.process, launch, undefined, true, p.snapshots), { initialProps: { process: null, snapshots: undefined } })`;
    - rerender with `RESUMABLE` and `SNAPS`, then with `[]`;
    - no error, and `length === 8` each time;
    - the slot ids are `process.resumeFrom:p1:0` to `:3`.
  - `'items whose minutes collide show seconds'`: two older snapshots at 12:05:10Z and 12:05:40Z get different labels.
  - `snapshot-time.test.ts`:
    - `formatSnapshotTimes(['2026-11-19T13:05:12.345Z', '2026-11-19T12:00:00.000Z'], 'en-GB', 'UTC')` equals each `new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(new Date(iso))`;
    - with `'…T13:05:12.345Z'`, `'…T13:05:40.000Z'` and `'…T12:00:00.000Z'`, the first two contain `13:05:12` and `13:05:40` and the third equals its short form;
    - `locale` left undefined does not throw.
- [ ] **Step 2: Run** `VITEST optio-ui useProcessLaunchActions.test.tsx` and `VITEST optio-ui snapshot-time.test.ts`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** both files, then `TSC optio-ui`. Expected: PASS.
- [ ] **Step 5: Commit** the hook, `snapshot-time.ts`, `index.ts`, the two tests, `packages/optio-ui/AGENTS.md` and `AGENTS.md`. Message: `feat(optio-ui): useProcessLaunchActions, the launch matrix moved from excavator, with resume-from-previous-snapshot items`.

---

### Task 9: optio-ui: LaunchControls renders the hook

**Owner's answer (2026-11-19), supersedes question 3 above:** keep the icon-only look. vultus-antd's
`CombinedActionButton` already has an `iconOnly` prop (vultus-antd 0.8.3: README "CombinedActionButton
selection"; `CombinedActionButton.tsx`): the main half shows only the active action's icon, its label becomes
the accessible name and the tooltip (with the reason below while disabled); the opener and the menu rows keep
their labels. So `LaunchControls` passes `iconOnly` wherever it is icon-only today (list rows, tree nodes,
the detail view's row), keeps a distinct icon per action, and keeps the green look where it has it today.
optio resolves vultus-antd 0.7.3, which lacks it: raise optio-ui's vultus-antd peer range to `>=0.8.3` and its
dev dependency to `^0.8.3` (check vultus-core's matching floor the same way). That changes optio's lockfile:
find how this repo maintains `pnpm-lock.yaml` (AGENTS.md, release cookbook) and report before committing it.

**Files:**
- Modify: `packages/optio-ui/src/components/LaunchControls.tsx`.
- Modify: `packages/optio-ui/src/components/ProcessItem.tsx`, `ProcessList.tsx`, `ProcessTreeView.tsx`, `ProcessFilter.tsx`: the `onLaunch` prop types take `opts?: LaunchOpts`.
- Modify: `packages/optio-ui/AGENTS.md`: the LaunchControls section (rendering, props, `denyReason` semantics, the warning, snapshots); the Spinner slot row (now the hook's icon, same key and size); the i18n keys (drop the rows for `processes.moreOptions`, `processes.resurrectHint`, `processes.restartDiscarding`, `processes.discardAndContinue`, `processes.resurrectingLabel`, which are no longer used). Mirror this in the root `AGENTS.md` (optio-ui Components and i18n Keys).
- Test: `packages/optio-ui/src/__tests__/LaunchControls.test.tsx`, `spinnerSlot.test.tsx`, `ProcessDetailView.test.tsx`, `ProcessItem.test.tsx`. Each one mocks `../hooks/useProcessSnapshots.js` with a `vi.fn()` that returns undefined by default; LaunchControls calls it before its early returns.

**Interfaces:**
- Consumes: Tasks 7 and 8 (`useProcessSnapshots`, `useProcessLaunchActions`, `LaunchOpts`, `runEndedAt`); vultus-antd `CombinedActionButton`; vultus-core `deny`.
- Produces: `LaunchControlsProps.onLaunch?: (processId: string, opts?: LaunchOpts) => void`. Every other prop is unchanged.

The component, in order. All hooks run unconditionally, before any return.
1. Hooks:
   - `Modal.useModal()`;
   - `snapshots = useProcessSnapshots(process?._id, { enabled: !!onLaunch && isLaunchable(process) && isResumable(process) && !isTaskFactory(process), runEndedAt: runEndedAt(process) })`;
   - `actions = useProcessLaunchActions(process, triggerLaunch, triggerResurrect, denyReason ? deny(denyReason) : true, snapshots)`.
   - `triggerLaunch(opts)`: when `opts === undefined && process.warning`, ask with `modal.confirm({ title: process.warning, onOk: () => onLaunch(process._id, undefined) })`; else `onLaunch(process._id, opts)`.
   - `triggerResurrect`: `onResurrect ? () => onResurrect(process._id) : (isResurrecting(process) ? () => {} : undefined)` (F10).
2. `!onLaunch` → null.
3. The kept-launch branch as today.
4. `!isLaunchable(process)` → null.
5. A task factory: `denyReason ? <DeniedLaunchButton> : <FactoryPlayButton>`, as today.
6. Otherwise `<>{contextHolder}<CombinedActionButton actions={styled} size={size} /></>`. With `iconFontSize`, `styled` maps each action to `{ ...a, icon: <span style={{ fontSize: iconFontSize }}>{a.icon}</span> }`; without it, `styled = actions`.

Remove `ResurrectControls` and the inline Case 1, Case 2 and resurrecting blocks.

- [ ] **Step 1: Write and adapt the tests in `LaunchControls.test.tsx`.**
  - Unchanged: the single play button, split primary, dropdown Restart, missing flags, `denyReason` single, empty `denyReason`, `primary button resurrects`, the `falls back to the resume split button` cases, and the factory, remove and kept-launch blocks.
  - `'renders disabled button when denyReason is set (split-button branch suppressed)'` becomes `'denyReason disables every action with its reason'`: the main button is disabled; after opening the menu, the Restart menuitem has `aria-disabled="true"`; `onLaunch` is never called.
  - The resurrect confirmation tests:
    - the menu texts are `/resume from last snapshot/i` and `/restart \(discard saved state\)/i`;
    - the OK button is `within(await screen.findByRole('dialog', {}, { timeout: 60_000 })).getByRole('button', { name: <the action's label> })`;
    - the body is `/discards the unsaved work/i`;
    - `'no resume item without saved state'` uses the new Restart text.
  - `'while resurrecting renders a single disabled Resurrecting indicator'`: one button, named `/resurrecting/i`, disabled; its tooltip text is `'Saving the work the failed run left behind; the process resumes afterwards.'`.
  - The i18n case uses the key `processes.resurrecting` = `'Feltámasztás…'`, and the button is named `/Feltámasztás…/`.
  - New `'previous-snapshot items: read the list for a launchable resumable process and offer its older snapshots'`:
    - `mockUseProcessSnapshots` returns 3 snapshots;
    - the menu shows 2 items labelled `Resume from previous snapshot (…)`;
    - clicking the first opens a dialog whose title ends `This discards 1 newer snapshot.`;
    - its OK → `onLaunch('3', { resume: true, resumeFrom: <2nd capturedAt> })`;
    - `mockUseProcessSnapshots` was called with `('3', { enabled: true, runEndedAt: null })`.
  - New `'no list read for a process that is not resumable, not launchable, a factory, or without onLaunch'`: `enabled: false` in each case.
  - New `'a task's warning is asked before a fresh launch, not before a resume'`: a non-resumable process with `warning: 'Careful'` → no launch on click; the dialog shows `'Careful'`; its OK → `onLaunch('x', undefined)`. A resumable process with a warning resumes without a dialog.
  - New `'iconFontSize sizes each action's icon'`: with 20, the icon's wrapper has `style.fontSize === '20px'`.
  - `spinnerSlot.test.tsx`: `{ name: 'Resurrecting' }` becomes `{ name: /resurrecting/i }` (two places); add the mock.
  - `ProcessDetailView.test.tsx`:
    - `{ name: 'Resume' }` becomes `{ name: /resume$/i }` (the name now includes the icon's label);
    - the `labels()` helper reads each button's `textContent` instead of `aria-label`;
    - `/discard and continue/i` becomes the dialog's button named by the action's label;
    - add the mock.
  - `ProcessItem.test.tsx`: add the mock only.
- [ ] **Step 2: Run** `VITEST optio-ui LaunchControls.test.tsx`. Expected: FAIL (the new cases).
- [ ] **Step 3: Implement** the component as above, and widen the prop types.
- [ ] **Step 4: Run**, one at a time: `VITEST optio-ui` with `LaunchControls.test.tsx`, `spinnerSlot.test.tsx`, `ProcessDetailView.test.tsx`, `ProcessItem.test.tsx`, `ProcessTreeView-stop.test.tsx`, `ProcessFilter.test.tsx`, `useProcessLaunchActions.test.tsx`; then `TSC optio-ui`. Expected: PASS.
- [ ] **Step 5: Commit** `LaunchControls.tsx`, the four widened components, the four tests, `packages/optio-ui/AGENTS.md` and `AGENTS.md`. Message: `feat(optio-ui): LaunchControls renders useProcessLaunchActions, with the resume-from-previous-snapshot items`.

---

### Task 10: docs outside the packages

**Files:**
- Modify: `docs/optio-capabilities.md`: one bullet in the "Changed since this snapshot" note, naming what is new:
  - the engine RPC `listSnapshots`;
  - launch `resumeFrom` with the four reasons;
  - `GET /api/processes/:id/snapshots`;
  - the `TaskInstance` hooks `list_snapshots` and `discard_snapshots_after`;
  - `optio_agents.snapshots`;
  - optio-ui's `useProcessLaunchActions` and `useProcessSnapshots`.
- Modify: `docs/writing-agent-wrappers.md`, "Stage 2 — Resume / snapshots": a new wrapper declares `SNAPSHOT_LAYOUT` in its `snapshots.py` and wires `snapshot_hooks(...)` into its task when `supports_resume`, passing a `capture_pending` check if it keeps a pending-capture store.
- Check: `AGENTS.md` (root) against the package `AGENTS.md` files that Tasks 1–9 changed, and fix any drift found.

- [ ] **Step 1:** Write the two additions.
- [ ] **Step 2:** Run `grep -rn resumeFrom /home/csillag/db-writing-work/optio/AGENTS.md` and `grep -rn list_snapshots /home/csillag/db-writing-work/optio/AGENTS.md`. Expected: the launch, RPC, route and TaskInstance lines from Tasks 1, 5 and 6.
- [ ] **Step 3: Commit** `docs/optio-capabilities.md`, `docs/writing-agent-wrappers.md` and `AGENTS.md` (only if changed). Message: `docs: resume from a previous snapshot in the capabilities note and the wrapper guide`.

---

### Task 11 (later, separate, not executed by this plan): excavator follow-up

This runs after Tasks 1–10 are merged to optio `main` (owner clearance) and released per `docs/release-cookbook.md`, in dependency order:
- PyPI: optio-core, optio-agents, the seven wrappers (and optio-agents-all if its pins need it);
- npm: optio-contracts, optio-api, optio-ui.

Allow for the npm and PyPI propagation delay. Then in `/home/csillag/db-writing-work/excavator`, from `main`, under excavator's own rules (no pnpm-lock commits there, its deploy rules):
1. Bump the optio dependencies of the frontend (optio-contracts, optio-ui), the API (optio-api, optio-contracts) and the engine (optio-core, optio-agents, the wrappers it uses).
2. `packages/frontend/src/features/processes/ProcessDetailPage.tsx`:
   - import `useProcessLaunchActions`, `type LaunchOpts`, `useProcessSnapshots`, `runEndedAt`, `isLaunchable` and `isResumable` from optio-ui;
   - before the early returns, `const snapshots = useProcessSnapshots(rootProcess?._id, { enabled: !!rootProcess && isLaunchable(rootProcess) && isResumable(rootProcess), runEndedAt: runEndedAt(rootProcess) })`;
   - call `useProcessLaunchActions(rootProcess, triggerLaunch, triggerResurrect, true, snapshots)`.
3. `packages/frontend/src/shared/hooks/useChatActions.tsx`: import the hook from optio-ui. Chats get no previous-snapshot items (no `snapshots` passed). Its `process.restart:` filter keeps working because the ids are unchanged.
4. Delete `packages/frontend/src/shared/hooks/useProcessLaunchActions.tsx` and `packages/frontend/src/shared/hooks/__tests__/useProcessLaunchActions.test.tsx`. Point the mocks in `packages/frontend/src/features/processes/__tests__/ProcessDetailPage.test.tsx` at optio-ui.
5. Add texts in `en.json`, `de.json` and `hu.json`:
   - `processes.resumeFromPrevious`, `processes.resumeFromPreviousConfirm_one` / `_other`, `processes.resumeFromPreviousKeepsDeliveries`;
   - `errors.processes.launch.invalid-request`, `.no-snapshot-support`, `.snapshot-not-found`, `.capture-pending`, with the matching entries in `packages/frontend/src/shared/api/error-routes.ts` (its `lint:error-coverage` reads `apiToFrontendRouteErrorReasons`, which now carries the four reasons).
6. Checks: the touched frontend test files, the frontend typecheck, `lint-i18n-keys`, `lint-error-coverage`.
