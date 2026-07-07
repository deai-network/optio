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

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
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

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
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

    runner = asyncio.create_task(optio.launch_and_wait("a", session_id=None))
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

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
    await parent_running.wait()
    await child1_running.wait()
    await child2_running.wait()

    # The parent sets child*_running immediately after spawning, but each child
    # only registers its cancellation entry once its execute task actually
    # starts (executor._execute_process). Wait for all three (parent + c1 + c2)
    # to be registered before cancelling — otherwise cancel() can propagate the
    # shared deadline before a child is cancellable and the entry-count assert
    # races under CPU load (seen as "got 1"/"got 2" instead of 3).
    for _ in range(1000):
        if len(optio._executor._cancellation_flags) >= 3:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError("children never registered in _cancellation_flags")

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

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
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
    is set, ctx.run_child returns ChildOutcome(state="cancelled")
    immediately without creating a child doc."""
    prefix = "p3t1"
    parent_started = asyncio.Event()
    spawn_after_cancel = asyncio.Event()
    refusal_result: dict = {}

    async def short_child(ctx):
        ctx.report_progress(50, "doing")

    async def parent(ctx):
        parent_started.set()
        # Wait for cancel.
        while ctx.should_continue():
            await asyncio.sleep(0.01)
        # Now flag is set; try to spawn another child.
        outcome = await ctx.run_child(
            execute=short_child, process_id="late_child", name="Late",
        )
        refusal_result["state"] = outcome.state
        spawn_after_cancel.set()

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=short_child, process_id="late_child", name="Late")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
    await parent_started.wait()  # parent is running -> its cancel flag is registered

    await optio.cancel("parent")
    await spawn_after_cancel.wait()

    assert refusal_result["state"] == "cancelled"
    late_proc = await get_process_by_process_id(mongo_db, prefix, "late_child")
    assert late_proc is None, "no child doc should exist for refused run_child"

    await runner
    await optio.shutdown(grace_seconds=0.5)


async def test_alpha_child_cancel_triggers_parent_cancel_of_siblings(mongo_db):
    """alpha: when child cancels and parent has survive_cancel=False at
    group level, parent's OTHER active children are also cancelled."""
    prefix = "p4t2"
    a_running = asyncio.Event()
    b_running = asyncio.Event()
    c_running = asyncio.Event()

    async def long_child(ctx):
        if ctx.process_id == "b":
            b_running.set()
        elif ctx.process_id == "c":
            c_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        a_running.set()
        async with ctx.parallel_group(survive_cancel=False) as group:
            await group.spawn(execute=long_child, process_id="b", name="B")
            await group.spawn(execute=long_child, process_id="c", name="C")

    parent_inst = TaskInstance(execute=parent, process_id="a", name="A")
    b_inst = TaskInstance(execute=long_child, process_id="b", name="B")
    c_inst = TaskInstance(execute=long_child, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst, c_inst])

    runner = asyncio.create_task(optio.launch_and_wait("a", session_id=None))
    await a_running.wait()
    await b_running.wait()
    await c_running.wait()
    await asyncio.sleep(0.1)

    # Cancel B directly: alpha should trigger cancel(A) which propagates to C.
    await optio.cancel("b")

    await asyncio.wait_for(runner, timeout=5.0)
    a_proc = await _wait_terminal(mongo_db, prefix, "a")
    b_proc = await _wait_terminal(mongo_db, prefix, "b")
    c_proc = await _wait_terminal(mongo_db, prefix, "c")
    assert b_proc["status"]["state"] == "cancelled"
    assert c_proc["status"]["state"] == "cancelled"
    # Parent does not catch the ExceptionGroup, so it re-raises out of
    # parent's execute_fn. The cancellation cascade still set the
    # parent's flag and transitioned its row through
    # cancel_requested/cancelling; the executor's `except Exception` arm
    # then writes 'failed', overwriting the transient cancel state.
    assert a_proc["status"]["state"] == "failed"

    await optio.shutdown(grace_seconds=0.5)


