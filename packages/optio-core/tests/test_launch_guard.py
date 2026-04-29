"""Tests for the launch-guard mechanism."""

import asyncio
import pytest
from optio_core.models import LaunchBlocked, TaskInstance
from optio_core.lifecycle import Optio


def test_launch_blocked_is_runtime_error():
    """LaunchBlocked subclasses RuntimeError so generic except clauses still catch it."""
    err = LaunchBlocked("blocked by filter {'project': 'p1'}; metadata={'project': 'p1'}")
    assert isinstance(err, RuntimeError)
    assert "blocked by filter" in str(err)


def test_launch_blocked_exported_from_package():
    """LaunchBlocked is exported from the top-level optio_core package."""
    import optio_core
    assert optio_core.LaunchBlocked is LaunchBlocked


async def test_block_launches_registers_and_unregisters():
    """block_launches() adds a token to _launch_blocks on enter and removes it on exit."""
    optio = Optio()
    assert optio._launch_blocks == {}

    async with optio.block_launches({"project": "p1"}):
        # Inside: exactly one block registered with the given filter.
        assert len(optio._launch_blocks) == 1
        (token,) = optio._launch_blocks.keys()
        assert optio._launch_blocks[token] == {"project": "p1"}

    # After exit: dict is empty again.
    assert optio._launch_blocks == {}


async def test_block_launches_two_concurrent_same_filter():
    """Two simultaneous block_launches() with the same filter create two distinct tokens."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        async with optio.block_launches({"project": "p1"}):
            assert len(optio._launch_blocks) == 2
        # Inner exited; one block remains.
        assert len(optio._launch_blocks) == 1
    # Outer exited; empty.
    assert optio._launch_blocks == {}


async def test_block_launches_lifted_on_body_exception():
    """The block is removed even when the body raises."""
    optio = Optio()
    with pytest.raises(ValueError, match="boom"):
        async with optio.block_launches({"project": "p1"}):
            assert len(optio._launch_blocks) == 1
            raise ValueError("boom")
    # Block was lifted regardless of the exception.
    assert optio._launch_blocks == {}


async def test_block_launches_exported_from_package():
    """block_launches is exported from the top-level optio_core package."""
    import optio_core
    async with optio_core.block_launches({"project": "p1"}):
        # Uses the module-level _instance singleton.
        assert len(optio_core._instance._launch_blocks) == 1
    assert optio_core._instance._launch_blocks == {}


async def test_check_launch_blocks_passes_when_no_blocks_registered():
    """No registered blocks → check is a fast no-op."""
    optio = Optio()
    # Should not raise.
    optio._check_launch_blocks({"project": "p1"})


async def test_check_launch_blocks_raises_when_metadata_matches():
    """Registered block whose filter matches the metadata raises LaunchBlocked."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked, match="project"):
            optio._check_launch_blocks({"project": "p1", "sourceId": "s1"})


async def test_check_launch_blocks_passes_when_metadata_does_not_match():
    """Registered block whose filter does not match the metadata is a no-op."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        optio._check_launch_blocks({"project": "p2"})
        optio._check_launch_blocks({"unrelated": "x"})


async def test_check_launch_blocks_handles_none_metadata():
    """metadata=None is treated as empty dict — empty filter still matches."""
    optio = Optio()
    async with optio.block_launches({}):
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks(None)


async def test_check_launch_blocks_empty_filter_blocks_everything():
    """An empty filter `{}` matches every task metadata."""
    optio = Optio()
    async with optio.block_launches({}):
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks({"project": "p1"})
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks({"anything": "else"})


async def test_check_launch_blocks_message_includes_filter_and_metadata():
    """The LaunchBlocked message contains both the matching filter and the rejected metadata."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        try:
            optio._check_launch_blocks({"project": "p1", "sourceId": "s1"})
        except LaunchBlocked as e:
            msg = str(e)
            assert "p1" in msg
            assert "s1" in msg
            assert "project" in msg
        else:
            pytest.fail("LaunchBlocked not raised")


async def test_adhoc_define_blocked_when_metadata_matches(mongo_db):
    """adhoc_define raises LaunchBlocked for a task whose metadata matches a registered block.

    Critically, NO process record is created in Mongo — the check happens
    before any DB write.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="adhoc1",
        name="adhoc1",
        metadata={"project": "p1", "sourceId": "s1"},
    )

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.adhoc_define(task)

    # Verify no record was created.
    coll = mongo_db["test_processes"]
    assert await coll.find_one({"processId": "adhoc1"}) is None


async def test_adhoc_define_passes_when_metadata_does_not_match(mongo_db):
    """adhoc_define succeeds when the task metadata does not match any block."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="adhoc2",
        name="adhoc2",
        metadata={"project": "p2"},
    )

    async with optio.block_launches({"project": "p1"}):
        proc = await optio.adhoc_define(task)
        assert proc["processId"] == "adhoc2"


async def test_execute_child_blocked_when_parent_metadata_matches(mongo_db):
    """A parent task with matching metadata observes LaunchBlocked from run_child.

    Children inherit parent metadata; the block matches the parent's project
    so the run_child call is rejected.

    The parent is launched BEFORE the block is registered so that
    Optio.launch_and_wait does not itself raise LaunchBlocked.  An asyncio
    Event is used to make the parent hold off on spawning its child until the
    block is active, ensuring the run_child call is the one that gets rejected.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    observed = {}
    block_active = asyncio.Event()

    async def child_task(ctx):
        observed["child_ran"] = True

    async def parent_task(ctx):
        # Wait until the launch block is registered before attempting run_child.
        await block_active.wait()
        try:
            await ctx.run_child(
                execute=child_task,
                process_id="child1",
                name="child1",
                params={},
            )
        except LaunchBlocked as e:
            observed["blocked"] = str(e)

    parent = TaskInstance(
        execute=parent_task,
        process_id="parent1",
        name="parent1",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([parent])
    await optio.adhoc_define(parent)

    # Launch parent before the block is activated so launch_and_wait itself
    # does not raise (the block check at launch time would also reject it).
    parent_future = asyncio.create_task(
        optio._executor.launch_process("parent1")
    )

    async with optio.block_launches({"project": "p1"}):
        # Signal the running parent that the block is now in force.
        block_active.set()
        # Wait for the parent to finish (it will observe LaunchBlocked on run_child).
        await parent_future

    assert "blocked" in observed
    assert "child_ran" not in observed


async def test_launch_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch raises LaunchBlocked synchronously for a task whose metadata matches a block.

    The check happens before the asyncio Task is scheduled, so the caller
    observes the exception directly.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch1",
        name="launch1",
        metadata={"project": "p1"},
    )
    await optio.adhoc_define(task)

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.launch("launch1")


async def test_launch_and_wait_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch_and_wait raises LaunchBlocked for a blocked task."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch2",
        name="launch2",
        metadata={"project": "p1"},
    )
    await optio.adhoc_define(task)

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.launch_and_wait("launch2")


async def test_launch_passes_when_task_metadata_does_not_match(mongo_db):
    """Launches with non-matching metadata succeed normally."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch3",
        name="launch3",
        metadata={"project": "p2"},
    )
    await optio.adhoc_define(task)

    async with optio.block_launches({"project": "p1"}):
        # No exception; awaitable returns normally.
        await optio.launch_and_wait("launch3")


async def test_launch_passes_when_pid_unknown(mongo_db):
    """An unknown process_id has no metadata to match — falls through to the
    existing 'no execute function found' failure path inside the executor.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async with optio.block_launches({"project": "p1"}):
        # No raise; the launch is harmless because no record exists.
        await optio.launch("nope")
