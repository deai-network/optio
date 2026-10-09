# Fewer writes per process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each step of a process's life (relaunch, start, message, end, dismiss) is one MongoDB update on the process document instead of two or three.

**Architecture:** `store.update_status` and `store.update_progress` gain optional `log` (and `set_fields`) arguments that ride in the same `update_one`; a new `store.relaunch_reset` replaces the reset + scheduled + log trio. The executor, `ProcessContext._write_progress` and `Optio.dismiss` call those instead of separate `append_log` / `clear_widget_upstream` / raw `update_one` writes. Nothing is buffered.

**Tech Stack:** Python 3, motor (async MongoDB), pytest-asyncio against the real test Mongo.

**Spec:** `docs/2026-10-09-fewer-process-writes-design.md`

## Global Constraints

- What is recorded stays the same: same fields, same log levels, messages and order, each entry's timestamp taken when its step happens. No buffering.
- Untouched: the reconcile at `init()`, orphan settling (`finalize_if_active`), the late final write, resurrect, `set_widget_*`, the resume flags, optio-api.
- `append_log`, `clear_widget_upstream` and `clear_result_fields` keep their current behaviour (other callers and tests use them).
- Tests must not depend on wall-clock time (AGENTS.md). Cancel deadlines in tests are `time.monotonic() + 3600`.
- Commits: no Co-Authored-By line (optio AGENTS.md rule).
- Test command, from `packages/optio-core` on the excavator host checkout `~/deai/optio` (local changes synced there): `../../.venv/bin/pytest <path> -v`.

## Review Focus

- **`$set` path conflict on relaunch:** setting `status` and `status.error` (etc.) in one `$set` is rejected by MongoDB ("would create a conflict"). `relaunch_reset` sets the whole `status` (its `to_dict()` writes every field, `None` included) and must leave out the `status.*` paths of the reset. Pinned by the Task 1 relaunch test, which runs against real Mongo.
- **A failing combined write:** status and log line now land or fail together. A failed end write must still park the final state (Rule 3) and later write "State recorded late: …". Covered by the existing `test_lost_final_writes.py`, whose `update_status` double must forward the new keyword arguments (Task 2).
- **A failing message write:** a progress write that raises loses that line (as today: the log write never ran after the progress write failed), and later messages still land. Covered by the existing `test_flush_failure_is_logged…` test in `test_progress_throttle.py`, once its doubles forward `log=` (Task 3).
- **Test doubles with fixed signatures:** wrappers of `update_status(db, prefix, oid, status, expire_at=None)` and `update_progress(db, prefix, oid, progress)` raise `TypeError` once the code passes `log=`. Six such doubles exist in optio-core's tests (Tasks 2, 3). Excavator's tests only replace `append_log`, which is unchanged.
- **A relaunch must not keep the previous run's children:** `relaunch_reset` does not delete; `launch_process` and `dismiss` call `delete_descendants` first. Pinned in Task 1 (`relaunch_reset` leaves a child alone) and Task 2 (a relaunch after a run with a child leaves no child).

---

### Task 1: store -- one-update writers

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py` (`update_status` ~176, `update_progress` ~274, `append_log` ~284, `clear_result_fields` ~396)
- Create: `packages/optio-core/tests/process_write_counter.py`
- Test: `packages/optio-core/tests/test_store.py`

**Interfaces:**
- Produces:
  - `update_status(db, prefix, process_oid, status, expire_at=None, *, log: tuple[str, str] | None = None, set_fields: dict[str, Any] | None = None) -> None`
  - `update_progress(db, prefix, process_oid, progress, *, log: tuple[str, str] | None = None) -> None`
  - `relaunch_reset(db, prefix, process_oid, status: ProcessStatus, log: tuple[str, str] | None) -> None`
  - test helper `CountingDb(db, prefix)`: wraps a motor database; `db[f"{prefix}_processes"]` returns a recording collection, everything else passes through. `.writes: list[Write]`, `Write(op: str, oid: ObjectId | None, update: dict | None)`; `op` is `"insert"`, `"update"` or `"delete"`; `oid` is the inserted `_id` or the filter's `_id`; `update` is the update document for `"update"`. `.updates_on(oid) -> list[dict]` returns the update documents for one OID in order.

- [ ] **Step 1: Write `tests/process_write_counter.py`** (`CountingDb`, `Write`): the recording collection wraps `insert_one`, `update_one`, `update_many`, `delete_one`, `delete_many` and delegates every other attribute (`find`, `find_one`, …) via `__getattr__`.

- [ ] **Step 2: Write the failing tests in `tests/test_store.py`**

```python
async def test_update_status_with_log_and_fields_is_one_update(mongo_db):
    oid = (await upsert_process(mongo_db, "test", TaskInstance(execute=_noop, process_id="s1", name="S")))["_id"]
    db = CountingDb(mongo_db, "test")
    await update_status(db, "test", oid, ProcessStatus(state="running"),
                        log=("event", "State changed to running"),
                        set_fields={"originatingSessionId": "tok-1"})
    assert len(db.updates_on(oid)) == 1
    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["status"]["state"] == "running"
    assert doc["originatingSessionId"] == "tok-1"
    assert (doc["log"][-1]["level"], doc["log"][-1]["message"]) == ("event", "State changed to running")
    assert isinstance(doc["log"][-1]["timestamp"], str)