async def test_parallel_group_fail_fast_under_alpha(mongo_db):
    """parallel_group(survive_failure=False): one child failing auto-cancels
    siblings via alpha, rather than waiting for them to finish."""
    prefix = "p4t4"
    started = asyncio.Event()

    async def quick_fail(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("kaboom")

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        started.set()
        async with ctx.parallel_group(survive_failure=False) as group:
            await group.spawn(execute=quick_fail, process_id="b", name="B")
            await group.spawn(execute=long_child, process_id="c", name="C")

    parent_inst = TaskInstance(execute=parent, process_id="a", name="A")
    b_inst = TaskInstance(execute=quick_fail, process_id="b", name="B")
    c_inst = TaskInstance(execute=long_child, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=3.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst, c_inst])

    t0 = _time.monotonic()
    runner = asyncio.create_task(optio.launch_and_wait("a", session_id=None))
    await started.wait()
    await asyncio.wait_for(runner, timeout=5.0)
    elapsed = _time.monotonic() - t0

    assert elapsed < 2.0, f"expected fail-fast, took {elapsed:.2f}s"

    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    c_proc = await get_process_by_process_id(mongo_db, prefix, "c")
    a_proc = await get_process_by_process_id(mongo_db, prefix, "a")
    assert b_proc["status"]["state"] == "failed"
    assert c_proc["status"]["state"] == "cancelled"
    # Parent does not catch the ExceptionGroup → re-raises → executor
    # writes 'failed'. After the alpha-cascade fix, parent's flag is no
    # longer set on a child-failure breach, so the parent's row stays
    # 'running' until the executor writes 'failed' directly.
    assert a_proc["status"]["state"] == "failed"

    await optio.shutdown(grace_seconds=0.5)


async def test_force_cancel_cascade_auto_propagate(mongo_db):
    """Stubborn child that ignores should_continue gets force-cancelled
    when grace expires; parent also force-cancelled."""
    prefix = "p5t1"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def stubborn_child(ctx):
        child_started.set()
        # Ignore should_continue; only break on asyncio.CancelledError.
        while True:
            await asyncio.sleep(0.05)

    async def parent(ctx):
        parent_started.set()
        await ctx.run_child(
            execute=stubborn_child, process_id="stub", name="Stub",
        )

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=stubborn_child, process_id="stub", name="Stub")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    # Fire-and-forget launch so the test does not have to absorb
    # CancelledError when force-cancel kills the runner task.
    await optio.launch("parent", session_id=None)
    await parent_started.wait()
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")

    # Poll DB until both reach terminal (force-cancel writes 'failed'
    # after grace expires).
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent", timeout=5.0)
    child_proc = await _wait_terminal(mongo_db, prefix, "stub", timeout=5.0)
    assert parent_proc["status"]["state"] in {"failed", "cancelled"}
    assert child_proc["status"]["state"] in {"failed", "cancelled"}

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)


async def test_force_cancel_cascade_optout_path(mongo_db):
    """Opt-out parent: cancel does not propagate to children; after grace,
    force-cancel cascade catches them."""
    prefix = "p5t3"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def long_child(ctx):
        while True:
            await asyncio.sleep(0.05)

    async def parent(ctx):
        parent_started.set()
        async with ctx.parallel_group(survive_cancel=True, survive_failure=True) as group:
            await group.spawn(execute=long_child, process_id="b", name="B")
            child_started.set()
            # Do not cancel B; let force-cancel cascade handle it.
            while True:
                await asyncio.sleep(0.05)

    parent_inst = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    b_inst = TaskInstance(execute=long_child, process_id="b", name="B")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst])
    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    await optio.launch("parent", session_id=None)
    await parent_started.wait()
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")

    # Immediately after cancel: B still active.
    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    assert b_proc["status"]["state"] in {"running", "scheduled"}

    # After grace + cascade, B reaches terminal via force_cancel cascade.
    b_proc = await _wait_terminal(mongo_db, prefix, "b", timeout=5.0)
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent", timeout=5.0)
    assert b_proc["status"]["state"] == "failed"
    assert parent_proc["status"]["state"] == "failed"

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)


