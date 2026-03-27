"""Tests for child progress callback mechanism."""

import asyncio
import os
from unittest.mock import patch
from optio.models import TaskInstance
from optio.executor import Executor
from optio.store import upsert_process, get_process_by_process_id


async def test_run_child_progress_callback(mongo_db):
    """Parent receives child progress updates via on_child_progress callback."""
    received = []

    async def child_fn(ctx):
        ctx.report_progress(50, "halfway")
        await asyncio.sleep(0.15)  # allow throttled callback to fire
        ctx.report_progress(100, "done")
        await asyncio.sleep(0.15)

    async def parent_fn(ctx):
        def on_progress(children):
            received.append([(c.process_id, c.percent, c.message) for c in children])

        await ctx.run_child(
            child_fn, "cb_child", "CB Child",
            on_child_progress=on_progress,
        )

    task = TaskInstance(execute=parent_fn, process_id="cb_parent", name="CB Parent")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("cb_parent")

    assert len(received) >= 1
    # Last snapshot should show child at 100%
    last = received[-1]
    assert last[0][0] == "cb_child"
    assert last[0][1] == 100


async def test_parallel_group_progress_callback(mongo_db):
    """Parallel group fires on_child_progress with all children's progress."""
    received = []

    async def child_fn(ctx):
        ctx.report_progress(50)
        await asyncio.sleep(0.2)
        ctx.report_progress(100)
        await asyncio.sleep(0.15)

    async def parent_fn(ctx):
        def on_progress(children):
            received.append(len(children))

        async with ctx.parallel_group(on_child_progress=on_progress) as group:
            await group.spawn(child_fn, "pg_c1", "Child 1")
            await group.spawn(child_fn, "pg_c2", "Child 2")

    task = TaskInstance(execute=parent_fn, process_id="pg_parent", name="PG Parent")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("pg_parent")

    # Should have received callbacks with 1 child, then 2 children
    assert any(n == 2 for n in received)


async def test_child_completion_fires_callback_immediately(mongo_db):
    """When a child completes, the callback fires immediately (not throttled)."""
    completion_states = []

    async def child_fn(ctx):
        ctx.report_progress(100)

    async def parent_fn(ctx):
        def on_progress(children):
            for c in children:
                if c.state == "done":
                    completion_states.append(c.state)

        await ctx.run_child(
            child_fn, "imm_child", "Immediate Child",
            on_child_progress=on_progress,
        )

    task = TaskInstance(execute=parent_fn, process_id="imm_parent", name="Imm Parent")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("imm_parent")

    assert "done" in completion_states


async def test_configurable_flush_interval(mongo_db):
    """DB flush interval reads from OPTIO_PROGRESS_FLUSH_INTERVAL_MS."""
    observed_interval = {}

    async def task_fn(ctx):
        observed_interval["value"] = ctx._flush_interval

    task = TaskInstance(execute=task_fn, process_id="flush_cfg", name="FlushCfg")
    await upsert_process(mongo_db, "test", task)

    with patch.dict(os.environ, {"OPTIO_PROGRESS_FLUSH_INTERVAL_MS": "50"}):
        executor = Executor(mongo_db, "test", {})
        executor.register_tasks([task])
        result = await executor.launch_process("flush_cfg")

    assert result == "done"
    assert observed_interval["value"] == 0.05
