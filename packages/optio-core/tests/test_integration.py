"""Integration test — full optio lifecycle."""

import asyncio
import json
from redis.asyncio import Redis
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.models import TaskInstance
from optio_core.lifecycle import Optio
from optio_core.store import get_process_by_process_id


async def test_full_lifecycle():
    """Test init → launch via Redis → execute → verify DB state."""
    mongo_client = AsyncIOMotorClient("mongodb://localhost:27017")
    db_name = f"optio_inttest_{id(asyncio.get_event_loop())}"
    db = mongo_client[db_name]
    redis_url = "redis://localhost:6379"
    prefix = "inttest"

    execution_log = []

    async def my_task(ctx):
        execution_log.append("started")
        ctx.report_progress(50, "Half done")
        await asyncio.sleep(0.1)
        ctx.report_progress(100, "Complete")
        execution_log.append("finished")

    async def my_failing_task(ctx):
        raise Exception("Intentional failure")

    async def get_tasks(services):
        return [
            TaskInstance(execute=my_task, process_id="good_task", name="Good Task"),
            TaskInstance(execute=my_failing_task, process_id="bad_task", name="Bad Task"),
        ]

    fw = Optio()
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"mongo": db},
        get_task_definitions=get_tasks,
    )

    # Verify processes created in DB
    good = await get_process_by_process_id(db, prefix, "good_task")
    assert good is not None
    assert good["status"]["state"] == "idle"

    bad = await get_process_by_process_id(db, prefix, "bad_task")
    assert bad is not None

    # Launch via Redis Stream
    redis = Redis.from_url(redis_url)
    await redis.xadd(
        f"{prefix}:commands",
        {"type": "launch", "payload": json.dumps({"processId": "good_task"})},
    )
    await redis.xadd(
        f"{prefix}:commands",
        {"type": "launch", "payload": json.dumps({"processId": "bad_task"})},
    )

    # Run briefly
    run_task = asyncio.create_task(fw.run())
    await asyncio.sleep(3)
    await fw.shutdown()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    # Verify good task completed
    good = await get_process_by_process_id(db, prefix, "good_task")
    assert good["status"]["state"] == "done"
    assert "started" in execution_log
    assert "finished" in execution_log

    # Verify bad task failed
    bad = await get_process_by_process_id(db, prefix, "bad_task")
    assert bad["status"]["state"] == "failed"
    assert bad["status"]["error"] == "Intentional failure"

    # Cleanup
    await redis.aclose()
    await mongo_client.drop_database(db_name)
    mongo_client.close()


async def test_child_process_tree():
    """Test that parent-child process trees work end-to-end."""
    mongo_client = AsyncIOMotorClient("mongodb://localhost:27017")
    db_name = f"optio_tree_{id(asyncio.get_event_loop())}"
    db = mongo_client[db_name]
    redis_url = "redis://localhost:6379"
    prefix = "treetest"

    async def child_task(ctx):
        ctx.report_progress(100, f"Child {ctx.process_id} done")

    async def parent_task(ctx):
        ctx.report_progress(10, "Starting children...")
        await ctx.run_child(child_task, "child_a", "Child A")
        ctx.report_progress(50, "First child done")

        async with ctx.parallel_group(max_concurrency=2) as group:
            await group.spawn(child_task, "child_b", "Child B")
            await group.spawn(child_task, "child_c", "Child C")

        ctx.report_progress(100, "All children done")

    async def get_tasks(services):
        return [
            TaskInstance(execute=parent_task, process_id="parent", name="Parent Task"),
        ]

    fw = Optio()
    await fw.init(
        mongo_db=db, prefix=prefix, redis_url=redis_url,
        services={"mongo": db}, get_task_definitions=get_tasks,
    )

    redis = Redis.from_url(redis_url)
    await redis.xadd(
        f"{prefix}:commands",
        {"type": "launch", "payload": json.dumps({"processId": "parent"})},
    )

    run_task = asyncio.create_task(fw.run())
    await asyncio.sleep(3)
    await fw.shutdown()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    # Verify parent
    parent = await get_process_by_process_id(db, prefix, "parent")
    assert parent["status"]["state"] == "done"

    # Verify children exist and completed
    child_a = await get_process_by_process_id(db, prefix, "child_a")
    assert child_a is not None
    assert child_a["status"]["state"] == "done"
    assert child_a["parentId"] == parent["_id"]
    assert child_a["depth"] == 1

    child_b = await get_process_by_process_id(db, prefix, "child_b")
    assert child_b is not None
    assert child_b["status"]["state"] == "done"

    child_c = await get_process_by_process_id(db, prefix, "child_c")
    assert child_c is not None
    assert child_c["status"]["state"] == "done"

    await redis.aclose()
    await mongo_client.drop_database(db_name)
    mongo_client.close()
