"""Tests for lost final-state writes.

Spec: docs/2026-10-03-lost-final-state-writes-design.md

Rule 3: a final-state write that fails because Mongo is unreachable is kept
and retried by the supervisor loop. Rule 4: cancel / cancel_and_wait /
group_cancel_and_wait settle an active row that has no task behind it instead
of waiting for it.

"Mongo is unreachable" is simulated by making chosen writes raise
AutoReconnect. No test depends on wall-clock time: every wait is on a
condition, with a 60 s hang ceiling.
"""
import asyncio
import time

import pytest
from pymongo.errors import AutoReconnect

from optio_core.lifecycle import Optio
from optio_core.models import ProcessStatus, TaskInstance
from optio_core.store import get_process_by_process_id, upsert_process

pytestmark = pytest.mark.asyncio

HANG_CEILING = 60.0


async def _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=2.0, run=True):
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run()) if run else None
    return optio, run_task


async def _stop_optio(optio, run_task):
    await optio.shutdown()
    if run_task is not None:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def _wait_until(pred, what):
    deadline = time.monotonic() + HANG_CEILING
    while True:
        value = await pred()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


async def _wait_state(mongo_db, prefix, process_id, states, log=None):
    """Wait for the row to reach one of `states` and, with `log`, also carry
    that log entry. The status and the log line are separate writes, so a
    test must wait for both rather than read the log right after the state."""
    async def pred():
        proc = await get_process_by_process_id(mongo_db, prefix, process_id)
        if proc is None or proc["status"]["state"] not in states:
            return None
        if log is not None and not _logged(proc, log):
            return None
        return proc
    what = f"{process_id} to reach {sorted(states)}" + (f" with log {log!r}" if log else "")
    return await _wait_until(pred, what)


def _fail_update_status(monkeypatch, states, times=1):
    """Make the executor's next `times` writes of a status in `states` raise."""
    import optio_core.executor as ex
    real = ex.update_status
    left = {"n": times}

    async def flaky(db, prefix, oid, status, expire_at=None):
        if status.state in states and left["n"] > 0:
            left["n"] -= 1
            raise AutoReconnect("injected: mongo unreachable")
        return await real(db, prefix, oid, status, expire_at=expire_at)

    monkeypatch.setattr(ex, "update_status", flaky)
    return left


def _logged(proc, needle):
    return any(needle in (e.get("message") or "") for e in proc.get("log", []))


async def _done(ctx):  # noqa: ARG001
    return None


# ---------- Rule 3: retry a final-state write that did not land ----------

async def test_lost_done_write_is_recorded_late(mongo_db, monkeypatch):
    prefix = "lfw_done"
    task = TaskInstance(process_id="p.done", name="Done", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [task])
    try:
        _fail_update_status(monkeypatch, {"done"})
        await optio.launch("p.done", session_id=None)
        proc = await _wait_state(
            mongo_db, prefix, "p.done", {"done"}, log="State recorded late: done",
        )
        assert proc["status"]["doneAt"] is not None
    finally:
        await _stop_optio(optio, run_task)


async def test_lost_failed_write_is_recorded_late(mongo_db, monkeypatch):
    prefix = "lfw_failed"

    async def boom(ctx):  # noqa: ARG001
        raise ValueError("boom")

    task = TaskInstance(process_id="p.fail", name="Fail", params={}, execute=boom)
    optio, run_task = await _start_optio(mongo_db, prefix, [task])
    try:
        _fail_update_status(monkeypatch, {"failed"})
        await optio.launch("p.fail", session_id=None)
        proc = await _wait_state(
            mongo_db, prefix, "p.fail", {"failed"}, log="State recorded late: failed",
        )
        assert proc["status"]["error"] == "boom"
    finally:
        await _stop_optio(optio, run_task)


async def test_lost_cooperative_cancel_write_is_recorded_late(mongo_db, monkeypatch):
    prefix = "lfw_coop"
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        while not ctx.cancellation_flag.is_set():
            await asyncio.sleep(0.02)

    task = TaskInstance(process_id="p.coop", name="Coop", params={}, execute=cooperative)
    optio, run_task = await _start_optio(mongo_db, prefix, [task])
    try:
        _fail_update_status(monkeypatch, {"cancelled"})
        await optio.launch("p.coop", session_id=None)
        await started.wait()
        await optio.cancel("p.coop")
        await _wait_state(
            mongo_db, prefix, "p.coop", {"cancelled"}, log="State recorded late: cancelled",
        )
    finally:
        await _stop_optio(optio, run_task)