async def test_update_progress_with_log_is_one_update(mongo_db):
    # update_progress(db, "test", oid, Progress(percent=None, message="hello"), log=("info", "hello"))
    # -> 1 update; doc["progress"]["message"] == "hello"; log[-1] == ("info", "hello")

async def test_relaunch_reset_is_one_update_and_resets_like_clear_result_fields(mongo_db):
    # Seed: status failed (error, failedAt), progress 40 "x", log of 3 entries,
    # widgetData {"a": 1}, widgetUpstream {"url": "u", "innerAuth": None},
    # and a child (create_child_process with parent_oid=oid).
    # relaunch_reset(db, "test", oid, ProcessStatus(state="scheduled"),
    #                ("event", "State changed to scheduled"))
    # -> 1 update on oid, no delete
    # doc["status"] == ProcessStatus(state="scheduled").to_dict()
    # doc["progress"] == Progress().to_dict()
    # [(e["level"], e["message"]) for e in doc["log"]] == [("event", "State changed to scheduled")]
    # doc["widgetData"] is None and doc["widgetUpstream"] is None
    # the child still exists

async def test_relaunch_reset_without_log_empties_the_log(mongo_db):
    # relaunch_reset(..., ProcessStatus(state="idle"), None) -> doc["log"] == []
```

- [ ] **Step 3: Run them, see them fail**

Run: `../../.venv/bin/pytest tests/test_store.py -v -k "one_update or relaunch_reset"`
Expected: FAIL (`TypeError: unexpected keyword argument 'log'`, `ImportError: relaunch_reset`).

- [ ] **Step 4: Implement in `store.py`**

- `_log_entry(level, message, data=None) -> dict`: the entry `append_log` builds today (timestamp `datetime.now(timezone.utc).isoformat()` at call time); `append_log` uses it.
- `_reset_fields() -> dict`: the `$set` of `clear_result_fields` today; `clear_result_fields` uses it (behaviour unchanged).
- `update_status` / `update_progress`: add `set_fields` to the `$set` and `{"$push": {"log": _log_entry(*log)}}` when given, in the same `update_one`. Without the new arguments the update document is exactly as today.
- `relaunch_reset`: `$set` = `_reset_fields()` minus its `status.*` keys, plus `status: status.to_dict()` and `log: [_log_entry(*log)] if log else []`. No delete.

- [ ] **Step 5: Run `tests/test_store.py` and `tests/test_widget_primitives.py`**

Expected: all PASS (the existing `clear_result_fields` tests included).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/store.py packages/optio-core/tests/process_write_counter.py packages/optio-core/tests/test_store.py
git commit -m "feat(optio-core): status and progress writes can carry their log line"
```

### Task 2: executor -- relaunch, start and end as one update each

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py` (`launch_process` ~183-188, `_execute_process` ~238-250, ~276-286, ~330-395; imports ~31-36)
- Modify: `packages/optio-core/tests/test_lost_final_writes.py:84` (the `flaky` double)
- Test: create `packages/optio-core/tests/test_process_writes.py`

**Interfaces:**
- Consumes: `update_status(..., log=, set_fields=)`, `relaunch_reset`, `CountingDb` (Task 1).
- Produces: nothing new for later tasks.

Setup for every test: `upsert_process(mongo_db, "test", task)` on the real db, then `Executor(CountingDb(mongo_db, "test"), "test", {})`, `register_tasks([task])`, `await executor.launch_process(pid, session_id=...)`. Cancel tests start the run with `asyncio.create_task`, wait on an `asyncio.Event` the body sets once running, then `executor.request_cancel_with_deadline(oid, deadline=time.monotonic() + 3600)`.

- [ ] **Step 1: Write the failing tests in `tests/test_process_writes.py`**

```python
async def test_top_level_run_without_messages_is_three_updates(mongo_db):
    # body: return
    # db.updates_on(oid) has length 3; no other writes
    # [e["message"] for e in doc["log"]] == ["State changed to scheduled",
    #     "State changed to running", "State changed to done"]

