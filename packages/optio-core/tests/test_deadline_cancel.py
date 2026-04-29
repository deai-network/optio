"""Tests for deadline-driven cooperative cancel.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio


pytestmark = pytest.mark.asyncio


async def test_init_accepts_cancel_grace_seconds(mongo_db):
    """Optio.init forwards cancel_grace_seconds onto OptioConfig."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsinit", cancel_grace_seconds=2.5)
    assert optio._config.cancel_grace_seconds == 2.5


async def test_init_default_cancel_grace_seconds(mongo_db):
    """Default cancel_grace_seconds is 5.0."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsdefault")
    assert optio._config.cancel_grace_seconds == 5.0


async def test_executor_tracks_running_task_and_cancel_entry(mongo_db):
    """While a process is running, _running_tasks and _cancellation_flags both
    have an entry; the flag value is a _CancelEntry, not a bare Event."""
    from optio_core.executor import Executor, _CancelEntry
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    prefix = "ctxtrack"
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_until_release(ctx):  # noqa: ARG001
        started.set()
        await release.wait()

    task_inst = TaskInstance(
        process_id="p.hold", name="Hold", params={}, execute=hold_until_release,
    )
    await upsert_process(mongo_db, prefix, task_inst)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks([task_inst])

    runner = asyncio.create_task(executor.launch_process("p.hold"))
    await started.wait()

    # Find the oid via the registry's only entry.
    assert len(executor._running_tasks) == 1
    oid = next(iter(executor._running_tasks))
    assert isinstance(executor._running_tasks[oid], asyncio.Task)
    entry = executor._cancellation_flags[oid]
    assert isinstance(entry, _CancelEntry)
    assert entry.deadline is None
    assert isinstance(entry.flag, asyncio.Event)

    release.set()
    await runner

    # After completion both registries are empty.
    assert executor._running_tasks == {}
    assert executor._cancellation_flags == {}


async def test_request_cancel_with_deadline_records_first_deadline(mongo_db):
    """First call records a deadline; second call is a no-op on the deadline."""
    from optio_core.executor import Executor, _CancelEntry
    import time as _time

    executor = Executor(mongo_db, "rcwd", services={})
    fake_oid = __import__("bson").ObjectId()
    flag = asyncio.Event()
    executor._cancellation_flags[fake_oid] = _CancelEntry(flag=flag, deadline=None)

    first = _time.monotonic() + 1.0
    found = executor.request_cancel_with_deadline(fake_oid, deadline=first)
    assert found is True
    assert flag.is_set()
    assert executor._cancellation_flags[fake_oid].deadline == first

    second = _time.monotonic() + 99.0
    found2 = executor.request_cancel_with_deadline(fake_oid, deadline=second)
    assert found2 is True
    assert executor._cancellation_flags[fake_oid].deadline == first  # not refreshed


async def test_request_cancel_with_deadline_returns_false_when_unknown(mongo_db):
    from optio_core.executor import Executor

    executor = Executor(mongo_db, "rcwd2", services={})
    fake_oid = __import__("bson").ObjectId()
    found = executor.request_cancel_with_deadline(fake_oid, deadline=1.0)
    assert found is False


async def test_write_force_cancelled_state_updates_active_process(mongo_db):
    """Conditional update flips active->failed and writes canonical error."""
    from optio_core._force_cancel import _write_force_cancelled_state
    from datetime import datetime, timezone
    from bson import ObjectId

    prefix = "wfcs"
    coll = mongo_db[f"{prefix}_processes"]
    oid = ObjectId()
    await coll.insert_one({
        "_id": oid,
        "processId": "p.fc",
        "name": "FC",
        "status": {"state": "running", "runningSince": datetime.now(timezone.utc)},
        "widgetUpstream": {"url": "http://x", "innerAuth": None},
        "log": [],
    })

    updated = await _write_force_cancelled_state(mongo_db, prefix, oid)
    assert updated is True
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "failed"
    assert "Task did not unwind within cancellation grace period" in doc["status"]["error"]
    assert doc["widgetUpstream"] is None


async def test_write_force_cancelled_state_no_op_on_terminal_process(mongo_db):
    """If the process is already terminal, the conditional update is a no-op."""
    from optio_core._force_cancel import _write_force_cancelled_state
    from datetime import datetime, timezone
    from bson import ObjectId

    prefix = "wfcs2"
    coll = mongo_db[f"{prefix}_processes"]
    oid = ObjectId()
    await coll.insert_one({
        "_id": oid,
        "processId": "p.done",
        "name": "Done",
        "status": {"state": "done", "doneAt": datetime.now(timezone.utc)},
        "widgetUpstream": None,
        "log": [],
    })

    updated = await _write_force_cancelled_state(mongo_db, prefix, oid)
    assert updated is False
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "done"


async def test_force_cancel_writes_failed_state_for_stubborn_task(mongo_db):
    """Stubborn task ignores the cooperative flag; force_cancel terminates it."""
    from optio_core.executor import Executor
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    prefix = "fc"
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        # Ignore the flag entirely — busy-await to give cancellation a hook.
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stubborn", params={}, execute=stubborn,
    )
    await upsert_process(mongo_db, prefix, task_inst)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks([task_inst])

    runner = asyncio.create_task(executor.launch_process("p.stub"))
    await started.wait()

    oid = next(iter(executor._running_tasks))
    await executor.force_cancel(oid)

    # Mongo state: failed with canonical error.
    coll = mongo_db[f"{prefix}_processes"]
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "failed"
    assert "Task did not unwind within cancellation grace period" in doc["status"]["error"]

    # The asyncio task is finished one way or another.
    with pytest.raises(asyncio.CancelledError):
        await runner


async def test_supervisor_force_cancels_past_deadline_entries(mongo_db):
    """A stubborn task whose deadline has passed is force-cancelled by the supervisor."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance
    import time as _time

    prefix = "supv"
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.supv", name="Supv", params={}, execute=stubborn,
    )

    async def gen(_services, _filter):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.supv")
        await started.wait()
        # Capture the inner asyncio.Task before force-cancel pops the registry.
        inner_oid = next(iter(optio._executor._running_tasks))
        inner_task = optio._executor._running_tasks[inner_oid]

        # Cancel — record the deadline.
        await optio.cancel("p.supv")

        # Within ~3s the supervisor should have force-cancelled.
        deadline = _time.monotonic() + 3.0
        while _time.monotonic() < deadline:
            proc = await optio.get_process("p.supv")
            if proc and proc["status"]["state"] == "failed":
                break
            await asyncio.sleep(0.1)

        proc = await optio.get_process("p.supv")
        assert proc["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in proc["status"]["error"]
        # Spec scenario 2: the asyncio Task object reports cancelled.
        assert inner_task.cancelled()
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_handle_cancel_records_deadline_on_running_process(mongo_db):
    """Calling cancel() on a running process records monotonic deadline."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance
    import time as _time

    prefix = "hcdl"
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(ctx):  # noqa: ARG001
        started.set()
        await release.wait()

    task_inst = TaskInstance(
        process_id="p.hold", name="Hold", params={}, execute=hold,
    )
    async def gen(_services, _filter):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=10.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.hold")
        await started.wait()

        before = _time.monotonic()
        await optio.cancel("p.hold")
        after = _time.monotonic()

        oid = next(iter(optio._executor._cancellation_flags))
        entry = optio._executor._cancellation_flags[oid]
        assert entry.flag.is_set()
        assert entry.deadline is not None
        # deadline is in the future, within [before+10, after+10] inclusive
        assert before + 10.0 - 0.5 <= entry.deadline <= after + 10.0 + 0.5
    finally:
        release.set()
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