async def test_lost_force_cancel_write_is_recorded_late(mongo_db, monkeypatch):
    """The bobcat incident: the force-cancel write raised and was never retried."""
    import optio_core._force_cancel as fc

    prefix = "lfw_force"
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    real = fc._write_force_cancelled_state
    left = {"n": 1}

    async def flaky(db, prefix_, oid, *args, **kwargs):
        if left["n"] > 0:
            left["n"] -= 1
            raise AutoReconnect("injected: mongo unreachable")
        return await real(db, prefix_, oid, *args, **kwargs)

    monkeypatch.setattr(fc, "_write_force_cancelled_state", flaky)

    task = TaskInstance(process_id="p.stub", name="Stub", params={}, execute=stubborn)
    optio, run_task = await _start_optio(mongo_db, prefix, [task], cancel_grace_seconds=0.2)
    try:
        await optio.launch("p.stub", session_id=None)
        await started.wait()
        await optio.cancel("p.stub")
        proc = await _wait_state(mongo_db, prefix, "p.stub", {"failed"})
        assert proc["status"]["error"] == fc.FORCE_CANCEL_ERROR
        assert left["n"] == 0  # the injected failure did happen
    finally:
        await _stop_optio(optio, run_task)


async def test_lost_running_write_settles_the_scheduled_row(mongo_db, monkeypatch):
    prefix = "lfw_start"
    task = TaskInstance(process_id="p.start", name="Start", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [task])
    try:
        _fail_update_status(monkeypatch, {"running"})
        await optio.launch("p.start", session_id=None)
        proc = await _wait_state(mongo_db, prefix, "p.start", {"failed"})
        assert proc["status"]["error"].startswith("Final state could not be recorded")
    finally:
        await _stop_optio(optio, run_task)


async def test_retry_keeps_trying_and_never_overwrites_a_settled_row(mongo_db, monkeypatch):
    """Executor-level: a failed retry stays queued; a row that left the active
    states in the meantime is left alone and the entry is dropped."""
    import optio_core.executor as ex
    from optio_core.executor import Executor

    prefix = "lfw_retry"
    tasks = [
        TaskInstance(process_id=pid, name=pid, params={}, execute=_done)
        for pid in ("p.keep", "p.moved")
    ]
    for t in tasks:
        await upsert_process(mongo_db, prefix, t)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks(tasks)

    _fail_update_status(monkeypatch, {"done"}, times=2)
    for pid in ("p.keep", "p.moved"):
        with pytest.raises(AutoReconnect):
            await executor.launch_process(pid, session_id=None)
    keep = await get_process_by_process_id(mongo_db, prefix, "p.keep")
    moved = await get_process_by_process_id(mongo_db, prefix, "p.moved")
    assert keep["status"]["state"] == "running"
    assert set(executor._unrecorded_finals) == {keep["_id"], moved["_id"]}

    # Something else settles p.moved meanwhile.
    await mongo_db[f"{prefix}_processes"].update_one(
        {"_id": moved["_id"]}, {"$set": {"status": ProcessStatus(state="idle").to_dict()}},
    )

    # First retry: Mongo still unavailable for p.keep.
    real_finalize = ex.finalize_if_active
    left = {"n": 1}

    async def flaky_finalize(db, prefix_, oid, status, **kw):
        if oid == keep["_id"] and left["n"] > 0:
            left["n"] -= 1
            raise AutoReconnect("injected: mongo unreachable")
        return await real_finalize(db, prefix_, oid, status, **kw)

    monkeypatch.setattr(ex, "finalize_if_active", flaky_finalize)
    await executor.retry_unrecorded_finals()
    assert keep["_id"] in executor._unrecorded_finals
    assert moved["_id"] not in executor._unrecorded_finals
    moved = await get_process_by_process_id(mongo_db, prefix, "p.moved")
    assert moved["status"]["state"] == "idle"

    # Make the backed-off entry due, then retry: it lands.
    executor._unrecorded_finals[keep["_id"]].next_attempt = 0.0
    await executor.retry_unrecorded_finals()
    assert executor._unrecorded_finals == {}
    keep = await get_process_by_process_id(mongo_db, prefix, "p.keep")
    assert keep["status"]["state"] == "done"


