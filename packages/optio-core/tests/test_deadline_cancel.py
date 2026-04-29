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


async def test_cancel_and_wait_cooperative(mongo_db):
    """Cooperative task ends 'cancelled'; cancel_and_wait returns 'cancelled'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "caw1"

    async def cooperative(ctx):
        # Honour the flag promptly.
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.coop", name="Coop", params={}, execute=cooperative,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.coop")
        await asyncio.sleep(0.2)  # let it transition to running
        state = await optio.cancel_and_wait("p.coop")
        assert state == "cancelled"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_stubborn_returns_failed(mongo_db):
    """Stubborn task force-cancelled; cancel_and_wait returns 'failed'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "caw2"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)
        state = await optio.cancel_and_wait("p.stub")
        assert state == "failed"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_returns_none_for_missing(mongo_db):
    from optio_core.lifecycle import Optio
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cawnone")
    state = await optio.cancel_and_wait("nope.does.not.exist")
    assert state is None


async def test_cancel_and_wait_short_circuits_for_already_terminal(mongo_db):
    """A done/failed/cancelled process returns its state immediately."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "cawterm"

    async def quick(ctx):  # noqa: ARG001
        return

    task_inst = TaskInstance(
        process_id="p.quick", name="Quick", params={}, execute=quick,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=gen,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch_and_wait("p.quick")
        state = await optio.cancel_and_wait("p.quick")
        assert state == "done"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_raises_timeout_when_force_cancel_neutered(mongo_db):
    """If force_cancel never converges, the internal ceiling fires."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "cawto"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.2,
    )

    # Patch force_cancel to be a no-op so the supervisor never actually
    # transitions the task. The ceiling should fire.
    async def _noop(_oid):
        return
    optio._executor.force_cancel = _noop  # type: ignore[assignment]

    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)
        with pytest.raises(asyncio.TimeoutError):
            await optio.cancel_and_wait("p.stub")
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


async def test_shutdown_finalizes_mixed_cooperative_and_stubborn_tasks(mongo_db):
    """Mixed tasks: cooperators -> 'cancelled', stubborn -> 'failed'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "shutmix"

    async def cooperative(ctx):
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.05)

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    tasks = [
        TaskInstance(process_id="p.coop", name="Coop", params={}, execute=cooperative),
        TaskInstance(process_id="p.stub", name="Stub", params={}, execute=stubborn),
    ]
    async def gen(_s, _f):
        return tasks

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.4,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.coop")
        await optio.launch("p.stub")
        await asyncio.sleep(0.3)

        await optio.shutdown()

        coop = await optio.get_process("p.coop")
        stub = await optio.get_process("p.stub")
        assert coop["status"]["state"] == "cancelled"
        assert stub["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in stub["status"]["error"]
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_shutdown_grace_seconds_override_honoured(mongo_db):
    """shutdown(grace_seconds=X) overrides the configured default for that call."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "shutov"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=10.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.3)

        # Even though the config grace is 10s, override to 0.3s.
        import time as _time
        t0 = _time.monotonic()
        await optio.shutdown(grace_seconds=0.3)
        elapsed = _time.monotonic() - t0
        # Should finish well under 10 seconds.
        assert elapsed < 6.0

        proc = await optio.get_process("p.stub")
        assert proc["status"]["state"] == "failed"
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_re_entry_idempotency_does_not_refresh_deadline(mongo_db):
    """Two cancel() calls 1s apart: deadline set by the first stays in force."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "reidem"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=1.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)

        await optio.cancel("p.stub")
        oid = next(iter(optio._executor._cancellation_flags))
        first_deadline = optio._executor._cancellation_flags[oid].deadline

        await asyncio.sleep(1.0)  # past the first deadline; supervisor may fire
        # Refresh the entry pointer; it may already be gone if force-cancelled.
        # Either way, calling cancel again must not raise and must be a no-op
        # on the deadline (if entry still exists).
        await optio.cancel("p.stub")
        if oid in optio._executor._cancellation_flags:
            assert optio._executor._cancellation_flags[oid].deadline == first_deadline

        # Eventually terminal.
        import time as _time
        ceil = _time.monotonic() + 4.0
        while _time.monotonic() < ceil:
            proc = await optio.get_process("p.stub")
            if proc and proc["status"]["state"] in ("failed", "cancelled"):
                break
            await asyncio.sleep(0.1)
        proc = await optio.get_process("p.stub")
        assert proc["status"]["state"] == "failed"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_to_thread_blocked_task_reaches_failed_state(mongo_db):
    """Task blocked inside asyncio.to_thread: Mongo state still goes to 'failed'.

    The thread is allowed to outlive the test. We use a short sleep so the
    underlying thread terminates before pytest tears down the event loop.
    """
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "thrblk"

    def _block_briefly() -> None:
        import time as _t
        _t.sleep(2.0)

    async def thread_blocked(ctx):  # noqa: ARG001
        await asyncio.to_thread(_block_briefly)

    task_inst = TaskInstance(
        process_id="p.thr", name="Thr", params={}, execute=thread_blocked,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.thr")
        await asyncio.sleep(0.2)

        await optio.cancel("p.thr")

        import time as _time
        ceil = _time.monotonic() + 4.0
        while _time.monotonic() < ceil:
            proc = await optio.get_process("p.thr")
            if proc and proc["status"]["state"] == "failed":
                break
            await asyncio.sleep(0.1)
        proc = await optio.get_process("p.thr")
        assert proc["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in proc["status"]["error"]
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
        # Let the orphaned thread finish before the loop tears down.
        await asyncio.sleep(2.5)
