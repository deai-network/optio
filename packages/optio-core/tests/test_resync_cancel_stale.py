"""Tests for cancel-stale-on-resync (B1)."""

import asyncio
import logging

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def test_resync_cancels_running_stale_task(mongo_db):
    """A stale task in `running` state is cooperatively cancelled, then deleted.

    Without B1, the resync would delete the record while the asyncio task is
    still running, leading to silent log/state writes against a vanished
    record. With B1, the task is asked to cancel and the resync waits for
    the terminal transition before proceeding to deletion.
    """
    started = asyncio.Event()

    async def hold_until_cancel(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.02)

    task1 = TaskInstance(
        execute=hold_until_cancel, process_id="t1", name="t1",
    )

    state = {"tasks": [task1]}

    async def gen(services, metadata_filter=None):
        return state["tasks"]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    try:
        await optio.launch("t1", session_id=None)
        await asyncio.wait_for(started.wait(), timeout=60.0)

        # Snapshot the running asyncio task so we can confirm it terminated.
        proc_doc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert proc_doc is not None
        oid = proc_doc["_id"]
        running_task = optio._executor._running_tasks.get(oid)
        assert running_task is not None and not running_task.done()

        # Drop t1 from the registered set, then resync.
        state["tasks"] = []
        await optio.resync()

        # Record is gone (remove_stale_processes ran after cancel).
        assert await mongo_db["test_processes"].find_one({"processId": "t1"}) is None

        # The cooperative cancel succeeded: the executor's in-memory tracking
        # for this oid is gone (the task removes itself from
        # _cancellation_flags / _running_tasks on terminal exit). Without B1,
        # remove_stale_processes deletes the record while the asyncio task is
        # still mid-flight — which would leave _cancellation_flags[oid]
        # populated at this point.
        assert oid not in optio._executor._cancellation_flags, (
            "stale running task was deleted before cooperative cancel "
            "completed; B1 (cancel-stale-on-resync) regression"
        )
        # The running task either is done or finishing its terminal-cleanup
        # finally block. Give it a moment to fully complete.
        await asyncio.wait_for(running_task, timeout=60.0)
        assert running_task.done()
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_resync_deletes_idle_stale_directly(mongo_db):
    """Idle (non-running) stale tasks are deleted without cancel attempt."""

    async def quick(ctx):
        return

    task1 = TaskInstance(execute=quick, process_id="t1", name="t1")

    state = {"tasks": [task1]}

    async def gen(services, metadata_filter=None):
        return state["tasks"]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    try:
        # t1 was synced but never launched; status is idle.
        doc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert doc is not None
        assert doc["status"]["state"] == "idle"

        state["tasks"] = []
        await optio.resync()

        assert await mongo_db["test_processes"].find_one({"processId": "t1"}) is None
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_resync_cancel_grace_exceeded_proceeds_with_deletion(mongo_db):
    """Uncooperative task: cancel times out, but record is still deleted (warn)."""

    started = asyncio.Event()

    async def uncooperative(ctx):
        started.set()
        # Ignore cancellation flag entirely — sleep a long time.
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            # If hard-cancelled, swallow and re-sleep to remain "alive".
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                pass

    task1 = TaskInstance(execute=uncooperative, process_id="t1", name="t1")

    state = {"tasks": [task1]}

    async def gen(services, metadata_filter=None):
        return state["tasks"]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    try:
        await optio.launch("t1", session_id=None)
        await asyncio.wait_for(started.wait(), timeout=60.0)

        state["tasks"] = []
        await optio.resync()

        # Even though cancel grace was exceeded, record is gone.
        assert await mongo_db["test_processes"].find_one({"processId": "t1"}) is None
    finally:
        await optio.shutdown(grace_seconds=0.5)


async def test_resync_does_not_cancel_non_stale_running_task(mongo_db):
    """Regression guard: a task that is NOT stale must not be cancelled."""

    started = asyncio.Event()

    async def hold_until_cancel(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.02)

    task1 = TaskInstance(
        execute=hold_until_cancel, process_id="t1", name="t1",
    )

    async def gen(services, metadata_filter=None):
        return [task1]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    try:
        await optio.launch("t1", session_id=None)
        await asyncio.wait_for(started.wait(), timeout=60.0)

        # Resync — same task list, t1 stays.
        await optio.resync()

        doc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert doc is not None
        assert doc["status"]["state"] == "running"
        # Cancellation flag must not have been set.
        oid = doc["_id"]
        entry = optio._executor._cancellation_flags.get(oid)
        assert entry is not None, "task should still be running"
        assert not entry.flag.is_set(), "non-stale task must not be cancelled"
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_resync_cancels_scheduled_stale_task(mongo_db, caplog):
    """A stale task whose DB state is `scheduled` is processed by the
    cancel-stale path before deletion.

    In production, the `scheduled`-state window between `update_status`
    and the executor's in-memory `_cancellation_flags` entry is racy and
    typically tiny, so the deterministic way to pin the contract is to
    seed the DB record directly into `scheduled` (mirroring the pattern
    used by `tests/test_task_ttl.py::test_early_cancel_scheduled_task_with_ttl_sets_expire_at`).
    The cancel-stale helper still classifies `scheduled` as non-terminal
    and routes the record through the cancel path (a cooperative-cancel
    request followed by a bounded wait for terminal). Without scheduled
    in the helper's `non_terminal` set, the record would be deleted
    directly like an idle one — bypassing the cancel-stale contract.

    With no executor entry (the seeded record was never launched), the
    cooperative cancel is a no-op and the helper's grace timeout fires;
    the resulting warning is the observable proof the helper picked up
    the scheduled-stale record. The record is then deleted as usual.
    """
    from optio_core.models import ProcessStatus
    from optio_core.store import update_status

    async def quick(ctx):
        return

    task1 = TaskInstance(execute=quick, process_id="t1", name="t1")

    state = {"tasks": [task1]}

    async def gen(services, metadata_filter=None):
        return state["tasks"]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    try:
        # Seed the DB record into `scheduled` state directly — no executor
        # entry, no asyncio task. This mirrors test_task_ttl.py's approach
        # for exercising the scheduled-state window.
        proc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert proc is not None and proc["status"]["state"] == "idle"
        await update_status(
            mongo_db, "test", proc["_id"], ProcessStatus(state="scheduled"),
        )

        # Drop t1 from the registered set, then resync with caplog active.
        state["tasks"] = []
        with caplog.at_level(logging.WARNING, logger="optio_core_core"):
            await optio.resync()

        # Record is gone — cancel-stale-then-delete completed.
        assert await mongo_db["test_processes"].find_one({"processId": "t1"}) is None

        # Observable proof the helper picked up the scheduled-stale record:
        # `_cancel_stale_processes` emits a `cancel-stale grace exceeded`
        # warning when the record fails to reach a terminal state within
        # `cancel_grace_seconds`. An idle-stale record skips the helper
        # entirely (per the regression in test_resync_deletes_idle_stale_directly)
        # and never produces this warning. So this assertion pins
        # "scheduled is in the helper's non_terminal set".
        warnings = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.WARNING
        ]
        assert any(
            "cancel-stale grace exceeded" in msg and "'t1'" in msg
            for msg in warnings
        ), (
            f"expected cancel-stale grace warning naming 't1'; "
            f"got: {warnings!r}"
        )
    finally:
        await optio.shutdown(grace_seconds=1.0)