async def test_start_update_carries_session_id_and_running_line(mongo_db):
    # launch_process(pid, session_id="tok-1")
    # start = db.updates_on(oid)[1]
    # start["$set"]["status"]["state"] == "running"
    # start["$set"]["originatingSessionId"] == "tok-1"
    # start["$push"]["log"]["message"] == "State changed to running"

@pytest.mark.parametrize("arm", ["done", "failed", "cancelled_returned", "cancelled_raised"])
async def test_end_update_carries_status_line_and_widget_clear(mongo_db, arm):
    # body sets ctx.set_widget_upstream("http://w") first (its own update), then:
    #   done: return; failed: raise ValueError("boom");
    #   cancelled_returned: wait for cancel, return;
    #   cancelled_raised: wait for cancel, raise asyncio.CancelledError()
    #     (the launch task then ends with CancelledError: pytest.raises)
    # end = db.updates_on(oid)[-1]
    # end["$set"]["status"]["state"] == {"done": "done", "failed": "failed"}.get(arm, "cancelled")
    # end["$set"]["widgetUpstream"] is None
    # (end["$push"]["log"]["level"], end["$push"]["log"]["message"]) ==
    #   done:               ("event", "State changed to done")
    #   failed:             ("error", "boom")
    #   cancelled_returned: ("event", "State changed to cancelled")
    #   cancelled_raised:   ("event", "State changed to cancelled (raised CancelledError)")
    # doc["widgetUpstream"] is None

async def test_end_update_sets_expire_at_when_the_task_has_a_ttl(mongo_db):
    # TaskInstance(..., ttl_seconds=3600): "expireAt" in end["$set"]; doc["expireAt"] is not None
    # same task without ttl_seconds: "expireAt" not in end["$set"]

async def test_no_execute_function_ends_in_one_update_with_widget_clear(mongo_db):
    # upsert the task, register nothing
    # 3 updates; end["$set"]["status"]["error"] == "No execute function found"
    # end["$set"]["widgetUpstream"] is None; "$push" not in end

async def test_child_run_is_insert_and_three_updates_plus_one_on_the_parent(mongo_db):
    # parent body: await ctx.run_child(child_body, "p.c", "C"); child body: return
    # parent: 4 updates (relaunch, start, "Spawned child: C", end)
    # child: 1 insert + 3 updates (... plus 1 message in Task 3's version)
    # child log == ["State changed to running", "State changed to done"]

async def test_relaunch_deletes_the_previous_runs_children(mongo_db):
    # run the parent of the test above twice; after the second run exactly one
    # child document with parentId == parent oid exists
