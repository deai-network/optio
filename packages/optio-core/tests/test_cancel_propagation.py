"""Tests for cancel propagation across the process tree.

Spec: docs/2026-05-11-cancel-propagation-design.md
"""
import asyncio
import time as _time

import pytest
from bson import ObjectId

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import (
    get_process_by_process_id, list_direct_children, upsert_process,
)


async def _wait_terminal(mongo_db, prefix: str, process_id: str, timeout: float = 5.0):
    """Poll until process_id reaches a terminal state or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await get_process_by_process_id(mongo_db, prefix, process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def test_cancel_parent_propagates_to_running_children(mongo_db):
    """cancel(parent) cancels running direct children when auto_cancel_children=True."""
    prefix = "p2t1"
    child_started = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        child_started.set()
        await ctx.run_child(
            execute=long_child,
            process_id="child",
            name="Child",
        )

    parent_task = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_task = TaskInstance(execute=long_child, process_id="child", name="Child")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_task)
    optio._executor.register_tasks([parent_task, child_task])

    runner = asyncio.create_task(optio.launch_and_wait("parent"))
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")
    await runner

    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    child_proc = await _wait_terminal(mongo_db, prefix, "child")
    assert parent_proc["status"]["state"] == "cancelled"
    assert child_proc["status"]["state"] == "cancelled"

    await optio.shutdown(grace_seconds=0.5)
