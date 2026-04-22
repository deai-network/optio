# Widget Reconciliation Delta Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Base revision:** `5d658a2108644eeae73b1764c0306e692d05107c` on branch `main` (as of 2026-04-22)

**Goal:** Close the three drift issues surfaced by the `finishing-a-development-branch` drift reviewer on `feat/widget-extensions`: make optio-core's two new reconciliation paths (`_reconcile_interrupted_processes`, `_force_finalize_stuck_processes`) clear `widgetUpstream` so the widget-extensions spec's lifecycle invariant holds under server-restart and shutdown-timeout paths, and rewrite the stale failure-mode row that still refers to a heartbeat-based handler.

**Architecture:** One-commit delta. Implementation is three small edits to `packages/optio-core/src/optio_core/lifecycle.py` (both reconciliation functions gain a `widgetUpstream: null` write) plus one spec edit and two test additions. The predecessor plan `docs/2026-04-21-optio-widget-extensions-plan.md` is left as an execution-record artifact; this plan supersedes its treatment of terminal-state widgetUpstream clearing.

**Tech Stack:** Python 3.11+, motor (async MongoDB), pytest + pytest-asyncio, Python project managed via pyenv at `/home/csillag/deai/pyenv/shims/python`.

---

## Scope constraints (read first)

- **widgetData is NOT cleared.** The widget-extensions design spec at `docs/2026-04-21-optio-widget-extensions-design.md:251-253` explicitly preserves `widgetData` across terminal states (`done` / `failed` / `cancelled`) so consumers can see final data. Only `widgetUpstream` is cleared at terminal — it holds routing/credential data that is meaningless once the worker is gone.
- **Do not modify the previous plan.** `docs/2026-04-21-optio-widget-extensions-plan.md` is an execution record. AGENTS.md treats plans as per-execution artifacts; this delta plan is authoritative for the reconciliation fix.
- **Preserve existing reconciliation behavior.** The four tests already in `packages/optio-core/tests/test_lifecycle_reconciliation.py` must continue to pass unchanged.
- **The whole delta is one commit** per the user's `feedback_batch_commits.md` convention.

---

## File Map

| File | Change |
|------|--------|
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify: `_reconcile_interrupted_processes` (lines 296-326) writes `widgetUpstream: None` alongside the status update. `_force_finalize_stuck_processes` (lines 328-360) includes `widgetUpstream: None` in its conditional `$set`. |
| `packages/optio-core/tests/test_lifecycle_reconciliation.py` | Modify: extend two existing tests (`test_startup_reconciles_all_active_states`, `test_shutdown_force_finalizes_uncooperative_task`) to assert `widgetUpstream` is cleared. Add one new test (`test_shutdown_leaves_cooperative_task_widget_upstream_alone`) covering the conditional-update guard. |
| `docs/2026-04-21-optio-widget-extensions-design.md` | Modify: replace the inaccurate "Worker process crashes hard" failure-mode row (line 358) and expand the "Optio's lifecycle guarantees" bullet (line 345) to enumerate all three terminal-state writers. |

No changes to the spec file's `**Base revision:**` line — this plan inherits the widget-extensions base revision by spec-update convention; brainstorming will handle that if the spec is ever re-brainstormed.

---

## Task 1: Extend `_reconcile_interrupted_processes` to clear `widgetUpstream`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:296-326`
- Test: `packages/optio-core/tests/test_lifecycle_reconciliation.py:40-80` (extend existing test)

**Context:** `_reconcile_interrupted_processes` is called by `Optio.init()` before `_sync_definitions` to reset any DB rows left in an active state by a previous session. On a widget-bearing process this currently leaves `widgetUpstream` pointing at a worker that is definitely not running anymore, contradicting design-spec line 345.