```

- [ ] **Step 2: Run them, see them fail**

Run: `../../.venv/bin/pytest tests/test_process_writes.py -v`
Expected: FAIL on the counts (9 updates instead of 3, the end update has no `$push`, and so on). `test_relaunch_deletes…` passes already (a guard, not new behaviour).

- [ ] **Step 3: Implement in `executor.py`**

- `launch_process`: `delete_descendants`, then `relaunch_reset(..., ProcessStatus(state="scheduled"), ("event", "State changed to scheduled"))`. The `hasUnsavedWork` clear stays as it is.
- Start: one `update_status(running, log=("event", "State changed to running"), set_fields={"originatingSessionId": effective_session_id})`; compute `effective_session_id` before it. The `_collection` import goes if nothing else in the module uses it.
- Each end arm: `update_status(final, expire_at=..., log=<the arm's line>, set_fields={"widgetUpstream": None})`, then `final_recorded = True`. The separate `append_log` and `clear_widget_upstream` calls go, including the shared one after the done/cancelled arms; `_cleanup_ephemeral` stays where it is.
- The no-execute-fn branch: the same `set_fields={"widgetUpstream": None}`, no `log`.

- [ ] **Step 4: Update the `flaky` double in `tests/test_lost_final_writes.py`** to `async def flaky(db, prefix, oid, status, expire_at=None, **kw)` and forward `**kw`.

- [ ] **Step 5: Run the new file and the executor, cancel, lost-final, widget, TTL, session-id and resume suites**

Run: `../../.venv/bin/pytest tests/test_process_writes.py tests/test_executor.py tests/test_lost_final_writes.py tests/test_cancel_propagation.py tests/test_widget_primitives.py tests/test_task_ttl.py tests/test_client_directed_events.py tests/test_context_resume.py tests/test_auto_resume.py -v`
Expected: PASS, apart from the failures already on `main` before this change (compare against `8d76a82c` if any fail).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_process_writes.py packages/optio-core/tests/test_lost_final_writes.py
git commit -m "feat(optio-core): a process's relaunch, start and end are one write each"
```

### Task 3: context -- a message and its log line in one update

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py:706-718` (`_write_progress`)
- Modify: `packages/optio-core/tests/test_progress_throttle.py` (the five `update_progress` doubles at ~167, ~206, ~239, ~317, ~427)
- Test: `packages/optio-core/tests/test_process_writes.py`

**Interfaces:**
- Consumes: `update_progress(..., log=)` (Task 1), the test setup of Task 2.

- [ ] **Step 1: Write the failing tests in `tests/test_process_writes.py`**

```python
async def test_two_messages_add_one_update_each(mongo_db):
    # body: ctx.report_progress(None, "a"); ctx.report_progress(50, "b", level="warning") -> return
    # 5 updates on oid
    # [(e["level"], e["message"]) for e in doc["log"]] == [
    #   ("event", "State changed to scheduled"), ("event", "State changed to running"),
    #   ("info", "a"), ("warning", "b"), ("event", "State changed to done")]

async def test_percent_only_progress_writes_no_log_line(mongo_db):
    # body: ctx.report_progress(30) -> return; 4 updates; the progress update has no "$push"
```

Also extend the child test of Task 2: the child body reports one message `"c1"`; the child gets 1 insert + 3 updates.

- [ ] **Step 2: Run them, see them fail** (7 updates instead of 5; the child gets 4 updates instead of 3).

- [ ] **Step 3: Implement `_write_progress`** as one `update_progress(..., log=(level, progress.message) if progress.message else None)`. Flush order, throttle, avalanche and the "(N messages dropped)" line are untouched.

- [ ] **Step 4: Update the five doubles in `tests/test_progress_throttle.py`** to accept `**kw` and forward it to the real `update_progress`.

- [ ] **Step 5: Run `tests/test_process_writes.py tests/test_progress_throttle.py tests/test_child_progress.py tests/test_progress_helpers.py`** -- expected PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_process_writes.py packages/optio-core/tests/test_progress_throttle.py
git commit -m "feat(optio-core): a progress message and its log line are one write"
```

### Task 4: dismiss in one update, then the whole suite

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:1093-1099` (`Optio.dismiss`; imports ~36)
- Modify: `AGENTS.md` (the feature list near "Lost final-state writes"): one line, "Process writes: each lifecycle step (relaunch, start, a message, end, dismiss) is one update on the process document. Spec: `docs/2026-10-09-fewer-process-writes-design.md`".
- Test: `packages/optio-core/tests/test_process_writes.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_dismiss_is_one_update_after_the_descendants_delete(mongo_db):
    # fw = Optio(); db = CountingDb(mongo_db, "dis"); await fw.init(mongo_db=db, prefix="dis")
    # insert a done row with a log entry and a child row (parentId = row oid)
    # db.writes.clear(); out = await fw.dismiss("done1")
    # out.ok; db.updates_on(row_oid) has length 1; the child is gone
    # doc["status"]["state"] == "idle" and doc["log"] == []
```

- [ ] **Step 2: Run it, see it fail** (2 updates).

- [ ] **Step 3: Implement:** `delete_descendants`, then `relaunch_reset(..., ProcessStatus(state="idle"), None)`. Drop imports that are no longer used.

- [ ] **Step 4: Run the whole optio-core suite** the way `make test` does: `../../.venv/bin/pytest -n 4 --dist loadscope -m "not serial"`, then `../../.venv/bin/pytest -m serial`. Expected: the same failures as `main` at `8d76a82c` and nothing new. Run both on `8d76a82c` first if that list is not at hand.

- [ ] **Step 5: Commit and push**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_process_writes.py AGENTS.md
git commit -m "feat(optio-core): dismiss is one write"
git push origin main
```

### Task 5: release, excavator bump, measurement

- [ ] **Step 1:** Wire patch release per `docs/release-cookbook.md`: `make release-wire BUMP=patch` (0.5.2 → 0.5.3). Use `OPTIO_SKIP_PREFLIGHT_TESTS=1` only if the preflight stops on the same failures that `main` had before the change.
- [ ] **Step 2:** In excavator, bump the optio-core and optio-contracts pins to 0.5.3, the same way as the last wire bump. Re-lock, run the engine suite (expected: 1240 passed as of `2cd7847f`, or the same failures as before), commit, push to main and GitHub.
- [ ] **Step 3:** Restart the dev engine on the new code. With the same idle load (about 15 sync checks a minute), count one minute of `gm_processes` change events by `operationType` and read the main MongoDB's CPU-s a minute. Baseline: 906 writes (816 updates, 45 inserts, 45 deletes) and about 18 CPU-s. Expected: about 450 updates. Record the results in `~/chat/idlecost/` next to `wc/` and report them.
