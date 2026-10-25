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

    A `scheduled` record that no task of this engine started (a TaskFactory
    launch waiting for its slot, the window before a run's start, or, as
    here, a record seeded into `scheduled` directly, mirroring
    `tests/test_task_ttl.py::test_early_cancel_scheduled_task_with_ttl_sets_expire_at`)
    has no cooperative flag to set. The cancel-stale helper ends it at once
    instead, as cancel() ends a scheduled record: `cancelled`, so it never
    runs, and its wait for terminal states has nothing to wait for (no
    grace warning). Without scheduled in the helper's `non_terminal` set,
    the record would be deleted directly like an idle one, never ended.
    The record is then deleted as usual.
    Spec: docs/2026-10-25-factory-results-design.md §3
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
    # The grace only bounds the wait of a regression; nothing here waits it out.
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=30.0,
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

        # The record's state as the cancel-stale helper leaves it, before the
        # resync deletes it.
        after_cancel: list[str] = []
        real = optio._cancel_stale_processes

        async def cancel_stale(stale, grace_seconds):
            await real(stale, grace_seconds)
            doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
            after_cancel.append(doc["status"]["state"])

        optio._cancel_stale_processes = cancel_stale

        # Drop t1 from the registered set, then resync with caplog active.
        state["tasks"] = []
        with caplog.at_level(logging.WARNING, logger="optio_core_core"):
            await optio.resync()

        # Ended at once by the cancel-stale helper, then deleted.
        assert after_cancel == ["cancelled"]
        assert await mongo_db["test_processes"].find_one({"processId": "t1"}) is None
        warnings = [
            rec.getMessage() for rec in caplog.records
            if rec.levelno == logging.WARNING
        ]
        assert not any("cancel-stale grace exceeded" in msg for msg in warnings), warnings
    finally:
        await optio.shutdown(grace_seconds=1.0)