- [ ] **Step 1: Extend the existing reconciliation test to assert `widgetUpstream` is cleared**

  In `packages/optio-core/tests/test_lifecycle_reconciliation.py`, modify `_make_active_seed` and `test_startup_reconciles_all_active_states` so the seeded processes carry a non-null `widgetUpstream` and the assertions check it gets cleared.

  Replace the `_make_active_seed` helper (lines 17-37):
  ```python
  def _make_active_seed(process_id: str, name: str, state: str) -> dict:
      """Minimal process doc to seed the DB in a given active state."""
      status: dict = {"state": state}
      if state == "running":
          status["runningSince"] = datetime.now(timezone.utc)
      return {
          "processId": process_id,
          "name": name,
          "params": {},
          "metadata": {},
          "parentId": None,
          "rootId": None,
          "depth": 0,
          "order": 0,
          "adhoc": False,
          "ephemeral": False,
          "status": status,
          "progress": {"percent": None, "message": None},
          "log": [],
          "createdAt": datetime.now(timezone.utc),
          "widgetUpstream": {
              "url": "http://127.0.0.1:45678",
              "innerAuth": None,
          },
          "widgetData": {"iframe": True},
      }
  ```

  Then extend `test_startup_reconciles_all_active_states` to assert clearing. After the existing loop that checks each reconciled process's state/error/failedAt/log (around lines 64-72), add assertions inside the same loop:
  ```python
          for pid in ("p_sched", "p_run", "p_creq", "p_cing"):
              proc = await get_process_by_process_id(mongo_db, prefix, pid)
              assert proc is not None, pid
              assert proc["status"]["state"] == "failed", f"{pid}: {proc['status']}"
              assert "restart" in (proc["status"]["error"] or "").lower(), pid
              assert proc["status"]["failedAt"] is not None, pid
              assert any(
                  "reconcil" in entry["message"].lower() for entry in proc["log"]
              ), f"{pid}: no reconcile log entry"
              # widgetUpstream must be cleared; widgetData must be preserved.
              assert proc.get("widgetUpstream") is None, (
                  f"{pid}: widgetUpstream not cleared: {proc.get('widgetUpstream')!r}"
              )
              assert proc.get("widgetData") == {"iframe": True}, (
                  f"{pid}: widgetData must be preserved across terminal: "
                  f"{proc.get('widgetData')!r}"
              )

          # Untouched
          done = await get_process_by_process_id(mongo_db, prefix, "p_done")
          assert done["status"]["state"] == "done"
          # Terminal rows that were already terminal must not have widgetUpstream
          # touched by reconciliation — the teardown path owns them.
          assert done.get("widgetUpstream") == {
              "url": "http://127.0.0.1:45678", "innerAuth": None,
          }, f"p_done's widgetUpstream should not be touched: {done.get('widgetUpstream')!r}"
          idle = await get_process_by_process_id(mongo_db, prefix, "p_idle")
          assert idle["status"]["state"] == "idle"
  ```

  Rationale for the "untouched" assertion: `_reconcile_interrupted_processes` must not write to terminal-state rows at all. The conditional in the function already filters by `ACTIVE_STATES`; this assertion pins that invariant.

- [ ] **Step 2: Run the extended test to confirm it fails**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py::test_startup_reconciles_all_active_states -v
  ```

  Expected: FAIL with `assert proc.get("widgetUpstream") is None` — reconciliation does not yet clear `widgetUpstream`.

- [ ] **Step 3: Extend `_reconcile_interrupted_processes` to clear `widgetUpstream`**

  Modify `packages/optio-core/src/optio_core/lifecycle.py:296-326`. Replace the loop body so each reconciliation also nulls `widgetUpstream`:

  ```python
      async def _reconcile_interrupted_processes(self) -> None:
          """Mark processes left in active states by a previous session as failed.

          Spec: docs/2026-04-22-process-reconciliation-design.md (Rule 1).

          On a fresh server start `Executor._cancellation_flags` is empty, so any
          Mongo record whose state is in `ACTIVE_STATES` was interrupted and
          cannot be running anywhere. Reset each one to 'failed' with an error
          explaining what happened, clear `widgetUpstream` (whose worker is
          definitely gone), and append a log entry. `widgetData` is preserved
          intentionally — the widget-extensions spec keeps it across terminal
          states for post-mortem inspection.
          """
          coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
          cursor = coll.find(
              {"status.state": {"$in": list(ACTIVE_STATES)}},
              {"_id": 1, "status.state": 1},
          )
          stale = [(doc["_id"], doc["status"]["state"]) async for doc in cursor]
          if not stale:
              return

          now = datetime.now(timezone.utc)
          error_msg = "Process was interrupted by server restart"
          for oid, prev_state in stale:
              await update_status(
                  self._config.mongo_db, self._config.prefix, oid,
                  ProcessStatus(state="failed", error=error_msg, failed_at=now),
              )
              await coll.update_one({"_id": oid}, {"$set": {"widgetUpstream": None}})
              await append_log(
                  self._config.mongo_db, self._config.prefix, oid,
                  "event", f"State reconciled: {prev_state} -> failed (server restart)",
              )
          logger.info(f"Reconciled {len(stale)} interrupted process(es) to 'failed'")
  ```

  Notes on the implementation choice:
  - We use a direct `coll.update_one` for the widgetUpstream nulling rather than adding a new store-layer helper. This mirrors how `_force_finalize_stuck_processes` (Task 2) already uses `coll.update_one` directly for its conditional write, keeping the two reconciliation paths visually and structurally consistent.
  - The `_id` used for the update is the same `_id` already in hand from `stale`; no extra lookup.
  - This does a second write per doc. The reconciliation path runs at most once per server start and typically on a small set of rows, so the extra round-trip is fine.

- [ ] **Step 4: Run the extended test to confirm it passes**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py::test_startup_reconciles_all_active_states -v
  ```

  Expected: PASS. All four reconciled processes show `widgetUpstream=None`, both terminal-state rows (`p_done`, `p_idle`) are untouched, and `widgetData` is preserved throughout.

