"""Tests for Redis-free mode."""

import asyncio

import pytest
from optio_core.lifecycle import Optio
from optio_core.store import get_process_by_process_id
from optio_core.models import TaskInstance, CancellationConfig


@pytest.mark.asyncio
async def test_init_without_redis(mongo_db):
    """Init succeeds without redis_url."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_no_redis")

    assert fw._config is not None
    assert fw._config.redis_url is None
    assert fw._redis is None
    assert fw._consumer is None
    assert fw._executor is not None
    assert fw._scheduler is not None


@pytest.mark.asyncio
async def test_run_and_shutdown_without_redis(mongo_db):
    """run() blocks until shutdown() is called, without Redis."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_no_redis_run")

    shutdown_called = False

    async def shutdown_after_delay():
        nonlocal shutdown_called
        await asyncio.sleep(0.2)
        shutdown_called = True
        await fw.shutdown()

    asyncio.create_task(shutdown_after_delay())
    await fw.run()

    assert shutdown_called


async def _dummy_task(ctx):
    ctx.report_progress(50, "halfway")
    ctx.report_progress(100, "done")


async def _slow_task(ctx):
    for i in range(10):
        if not ctx.should_continue():
            return
        await asyncio.sleep(0.05)
        ctx.report_progress((i + 1) * 10, f"step {i + 1}")


async def _get_tasks(services):
    return [
        TaskInstance(execute=_dummy_task, process_id="test_task", name="Test Task"),
        TaskInstance(execute=_slow_task, process_id="slow_task", name="Slow Task",
                     cancellation=CancellationConfig(cancellable=True)),
    ]


@pytest.mark.asyncio
async def test_launch_and_wait(mongo_db):
    """launch_and_wait() runs process to completion."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_direct",
                  get_task_definitions=_get_tasks)

    await fw.launch_and_wait("test_task")

    proc = await get_process_by_process_id(mongo_db, "test_direct", "test_task")
    assert proc["status"]["state"] == "done"


@pytest.mark.asyncio
async def test_launch_fire_and_forget(mongo_db):
    """launch() returns immediately, process runs in background."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_fire",
                  get_task_definitions=_get_tasks)

    await fw.launch("slow_task")

    # Give the background task a moment to start
    await asyncio.sleep(0.1)

    proc = await get_process_by_process_id(mongo_db, "test_fire", "slow_task")
    assert proc["status"]["state"] == "running"

    # Wait for it to finish
    await asyncio.sleep(1)
    proc = await get_process_by_process_id(mongo_db, "test_fire", "slow_task")
    assert proc["status"]["state"] == "done"


@pytest.mark.asyncio
async def test_cancel(mongo_db):
    """cancel() stops a running process."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_cancel_direct",
                  get_task_definitions=_get_tasks)

    await fw.launch("slow_task")
    await asyncio.sleep(0.1)

    await fw.cancel("slow_task")
    await asyncio.sleep(0.5)

    proc = await get_process_by_process_id(mongo_db, "test_cancel_direct", "slow_task")
    assert proc["status"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_dismiss(mongo_db):
    """dismiss() resets a completed process to idle."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_dismiss_direct",
                  get_task_definitions=_get_tasks)

    await fw.launch_and_wait("test_task")
    proc = await get_process_by_process_id(mongo_db, "test_dismiss_direct", "test_task")
    assert proc["status"]["state"] == "done"

    await fw.dismiss("test_task")
    proc = await get_process_by_process_id(mongo_db, "test_dismiss_direct", "test_task")
    assert proc["status"]["state"] == "idle"


@pytest.mark.asyncio
async def test_resync(mongo_db):
    """resync() re-syncs task definitions."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_resync_direct",
                  get_task_definitions=_get_tasks)

    # Verify tasks exist
    proc = await get_process_by_process_id(mongo_db, "test_resync_direct", "test_task")
    assert proc is not None

    await fw.resync()

    # Still exists after resync
    proc = await get_process_by_process_id(mongo_db, "test_resync_direct", "test_task")
    assert proc is not None


@pytest.mark.asyncio
async def test_on_command_raises_without_redis(mongo_db):
    """on_command() raises when Redis is not configured."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_no_cmd")

    with pytest.raises(RuntimeError, match="Custom commands require Redis"):
        fw.on_command("test", lambda p: None)


@pytest.mark.asyncio
async def test_get_process(mongo_db):
    """get_process() returns a process by process_id."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_get",
                  get_task_definitions=_get_tasks)

    proc = await fw.get_process("test_task")
    assert proc is not None
    assert proc["processId"] == "test_task"
    assert proc["name"] == "Test Task"
    assert proc["status"]["state"] == "idle"


@pytest.mark.asyncio
async def test_get_process_not_found(mongo_db):
    """get_process() returns None for unknown process_id."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_get_missing",
                  get_task_definitions=_get_tasks)

    proc = await fw.get_process("nonexistent")
    assert proc is None


@pytest.mark.asyncio
async def test_list_processes_no_filter(mongo_db):
    """list_processes() returns all processes."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_list",
                  get_task_definitions=_get_tasks)

    procs = await fw.list_processes()
    assert len(procs) == 2
    names = {p["name"] for p in procs}
    assert names == {"Test Task", "Slow Task"}


@pytest.mark.asyncio
async def test_list_processes_filter_state(mongo_db):
    """list_processes(state=...) filters by state."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_list_state",
                  get_task_definitions=_get_tasks)

    procs = await fw.list_processes(state="idle")
    assert len(procs) == 2

    procs = await fw.list_processes(state="running")
    assert len(procs) == 0

    await fw.launch_and_wait("test_task")
    procs = await fw.list_processes(state="done")
    assert len(procs) == 1
    assert procs[0]["processId"] == "test_task"
