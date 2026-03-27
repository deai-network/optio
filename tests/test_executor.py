"""Tests for task executor — sequential child execution, cancellation, failure handling."""

import asyncio
from feldwebel.models import TaskInstance
from feldwebel.executor import Executor
from feldwebel.lifecycle import Feldwebel
from feldwebel.store import upsert_process, get_process_by_process_id


async def test_launch_basic_process(mongo_db):
    async def my_task(ctx):
        ctx.report_progress(50, "Working...")
        await asyncio.sleep(0.1)
        ctx.report_progress(100, "Done")

    task = TaskInstance(execute=my_task, process_id="basic", name="Basic")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("basic")
    assert result == "done"

    proc = await get_process_by_process_id(mongo_db, "test", "basic")
    assert proc["status"]["state"] == "done"
    assert proc["status"]["doneAt"] is not None
    assert proc["status"]["duration"] is not None


async def test_launch_failing_process(mongo_db):
    async def failing_task(ctx):
        raise Exception("Something broke")

    task = TaskInstance(execute=failing_task, process_id="fail", name="Failing")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("fail")
    assert result == "failed"

    proc = await get_process_by_process_id(mongo_db, "test", "fail")
    assert proc["status"]["state"] == "failed"
    assert proc["status"]["error"] == "Something broke"


async def test_cooperative_cancellation(mongo_db):
    async def cancellable_task(ctx):
        for i in range(100):
            if not ctx.should_continue():
                return
            await asyncio.sleep(0.01)

    task = TaskInstance(execute=cancellable_task, process_id="cancel_me", name="Cancellable")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    async def cancel_after_delay():
        await asyncio.sleep(0.05)
        proc = await get_process_by_process_id(mongo_db, "test", "cancel_me")
        executor.request_cancel(proc["_id"])

    result, _ = await asyncio.gather(
        executor.launch_process("cancel_me"),
        cancel_after_delay(),
    )
    assert result == "cancelled"

    proc = await get_process_by_process_id(mongo_db, "test", "cancel_me")
    assert proc["status"]["state"] == "cancelled"


async def test_sequential_child_execution(mongo_db):
    results = []

    async def child_task(ctx):
        results.append(ctx.process_id)
        ctx.report_progress(100, "Child done")

    async def parent_task(ctx):
        await ctx.run_child(child_task, "child_1", "Child 1", {"step": 1})
        await ctx.run_child(child_task, "child_2", "Child 2", {"step": 2})

    task = TaskInstance(execute=parent_task, process_id="parent", name="Parent")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("parent")
    assert result == "done"
    assert results == ["child_1", "child_2"]

    child1 = await get_process_by_process_id(mongo_db, "test", "child_1")
    assert child1 is not None
    assert child1["status"]["state"] == "done"
    assert child1["depth"] == 1


async def test_child_failure_propagates(mongo_db):
    async def failing_child(ctx):
        raise Exception("Child failed")

    async def parent_task(ctx):
        await ctx.run_child(failing_child, "bad_child", "Bad Child")

    task = TaskInstance(execute=parent_task, process_id="parent_fail", name="Parent")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("parent_fail")
    assert result == "failed"


async def test_child_failure_survived(mongo_db):
    async def failing_child(ctx):
        raise Exception("Child failed")

    async def parent_task(ctx):
        result = await ctx.run_child(
            failing_child, "bad_child2", "Bad Child",
            survive_failure=True,
        )
        assert result == "failed"
        ctx.report_progress(100, "Parent survived")

    task = TaskInstance(execute=parent_task, process_id="survive", name="Survivor")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("survive")
    assert result == "done"


async def test_idempotent_launch(mongo_db):
    """Launching an already running process returns None (ignored)."""
    async def long_task(ctx):
        await asyncio.sleep(10)

    task = TaskInstance(execute=long_task, process_id="idem", name="Idempotent")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    # Launch in background
    launch_task = asyncio.create_task(executor.launch_process("idem"))
    await asyncio.sleep(0.05)

    # Try to launch again — should return None (already running)
    result2 = await executor.launch_process("idem")
    assert result2 is None

    # Clean up
    proc = await get_process_by_process_id(mongo_db, "test", "idem")
    executor.request_cancel(proc["_id"])
    await launch_task


async def test_lifecycle_log_entries(mongo_db):
    """Process execution writes log entries for state transitions and progress messages."""
    async def my_task(ctx):
        ctx.report_progress(50, "Halfway there")
        await asyncio.sleep(1.1)  # wait for progress flush
        ctx.report_progress(100)  # no message — should NOT produce a log entry

    task = TaskInstance(execute=my_task, process_id="loglife", name="Lifecycle")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("loglife")

    proc = await get_process_by_process_id(mongo_db, "test", "loglife")
    messages = [e["message"] for e in proc["log"]]

    # Should have: scheduled, running, progress message, done
    assert "State changed to scheduled" in messages
    assert "State changed to running" in messages
    assert "Halfway there" in messages
    assert "State changed to done" in messages

    # "100%" progress without message should NOT appear
    assert not any("100" in m for m in messages if "State" not in m)