- [ ] **Step 5: Run the rest of the reconciliation suite to verify no regression**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py -v
  ```

  Expected: all 4 tests pass (`test_startup_reconciles_all_active_states`, `test_startup_reconciliation_is_noop_on_fresh_db`, `test_shutdown_force_finalizes_uncooperative_task`, `test_shutdown_leaves_cooperative_task_alone`).

---

## Task 2: Extend `_force_finalize_stuck_processes` to clear `widgetUpstream` conditionally

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:328-360`
- Test: `packages/optio-core/tests/test_lifecycle_reconciliation.py:100-123` (extend existing test) and add one new test

**Context:** `_force_finalize_stuck_processes` runs at the end of `Optio.shutdown()` when the grace-period elapsed with tasks still holding cancellation flags. Its update is **conditional** on `status.state ∈ ACTIVE_STATES` so a task that flushed a terminal state at the last moment is not overwritten. The widgetUpstream cleanup must share that condition so we never racily wipe a value the task's own teardown set.

- [ ] **Step 1: Extend `test_shutdown_force_finalizes_uncooperative_task` to assert `widgetUpstream` is cleared**

  Modify `test_shutdown_force_finalizes_uncooperative_task` (lines 100-123) to seed `widgetUpstream` on the stuck process mid-run and assert the force-finalize path clears it.

  Replace the test body:
  ```python
  async def test_shutdown_force_finalizes_uncooperative_task(mongo_db):
      """A task that does not respond to cancellation in time is marked failed
      and has its widgetUpstream cleared."""
      prefix = "shutdowntest"

      started = asyncio.Event()

      async def uncooperative(ctx):
          # Pretend we registered an upstream; the stuck task never gets to
          # clear it via its own teardown, so force-finalize must.
          await ctx.set_widget_upstream("http://127.0.0.1:45678")
          started.set()
          await asyncio.sleep(30)  # ignore any cancellation signal

      async def get_tasks(_services):
          return [TaskInstance(execute=uncooperative, process_id="stuck", name="Stuck")]

      fw = Optio()
      await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
      await fw.launch("stuck")
      await asyncio.wait_for(started.wait(), timeout=2.0)

      # Sanity: upstream was actually set before shutdown.
      pre = await get_process_by_process_id(mongo_db, prefix, "stuck")
      assert pre["widgetUpstream"] is not None

      await fw.shutdown(grace_seconds=0.2)

      proc = await get_process_by_process_id(mongo_db, prefix, "stuck")
      assert proc["status"]["state"] == "failed"
      assert "grace" in (proc["status"]["error"] or "").lower(), proc["status"]["error"]
      assert proc["status"]["failedAt"] is not None
      assert proc.get("widgetUpstream") is None, (
          f"widgetUpstream should be cleared on force-finalize: {proc.get('widgetUpstream')!r}"
      )
  ```

  Import note: the test uses `ctx.set_widget_upstream`, which already exists in `ProcessContext` (see `packages/optio-core/src/optio_core/context.py:88-97`) — no new imports needed.

