"""Tests for parallel child execution."""

import asyncio
from feldwebel.models import TaskInstance
from feldwebel.executor import Executor
from feldwebel.store import upsert_process, get_process_by_process_id


async def test_parallel_basic(mongo_db):
    order = []

    async def child(ctx):
        order.append(f"start_{ctx.process_id}")
        await asyncio.sleep(0.05)
        order.append(f"end_{ctx.process_id}")

    async def parent(ctx):
        async with ctx.parallel_group(max_concurrency=3) as group:
            for i in range(3):
                await group.spawn(child, f"p_child_{i}", f"Child {i}")

    task = TaskInstance(execute=parent, process_id="par", name="Parallel")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("par")
    assert result == "done"
    starts = [x for x in order if x.startswith("start_")]
    assert len(starts) == 3


async def test_parallel_concurrency_limit(mongo_db):
    running = {"count": 0, "max": 0}

    async def child(ctx):
        running["count"] += 1
        running["max"] = max(running["max"], running["count"])
        await asyncio.sleep(0.05)
        running["count"] -= 1

    async def parent(ctx):
        async with ctx.parallel_group(max_concurrency=2) as group:
            for i in range(5):
                await group.spawn(child, f"lim_child_{i}", f"Child {i}")

    task = TaskInstance(execute=parent, process_id="lim", name="Limited")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("lim")
    assert result == "done"
    assert running["max"] <= 2


async def test_parallel_survive_failure(mongo_db):
    async def good_child(ctx):
        await asyncio.sleep(0.01)

    async def bad_child(ctx):
        raise Exception("boom")

    async def parent(ctx):
        async with ctx.parallel_group(max_concurrency=3, survive_failure=True) as group:
            await group.spawn(good_child, "good_1", "Good 1")
            await group.spawn(bad_child, "bad_1", "Bad 1")
            await group.spawn(good_child, "good_2", "Good 2")
        done_count = sum(1 for r in group.results if r.state == "done")
        assert done_count == 2

    task = TaskInstance(execute=parent, process_id="surv", name="Survive")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("surv")
    assert result == "done"


async def test_parallel_failure_propagates(mongo_db):
    async def bad_child(ctx):
        raise Exception("boom")

    async def parent(ctx):
        async with ctx.parallel_group(max_concurrency=3, survive_failure=False) as group:
            await group.spawn(bad_child, "fail_child", "Fail")

    task = TaskInstance(execute=parent, process_id="prop", name="Propagate")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("prop")
    assert result == "failed"
