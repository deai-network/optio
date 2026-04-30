"""Tests for group_cancel / group_cancel_and_wait.

Spec: docs/2026-04-30-group-cancel-design.md
"""
import asyncio
import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


async def _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=2.0):
    """Helper: init an Optio with the given tasks, return (optio, run_task)."""
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run())
    return optio, run_task

async def _stop_optio(optio, run_task):
    await optio.shutdown()
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass


# ---------- Filter validation (no Mongo needed) ----------

@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel(bad_filter, block_new_launches=block_new_launches)


@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_and_wait_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel_and_wait raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel_and_wait(bad_filter, block_new_launches=block_new_launches)


# ---------- Snapshot + parallel cancel ----------

async def test_group_cancel_only_cancels_in_scope(mongo_db):
    """group_cancel cancels tasks that match the filter and leaves others alone."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release_b = asyncio.Event()

    async def cooperative_a(ctx):
        started_a.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def cooperative_b(ctx):  # noqa: ARG001
        started_b.set()
        await release_b.wait()

    task_a = TaskInstance(
        process_id="p.a", name="A", params={}, execute=cooperative_a,
        metadata={"team": "alpha"},
    )
    task_b = TaskInstance(
        process_id="p.b", name="B", params={}, execute=cooperative_b,
        metadata={"team": "beta"},
    )

    optio, run_task = await _start_optio(mongo_db, "gc_scope", [task_a, task_b])
    try:
        await optio.launch("p.a")
        await optio.launch("p.b")
        await started_a.wait()
        await started_b.wait()

        await optio.group_cancel({"team": "alpha"})

        # Wait long enough for cooperative_a to observe and unwind.
        await asyncio.sleep(0.25)

        proc_a = await optio.get_process("p.a")
        proc_b = await optio.get_process("p.b")
        assert proc_a["status"]["state"] == "cancelled"
        assert proc_b["status"]["state"] == "running"

        release_b.set()
        await asyncio.sleep(0.1)
    finally:
        await _stop_optio(optio, run_task)