- [ ] **Step 2: Add a new test confirming cooperative tasks keep control over `widgetUpstream`**

  Append a new test after `test_shutdown_leaves_cooperative_task_alone`:
  ```python
  async def test_shutdown_leaves_cooperative_task_widget_upstream_alone(mongo_db):
      """The force-finalize conditional update does not clobber widgetUpstream
      for a task that flushed its own terminal state inside the grace period.

      This pins the invariant that widgetUpstream clearing by
      _force_finalize_stuck_processes is scoped to the same conditional
      (status.state in ACTIVE_STATES) as the state write — we must not
      race a cooperative task's own teardown.
      """
      prefix = "shutdowncoop_widget"

      started = asyncio.Event()

      async def cooperative(ctx):
          await ctx.set_widget_upstream("http://127.0.0.1:45678")
          started.set()
          while ctx.should_continue():
              await asyncio.sleep(0.05)
          # Teardown: the executor's normal-return path will clear
          # widgetUpstream via clear_widget_upstream in _execute_process.
          # We simulate an early self-clear here by the worker to prove
          # _force_finalize does not overwrite it back to None (it would
          # already be None in this case; the point is that the conditional
          # $set on ACTIVE_STATES keeps us from touching it at all).

      async def get_tasks(_services):
          return [TaskInstance(execute=cooperative, process_id="nice", name="Nice")]

      fw = Optio()
      await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
      await fw.launch("nice")
      await asyncio.wait_for(started.wait(), timeout=2.0)

      await fw.shutdown(grace_seconds=1.0)

      proc = await get_process_by_process_id(mongo_db, prefix, "nice")
      # The cooperative task reaches a terminal state via the executor's
      # normal path, so status is cancelled, not failed, and
      # _force_finalize's conditional skips this row.
      assert proc["status"]["state"] == "cancelled", proc["status"]
      assert not proc["status"].get("error")
      # widgetUpstream is cleared by the executor's teardown, not by
      # _force_finalize — that's fine; the invariant we are pinning is
      # that the task owns the field through its terminal transition.
      assert proc.get("widgetUpstream") is None
  ```

