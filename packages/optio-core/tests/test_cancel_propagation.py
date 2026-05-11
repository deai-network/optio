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


async def test_cancel_optout_does_not_auto_cancel_children(mongo_db):
    """Parent with auto_cancel_children=False keeps children running until
    its own execute fn cancels them or force-cancel cascade catches them."""
    prefix = "p2t3"
    child_started = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(
                execute=long_child, process_id="child", name="Child",
            )
            child_started.set()
            while ctx.should_continue():
                await asyncio.sleep(0.01)

    parent_task = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    child_task = TaskInstance(execute=long_child, process_id="child", name="Child")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=1.0)
    await upsert_process(mongo_db, prefix, parent_task)
    optio._executor.register_tasks([parent_task, child_task])

    runner = asyncio.create_task(optio.launch_and_wait("parent"))
    await child_started.wait()
    await asyncio.sleep(0.1)

    await optio.cancel("parent")

    # Immediately after cancel: child should still be active because
    # parent opted out.
    child_proc = await get_process_by_process_id(mongo_db, prefix, "child")
    assert child_proc["status"]["state"] in {"running", "scheduled"}, (
        f"opt-out parent should not auto-cancel children, "
        f"got child state={child_proc['status']['state']}"
    )

    # Cleanup: cancel child directly so parent can exit. The full
    # opt-out terminal behavior is verified in Phase 5 (force-cancel
    # cascade) and Phase 6 (orphan safety net).
    await optio.cancel("child")
    await asyncio.wait_for(runner, timeout=5.0)
    await optio.shutdown(grace_seconds=0.5)


async def test_cancel_recursion_honors_per_level_optout(mongo_db):
    """A->B->C where B opts out. cancel(A) cancels A and B; C remains active."""
    prefix = "p2t4"
    a_running = asyncio.Event()
    b_running = asyncio.Event()
    c_running = asyncio.Event()

    async def c_task(ctx):
        c_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def b_task(ctx):
        b_running.set()
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(execute=c_task, process_id="c", name="C")
            while ctx.should_continue():
                await asyncio.sleep(0.01)

    async def a_task(ctx):
        a_running.set()
        await ctx.run_child(execute=b_task, process_id="b", name="B")

    a_inst = TaskInstance(execute=a_task, process_id="a", name="A")
    b_inst = TaskInstance(
        execute=b_task, process_id="b", name="B",
        auto_cancel_children=False,
    )
    c_inst = TaskInstance(execute=c_task, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=1.0)
    await upsert_process(mongo_db, prefix, a_inst)
    optio._executor.register_tasks([a_inst, b_inst, c_inst])

    runner = asyncio.create_task(optio.launch_and_wait("a"))
    await a_running.wait()
    await b_running.wait()
    await c_running.wait()
    await asyncio.sleep(0.1)

    await optio.cancel("a")

    # Immediately: A and B should be in cancel_requested/cancelling;
    # C remains active because B opted out.
    a_proc = await get_process_by_process_id(mongo_db, prefix, "a")
    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    c_proc = await get_process_by_process_id(mongo_db, prefix, "c")
    assert a_proc["status"]["state"] in {"cancel_requested", "cancelling"}
    assert b_proc["status"]["state"] in {"cancel_requested", "cancelling"}
    assert c_proc["status"]["state"] in {"running", "scheduled"}

    # Cleanup: cancel C directly so B can exit, then A can exit.
    await optio.cancel("c")
    await asyncio.wait_for(runner, timeout=5.0)
    await optio.shutdown(grace_seconds=0.5)


async def test_cancel_shared_deadline_across_subtree(mongo_db):
    """All entries created under one cancel sweep share the same monotonic deadline."""
    prefix = "p2t5"
    parent_running = asyncio.Event()
    child1_running = asyncio.Event()
    child2_running = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        parent_running.set()
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(execute=long_child, process_id="c1", name="C1")
            child1_running.set()
            await group.spawn(execute=long_child, process_id="c2", name="C2")
            child2_running.set()

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    c1_inst = TaskInstance(execute=long_child, process_id="c1", name="C1")
    c2_inst = TaskInstance(execute=long_child, process_id="c2", name="C2")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=3.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, c1_inst, c2_inst])

    runner = asyncio.create_task(optio.launch_and_wait("parent"))
    await parent_running.wait()
    await child1_running.wait()
    await child2_running.wait()
    await asyncio.sleep(0.1)

    await optio.cancel("parent")

    # Inspect deadlines in the executor's cancellation_flags map.
    entries = optio._executor._cancellation_flags
    deadlines = [entry.deadline for entry in entries.values() if entry.deadline is not None]
    assert len(deadlines) >= 3, f"expected >=3 entries, got {len(deadlines)}: {deadlines}"
    assert all(d == deadlines[0] for d in deadlines), (
        f"deadlines diverge: {deadlines}"
    )

    await asyncio.wait_for(runner, timeout=10.0)
    await optio.shutdown(grace_seconds=0.5)


async def test_cancel_concurrent_calls_are_idempotent(mongo_db):
    """Concurrent cancel(parent) calls do not corrupt state. One wins,
    others return not-cancellable."""
    prefix = "p2t6"
    parent_running = asyncio.Event()

    async def long_parent(ctx):
        parent_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    parent_inst = TaskInstance(execute=long_parent, process_id="parent", name="Parent")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst])

    runner = asyncio.create_task(optio.launch_and_wait("parent"))
    await parent_running.wait()
    await asyncio.sleep(0.05)

    results = await asyncio.gather(
        optio.cancel("parent"),
        optio.cancel("parent"),
        optio.cancel("parent"),
    )
    ok_count = sum(1 for r in results if r.ok)
    not_cancellable_count = sum(
        1 for r in results if not r.ok and r.reason == "not-cancellable"
    )
    assert ok_count == 1, f"expected exactly one ok, got {ok_count} ({results})"
    assert ok_count + not_cancellable_count == 3

    await asyncio.wait_for(runner, timeout=3.0)
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    assert parent_proc["status"]["state"] == "cancelled"

    await optio.shutdown(grace_seconds=0.5)


async def test_run_child_refuses_after_parent_cancel_when_auto(mongo_db):
    """When parent has auto_cancel_children=True and its cancellation_flag
    is set, ctx.run_child returns 'cancelled' immediately without creating
    a child doc."""
    prefix = "p3t1"
    spawn_after_cancel = asyncio.Event()
    refusal_result: dict = {}

    async def short_child(ctx):
        ctx.report_progress(50, "doing")

    async def parent(ctx):
        # Wait for cancel.
        while ctx.should_continue():
            await asyncio.sleep(0.01)
        # Now flag is set; try to spawn another child.
        state = await ctx.run_child(
            execute=short_child, process_id="late_child", name="Late",
        )
        refusal_result["state"] = state
        spawn_after_cancel.set()

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=short_child, process_id="late_child", name="Late")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    runner = asyncio.create_task(optio.launch_and_wait("parent"))
    await asyncio.sleep(0.1)

    await optio.cancel("parent")
    await spawn_after_cancel.wait()

    assert refusal_result["state"] == "cancelled"
    late_proc = await get_process_by_process_id(mongo_db, prefix, "late_child")
    assert late_proc is None, "no child doc should exist for refused run_child"

    await runner
    await optio.shutdown(grace_seconds=0.5)