async def test_child_spawn_and_failure_log_entries(mongo_db):
    """Parent logs child spawn; failed child logs error."""
    async def failing_child(ctx):
        raise Exception("Child broke")

    async def parent_task(ctx):
        await ctx.run_child(failing_child, "bad", "Bad Child", survive_failure=True)

    task = TaskInstance(execute=parent_task, process_id="logparent", name="Parent")
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("logparent")

    parent = await get_process_by_process_id(mongo_db, "test", "logparent")
    parent_msgs = [e["message"] for e in parent["log"]]
    assert "Spawned child: Bad Child" in parent_msgs

    child = await get_process_by_process_id(mongo_db, "test", "bad")
    child_msgs = [e["message"] for e in child["log"]]
    assert "Child broke" in child_msgs
    assert any(e["level"] == "error" for e in child["log"])


async def test_child_inherits_parent_metadata(mongo_db):
    """Child process should receive parent's metadata."""
    child_metadata = {}

    async def child_task(ctx):
        nonlocal child_metadata
        child_metadata = ctx.metadata

    async def parent_task(ctx):
        await ctx.run_child(child_task, "meta_child", "Meta Child")

    task = TaskInstance(
        execute=parent_task, process_id="meta_parent", name="Meta Parent",
        metadata={"targetId": "source_99"},
    )
    await upsert_process(mongo_db, "test", task)

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("meta_parent")
    assert result == "done"
    assert child_metadata == {"targetId": "source_99"}

    # Also verify it's persisted in the DB
    child = await get_process_by_process_id(mongo_db, "test", "meta_child")
    assert child["metadata"] == {"targetId": "source_99"}


async def test_adhoc_define_root(mongo_db, redis_url):
    """adhoc_define creates a root process with adhoc=True, registers in task registry."""
    async def my_task(ctx):
        ctx.report_progress(100, "Done")

    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="adhoc_test", redis_url=redis_url)

    task = TaskInstance(execute=my_task, process_id="adhoc_root", name="Ad-hoc Root")
    await fw.adhoc_define(task)

    proc = await get_process_by_process_id(mongo_db, "adhoc_test", "adhoc_root")
    assert proc is not None
    assert proc["adhoc"] is True
    assert proc["ephemeral"] is False
    assert proc["status"]["state"] == "idle"
    assert proc["depth"] == 0
    assert proc["parentId"] is None

    # Should be launchable
    result = await fw._executor.launch_process("adhoc_root")
    assert result == "done"

    await fw.shutdown()


async def test_adhoc_define_child(mongo_db, redis_url):
    """adhoc_define with parent_id creates a child process with correct depth and rootId."""
    async def my_task(ctx):
        pass

    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="adhoc_test2", redis_url=redis_url)

    # Create a parent
    parent_task = TaskInstance(execute=my_task, process_id="parent", name="Parent")
    parent = await upsert_process(mongo_db, "adhoc_test2", parent_task)

    child_task = TaskInstance(execute=my_task, process_id="adhoc_child", name="Ad-hoc Child")
    await fw.adhoc_define(child_task, parent_id=parent["_id"])

    child = await get_process_by_process_id(mongo_db, "adhoc_test2", "adhoc_child")
    assert child is not None
    assert child["adhoc"] is True
    assert child["depth"] == 1
    assert child["parentId"] == parent["_id"]
    assert child["rootId"] == parent["rootId"]

    await fw.shutdown()


async def test_ephemeral_process_deleted_after_completion(mongo_db, redis_url):
    """A process defined with ephemeral=True is deleted after reaching done state."""
    async def my_task(ctx):
        ctx.report_progress(100, "Done")

    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="eph_test", redis_url=redis_url)

    task = TaskInstance(execute=my_task, process_id="eph_done", name="Ephemeral Done")
    await fw.adhoc_define(task, ephemeral=True)

    result = await fw._executor.launch_process("eph_done")
    assert result == "done"

    # Process should be deleted
    proc = await get_process_by_process_id(mongo_db, "eph_test", "eph_done")
    assert proc is None

    # Should be removed from task registry
    assert "eph_done" not in fw._executor._task_registry

    await fw.shutdown()


async def test_mark_ephemeral_during_execution(mongo_db, redis_url):
    """ctx.mark_ephemeral() causes deletion after completion."""
    async def my_task(ctx):
        await ctx.mark_ephemeral()
        ctx.report_progress(100, "Done")

    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="mark_eph_test", redis_url=redis_url)

    task = TaskInstance(execute=my_task, process_id="mark_eph", name="Mark Ephemeral")
    await fw.adhoc_define(task)  # ephemeral=False at define time

    result = await fw._executor.launch_process("mark_eph")
    assert result == "done"

    # Process should be deleted (mark_ephemeral set it during execution)
    proc = await get_process_by_process_id(mongo_db, "mark_eph_test", "mark_eph")
    assert proc is None

    await fw.shutdown()


async def test_adhoc_define_ephemeral(mongo_db, redis_url):
    """adhoc_define with ephemeral=True sets the flag on the process."""
    async def my_task(ctx):
        pass

    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="adhoc_test3", redis_url=redis_url)

    task = TaskInstance(execute=my_task, process_id="adhoc_eph", name="Ephemeral")
    await fw.adhoc_define(task, ephemeral=True)

    proc = await get_process_by_process_id(mongo_db, "adhoc_test3", "adhoc_eph")
    assert proc["ephemeral"] is True

    await fw.shutdown()