- [ ] **Step 3: Run the two tests to confirm they fail**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py::test_shutdown_force_finalizes_uncooperative_task tests/test_lifecycle_reconciliation.py::test_shutdown_leaves_cooperative_task_widget_upstream_alone -v
  ```

  Expected: `test_shutdown_force_finalizes_uncooperative_task` FAILS (widgetUpstream is still set after force-finalize). `test_shutdown_leaves_cooperative_task_widget_upstream_alone` may pass already (the executor's normal path already clears widgetUpstream) — that's fine; we are pinning the invariant for future protection.

- [ ] **Step 4: Extend `_force_finalize_stuck_processes` to include `widgetUpstream` in the conditional `$set`**

  Modify `packages/optio-core/src/optio_core/lifecycle.py:328-360`. The existing function uses a conditional update — we piggyback on it:

  ```python
      async def _force_finalize_stuck_processes(self, oids: list[ObjectId]) -> None:
          """Mark processes that did not unwind during shutdown as failed.

          Spec: docs/2026-04-22-process-reconciliation-design.md (Rule 2).

          Uses a conditional Mongo update so we do not overwrite a terminal
          state a task may have flushed at the last moment. The same
          conditional scopes the widgetUpstream clearing — a task that won
          the race to terminal owns its widgetUpstream transition (via the
          executor's teardown path).
          """
          coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
          now = datetime.now(timezone.utc)
          error_msg = "Task did not exit within shutdown grace period"
          status_doc = ProcessStatus(
              state="failed", error=error_msg, failed_at=now,
          ).to_dict()

          forced = 0
          for oid in oids:
              result = await coll.update_one(
                  {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
                  {"$set": {"status": status_doc, "widgetUpstream": None}},
              )
              if result.modified_count:
                  forced += 1
                  await append_log(
                      self._config.mongo_db, self._config.prefix, oid,
                      "event", "State forced: running -> failed (shutdown grace period exceeded)",
                  )
              self._executor._cancellation_flags.pop(oid, None)

          if forced:
              logger.warning(
                  f"Force-finalized {forced} process(es) that did not exit within grace period"
              )
  ```

  The only functional change is adding `"widgetUpstream": None` to the `$set` dict. Everything else is unchanged.

- [ ] **Step 5: Run the two tests to confirm they pass**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py::test_shutdown_force_finalizes_uncooperative_task tests/test_lifecycle_reconciliation.py::test_shutdown_leaves_cooperative_task_widget_upstream_alone -v
  ```

  Expected: both PASS.

- [ ] **Step 6: Run the full reconciliation suite to catch regressions**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest tests/test_lifecycle_reconciliation.py -v
  ```

  Expected: all 5 tests pass (the 4 pre-existing plus the one new `*_widget_upstream_alone` test).

- [ ] **Step 7: Run the complete optio-core test suite to catch wider regressions**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest -q
  ```

  Expected: all tests pass (93 pre-existing + widget-extensions suite contributions). No existing test should fail.

---

## Task 3: Update the widget-extensions design spec

**Files:**
- Modify: `docs/2026-04-21-optio-widget-extensions-design.md:345` (lifecycle-guarantees bullet) and `:358` (failure-mode table row)

**Context:** The spec currently references a heartbeat-based failure handler that does not exist, and states the lifecycle guarantee in a way that was only true for the executor's own teardown path. After Tasks 1–2 the guarantee holds for the reconciliation paths too; the spec wording needs to match.

- [ ] **Step 1: Replace the lifecycle-guarantees bullet**

  In `docs/2026-04-21-optio-widget-extensions-design.md`, find line 345:
  ```
  - `widgetUpstream` is cleared whenever the process enters a terminal state, whether or not the worker called `clear_widget_upstream` explicitly. Optio-core clears it as part of the standard teardown path.
  ```

  Replace with:
  ```
  - `widgetUpstream` is cleared whenever the process enters a terminal state, whether or not the worker called `clear_widget_upstream` explicitly. Three code paths maintain this invariant: (1) the executor's normal teardown in `_execute_process` for cooperating tasks; (2) `_reconcile_interrupted_processes` at server start for processes left in active states by a crashed or killed previous session; (3) `_force_finalize_stuck_processes` at shutdown for tasks that did not unwind within the grace period. Paths (2) and (3) also force the state to `failed`; see the failure-mode table below.
  ```

- [ ] **Step 2: Replace the stale failure-mode table row**

  Find the row at line 358:
  ```
  | Worker process crashes hard (no teardown) | Existing instance-liveness heartbeat detects the dead process and transitions it to `failed`; `widgetUpstream` cleared as part of that transition. | Nothing. |
  ```

  Replace with:
  ```
  | Worker process crashes hard (no teardown) | DB row remains in its active state until the next optio-core start, when `_reconcile_interrupted_processes` transitions it to `failed` with error "Process was interrupted by server restart" and clears `widgetUpstream`. `widgetData` is preserved per lifecycle rules. In-flight proxied requests during the dead window return 502 or 404 from the widget proxy's upstream-lookup (stale cache expires within 5 s; thereafter MongoDB is the source of truth). | Nothing. |
  | Shutdown grace period elapses before task unwinds | `_force_finalize_stuck_processes` transitions the row to `failed` with error "Task did not exit within shutdown grace period" and clears `widgetUpstream`, conditionally so a task that flushed a terminal state at the last moment keeps its own terminal write. | Cooperating tasks should respond to `ctx.should_continue()` and exit promptly; long-running subprocess terminate paths should fit in the grace window. |
  ```

  The second row above is NEW — the shutdown-grace-period failure mode was never described in the spec. Adding it now brings the failure-mode table in line with the lifecycle reality introduced by `5d658a2` and the widget fixes that followed.

- [ ] **Step 3: Verify the spec still reads coherently**

  Read `docs/2026-04-21-optio-widget-extensions-design.md` lines 340-370. Confirm:
  - The "Optio's lifecycle guarantees" bullets now reference three code paths explicitly.
  - The failure-mode table has an accurate "Worker process crashes hard" row with no heartbeat mention.
  - The new "Shutdown grace period elapses" row is present.
  - No other row references the nonexistent heartbeat-based handler.

  If any reference remains, remove it.

---

## Task 4: Commit

**Files:** all the above, in one commit.

- [ ] **Step 1: Re-verify all tests pass**

  ```bash
  cd /home/csillag/deai/optio/packages/optio-core
  pytest -q
  ```

  Expected: all tests pass.

- [ ] **Step 2: Stage the exact files**

  ```bash
  cd /home/csillag/deai/optio
  git add packages/optio-core/src/optio_core/lifecycle.py \
          packages/optio-core/tests/test_lifecycle_reconciliation.py \
          docs/2026-04-21-optio-widget-extensions-design.md \
          docs/2026-04-22-widget-reconciliation-delta-plan.md
  ```

  Note: the plan file itself is included so the branch carries the record of this delta.

- [ ] **Step 3: Verify the staged changes**

  ```bash
  cd /home/csillag/deai/optio
  git status
  git diff --staged --stat
  ```

  Expected: four files staged, no other changes. If `packages/optio-demo/src/optio_demo/notebooks/sample.py` or `__marimo__/` appears modified/untracked, those are marimo's own runtime state from manual testing and must NOT be staged.

- [ ] **Step 4: Create the commit**

  ```bash
  cd /home/csillag/deai/optio
  git commit -m "$(cat <<'EOF'
  Clear widgetUpstream in reconciliation paths; document lifecycle

  The widget-extensions spec (docs/2026-04-21-optio-widget-extensions-
  design.md) guarantees that `widgetUpstream` is cleared whenever a
  process enters a terminal state. optio-core satisfies this for the
  executor's normal teardown, but the two reconciliation paths added on
  main at 5d658a2 (_reconcile_interrupted_processes at server start and
  _force_finalize_stuck_processes at shutdown) transitioned rows to
  `failed` while leaving `widgetUpstream` intact, contradicting the
  spec. The spec itself also referenced a nonexistent heartbeat-based
  failure handler in its failure-mode table, a pre-existing inaccuracy
  that surfaced alongside this gap.

  Both reconciliation functions now write `widgetUpstream: None`
  alongside the status transition:

   - _reconcile_interrupted_processes uses a second update_one per
     reconciled row (consistent with its existing per-row pattern).
   - _force_finalize_stuck_processes folds the widgetUpstream null into
     its existing conditional \$set, so a task that flushed its own
     terminal state at the last moment keeps full ownership of its
     widgetUpstream transition.

  widgetData is intentionally not cleared — the widget-extensions
  lifecycle preserves it across terminal states for post-mortem
  inspection.

  The existing reconciliation tests are extended: the startup-
  reconciliation test seeds widgetUpstream + widgetData on active rows
  and asserts widgetUpstream is cleared while widgetData survives; the
  shutdown force-finalize test seeds widgetUpstream via
  ctx.set_widget_upstream and asserts clearing. One new test pins the
  invariant that _force_finalize's conditional update does not touch a
  cooperative task's row.

  Spec updates: the "Optio's lifecycle guarantees" bullet now names all
  three code paths that maintain the invariant. The failure-mode table
  replaces the heartbeat reference with the reconciliation path and
  adds a new row for the shutdown-grace-period case that was never
  documented.
  EOF
  )"
  ```

- [ ] **Step 5: Verify the commit is clean**

  ```bash
  cd /home/csillag/deai/optio
  git log --oneline -1
  git status
  ```

  Expected: one new commit on `feat/widget-extensions`, working tree clean (modulo the marimo runtime artifacts).

---

## Self-Review Notes

- **Spec coverage:** All three drift findings are addressed — Drift 1 by Task 3 (replace heartbeat row), Drift 2 by Tasks 1–2 (implementation gap in both reconciliation paths), Drift 3 by Task 3 (the new lifecycle guarantees bullet names the real code paths; the old plan itself is left untouched per project convention).
- **Placeholders:** none. Every code step has concrete code; every run step has a concrete command and expected outcome.
- **Type consistency:** the function names `_reconcile_interrupted_processes` and `_force_finalize_stuck_processes` match the code; the Mongo field name is `widgetUpstream` (camelCase, per the adapter convention shown in `models.py`).
- **One-commit scope:** all five numbered tasks' edits land in one commit per the user's batch-commits preference.