# ---------- Rule 4: settle a row that has no task behind it ----------

async def _orphan(mongo_db, prefix, process_id, state):
    """Leave a row active with no task behind it, as a lost final write does."""
    await mongo_db[f"{prefix}_processes"].update_one(
        {"processId": process_id},
        {"$set": {"status": ProcessStatus(state=state).to_dict()}},
    )


def _assert_settled_orphan(proc, prev_state):
    assert proc["status"]["state"] == "failed", proc["status"]
    assert "no running task" in proc["status"]["error"]
    assert _logged(proc, f"State reconciled: {prev_state} -> failed (no running task)")


async def test_group_cancel_and_wait_settles_orphans(mongo_db):
    prefix = "lfw_group"
    states = {"p.run": "running", "p.req": "cancel_requested", "p.cing": "cancelling"}
    tasks = [
        TaskInstance(process_id=pid, name=pid, params={}, execute=_done,
                     metadata={"grp": "g"})
        for pid in states
    ]
    optio, run_task = await _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=0.2)
    try:
        for pid, state in states.items():
            await _orphan(mongo_db, prefix, pid, state)
        await optio.group_cancel_and_wait({"grp": "g"})
        for pid, state in states.items():
            proc = await get_process_by_process_id(mongo_db, prefix, pid)
            # cancel() moves a running orphan to cancel_requested first.
            prev = "cancel_requested" if state == "running" else state
            _assert_settled_orphan(proc, prev)
    finally:
        await _stop_optio(optio, run_task)


async def test_cancel_and_wait_settles_orphan(mongo_db):
    prefix = "lfw_caw"
    task = TaskInstance(process_id="p.cing", name="Cing", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [task], cancel_grace_seconds=0.2)
    try:
        await _orphan(mongo_db, prefix, "p.cing", "cancelling")
        assert await optio.cancel_and_wait("p.cing") == "failed"
        proc = await get_process_by_process_id(mongo_db, prefix, "p.cing")
        _assert_settled_orphan(proc, "cancelling")
    finally:
        await _stop_optio(optio, run_task)


async def test_cancel_settles_running_orphan(mongo_db):
    """Before: the row was left in cancel_requested for good."""
    prefix = "lfw_cancel"
    task = TaskInstance(process_id="p.run", name="Run", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [task])
    try:
        await _orphan(mongo_db, prefix, "p.run", "running")
        outcome = await optio.cancel("p.run")
        assert outcome.ok
        proc = await get_process_by_process_id(mongo_db, prefix, "p.run")
        _assert_settled_orphan(proc, "cancel_requested")
    finally:
        await _stop_optio(optio, run_task)


async def test_orphan_with_parked_final_gets_the_parked_state(mongo_db, monkeypatch):
    """A row whose final write is queued (Rule 3) settles to that state, not
    to the generic orphan failure. No run(): the supervisor must not retry first."""
    prefix = "lfw_parked"
    task = TaskInstance(process_id="p.done", name="Done", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [task], run=False)
    try:
        _fail_update_status(monkeypatch, {"done"})
        await optio.launch("p.done", session_id=None)

        async def parked():
            return bool(optio._executor._unrecorded_finals)
        await _wait_until(parked, "the done write to be parked")

        assert await optio.cancel_and_wait("p.done") == "done"
    finally:
        await _stop_optio(optio, run_task)


async def test_live_task_and_scheduled_row_are_not_orphans(mongo_db):
    prefix = "lfw_live"
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        while not ctx.cancellation_flag.is_set():
            await asyncio.sleep(0.02)

    live = TaskInstance(process_id="p.live", name="Live", params={}, execute=cooperative,
                        metadata={"grp": "live"})
    sched = TaskInstance(process_id="p.sched", name="Sched", params={}, execute=_done)
    optio, run_task = await _start_optio(mongo_db, prefix, [live, sched])
    try:
        await optio.launch("p.live", session_id=None)
        await started.wait()
        await optio.group_cancel_and_wait({"grp": "live"})
        proc = await get_process_by_process_id(mongo_db, prefix, "p.live")
        assert proc["status"]["state"] == "cancelled"

        await _orphan(mongo_db, prefix, "p.sched", "scheduled")
        assert await optio.cancel_and_wait("p.sched") == "cancelled"
    finally:
        await _stop_optio(optio, run_task)
