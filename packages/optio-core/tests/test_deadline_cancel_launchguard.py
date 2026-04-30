"""Integration tests for deadline-cancel × launch-guard.

Spec: docs/2026-04-30-deadline-cancel-launchguard-integration-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import LaunchBlocked, TaskInstance


pytestmark = pytest.mark.asyncio


async def test_child_launchblocked_propagates_and_parent_cancellable(mongo_db):
    """A parent that handles LaunchBlocked from a child remains cancellable.

    Verifies:
    - LaunchBlocked raises out of run_child as a normal exception
    - The blocked child never enters _cancellation_flags / _running_tasks
    - cancel/cancel_and_wait on the parent reaches a clean terminal state
    - _launch_blocks is empty after the test
    """
    prefix = "intg1"

    parent_started = asyncio.Event()
    block_active = asyncio.Event()
    child_block_observed = asyncio.Event()
    parent_done = asyncio.Event()

    async def child(ctx):  # noqa: ARG001
        return  # never reached when blocked

    async def parent(ctx):
        parent_started.set()
        # Wait until the block is registered before attempting run_child,
        # so that run_child is the call that gets rejected (not the launch).
        await block_active.wait()
        try:
            await ctx.run_child(
                execute=child,
                process_id="p.child",
                name="Child",
                params={},
            )
        except LaunchBlocked:
            child_block_observed.set()

        # Cooperate with cancel after the block has been observed.
        for _ in range(100):
            if ctx.cancellation_flag.is_set():
                parent_done.set()
                return
            await asyncio.sleep(0.05)
        parent_done.set()

    parent_task = TaskInstance(
        process_id="p.parent", name="Parent", params={},
        execute=parent, metadata={"kind": "parent"},
    )

    async def gen(_s, _f):
        return [parent_task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        # Launch the parent before registering the block so that
        # optio.launch itself is not rejected.
        await optio.launch("p.parent")
        await parent_started.wait()

        # Block launches whose metadata matches kind=parent — this is the
        # metadata that execute_child passes to _check_launch_blocks
        # (it uses parent_ctx.metadata, which is the parent's metadata).
        async with optio.block_launches({"kind": "parent"}):
            # Signal the running parent that the block is now in force,
            # so run_child will be rejected.
            block_active.set()
            # Issue cancel; this also nudges the cooperative loop.
            state = await optio.cancel_and_wait("p.parent")
            assert state == "cancelled"

        # After the block context exits, _launch_blocks must be empty.
        assert optio._launch_blocks == {}

        # Registries should be empty for parent (cooperative cancel) and
        # the would-be child should never have appeared.
        assert optio._executor._cancellation_flags == {}
        assert optio._executor._running_tasks == {}
        # Parent observed the LaunchBlocked exception.
        assert child_block_observed.is_set()
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