async def test_force_cancel_cascade_catches_late_optout_child(mongo_db):
    """Opt-out parent spawns a NEW child after cancel arrives. Force-cancel
    cascade walks the DB at force time and catches it."""
    prefix = "p5t4"
    parent_started = asyncio.Event()
    late_child_spawned = asyncio.Event()

    async def late_child(ctx):
        while True:
            await asyncio.sleep(0.05)

    async def parent(ctx):
        parent_started.set()
        # Wait for cancel to arrive.
        while ctx.should_continue():
            await asyncio.sleep(0.01)
        # Opt-out window: still allowed to spawn.
        async with ctx.parallel_group(survive_cancel=True, survive_failure=True) as group:
            await group.spawn(
                execute=late_child, process_id="late", name="Late",
            )
            late_child_spawned.set()
            while True:
                await asyncio.sleep(0.05)

    parent_inst = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    late_inst = TaskInstance(execute=late_child, process_id="late", name="Late")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, late_inst])
    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    await optio.launch("parent", session_id=None)
    await parent_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")
    await late_child_spawned.wait()

    # Within force-cancel window, late child exists in DB and is active.
    # spawn() schedules child persistence as a background task
    # (context.py: asyncio.create_task(_run())); it does not await the DB
    # write. Poll for the late child to appear and be active within the
    # force-cancel window instead of reading eagerly (the eager read raced
    # cold-start persistence and failed when this test ran first/alone).
    late_proc = None
    end = _time.monotonic() + 0.4
    while _time.monotonic() < end:
        late_proc = await get_process_by_process_id(mongo_db, prefix, "late")
        if late_proc is not None and late_proc["status"]["state"] in {"running", "scheduled"}:
            break
        await asyncio.sleep(0.01)
    assert late_proc is not None, "late child never persisted/active within window"
    assert late_proc["status"]["state"] in {"running", "scheduled"}

    # After grace + cascade, late child reaches terminal.
    late_proc = await _wait_terminal(mongo_db, prefix, "late", timeout=5.0)
    assert late_proc["status"]["state"] == "failed"

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)


async def test_task_raising_cancelled_error_reaches_cancelled_state(mongo_db):
    """Task body that observes the cancel flag and raises asyncio.CancelledError
    must reach terminal state=cancelled.

    This is the optio-recipe-runner pattern: it explicitly raises
    CancelledError after the cancel flag fires (see optio_recipe_runner/
    session.py). CancelledError is a BaseException, not Exception, so
    naive `except Exception` clauses don't catch it — the executor needs
    a dedicated arm or the row stays at `cancelling` forever and the
    supervisor's force_cancel can't help (the cancellation flag entry is
    popped when the task body unwinds)."""
    prefix = "p6t1"
    started = asyncio.Event()

    async def raises_cancelled_on_flag(ctx):
        started.set()
        # Cooperatively wait on the flag (the recipe-runner pattern), then
        # raise CancelledError instead of returning normally.
        await ctx.cancellation_flag.wait()
        raise asyncio.CancelledError()

    task = TaskInstance(
        execute=raises_cancelled_on_flag, process_id="raiser", name="Raiser",
    )

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, task)
    optio._executor.register_tasks([task])

    runner = asyncio.create_task(optio.launch_and_wait("raiser", session_id=None))
    await started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("raiser")

    # The runner task absorbs the CancelledError raised by the task body.
    try:
        await runner
    except asyncio.CancelledError:
        pass

    proc = await _wait_terminal(mongo_db, prefix, "raiser")
    assert proc["status"]["state"] == "cancelled"
    assert proc["status"].get("stoppedAt") is not None
    # The flag entry must have been popped (otherwise force_cancel would
    # later overwrite our `cancelled` state with `failed`).
    assert not optio._executor._cancellation_flags

    await optio.shutdown(grace_seconds=0.5)


async def test_child_raises_cancelled_error_propagates_to_parent(mongo_db):
    """Cancel-error from a child task body propagates up: parent's
    `ctx.run_child` re-raises CancelledError, parent itself reaches
    `cancelled`. End-to-end check of the full cancel-propagation chain
    when the cancellation mechanism is CancelledError (not cooperative
    return)."""
    prefix = "p6t2"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def child(ctx):
        child_started.set()
        await ctx.cancellation_flag.wait()
        raise asyncio.CancelledError()

    async def parent(ctx):
        parent_started.set()
        await ctx.run_child(execute=child, process_id="kid", name="Kid")

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=child, process_id="kid", name="Kid")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    runner = asyncio.create_task(optio.launch_and_wait("parent", session_id=None))
    await parent_started.wait()
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")
    try:
        await runner
    except asyncio.CancelledError:
        pass

    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    child_proc = await _wait_terminal(mongo_db, prefix, "kid")
    assert child_proc["status"]["state"] == "cancelled"
    assert parent_proc["status"]["state"] == "cancelled"

    await optio.shutdown(grace_seconds=0.5)


