"""Shutdown must drain cooperating tasks BEFORE unblocking run().

Spec: docs/superpowers/specs/2026-06-02-graceful-engine-shutdown-capture-design.md
(Addendum). Regression guard for the run()/shutdown() ordering race: the
signal handler dispatched shutdown() as a detached task that set
_shutdown_event before the cooperative drain, so run() returned and
asyncio.run() teardown cancelled the drain mid-capture.
"""
import asyncio
import time

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance

pytestmark = pytest.mark.asyncio


async def test_shutdown_event_fires_only_after_drain(mongo_db):
    prefix = "shut1"
    running = asyncio.Event()
    captured = asyncio.Event()

    async def task(ctx):
        running.set()
        # Cooperate with cancel, then simulate a snapshot capture that takes
        # real time inside the cooperative window.
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)  # "capture"
        captured.set()

    ti = TaskInstance(process_id="t.cap", name="Cap", params={}, execute=task)

    async def gen(_s, _f):
        return [ti]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("t.cap", session_id=None)
        await asyncio.wait_for(running.wait(), timeout=60)

        # Mimic the signal handler: dispatch shutdown as a detached task.
        shutdown_task = asyncio.create_task(optio.shutdown())

        # The invariant: by the time run()'s unblock event is set, the
        # cooperative drain must already have completed (task captured).
        await asyncio.wait_for(optio._shutdown_event.wait(), timeout=60)
        assert captured.is_set(), (
            "_shutdown_event fired before the running task finished its "
            "cooperative capture — run() would return and the loop tear-down "
            "would abort the drain"
        )

        await asyncio.wait_for(shutdown_task, timeout=60)
    finally:
        await asyncio.wait_for(run_task, timeout=60)


async def test_launch_refused_during_shutdown(mongo_db):
    prefix = "shut2"
    running = asyncio.Event()

    async def slow(ctx):
        running.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    a = TaskInstance(process_id="t.a", name="A", params={}, execute=slow)
    b = TaskInstance(process_id="t.b", name="B", params={}, execute=slow)

    async def gen(_s, _f):
        return [a, b]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("t.a", session_id=None)
        await asyncio.wait_for(running.wait(), timeout=60)

        shutdown_task = asyncio.create_task(optio.shutdown())
        # Wait until shutdown() has actually entered its shutting-down state
        # (set synchronously at the top of shutdown()), then verify the launch
        # is refused. Gating on the state, not a fixed sleep, is race-free.
        deadline = time.monotonic() + 60.0
        while not optio._shutting_down:
            if time.monotonic() >= deadline:
                raise AssertionError("shutdown did not enter shutting-down state")
            await asyncio.sleep(0.005)

        outcome = await optio.launch("t.b", session_id=None)
        assert outcome.ok is False
        assert outcome.reason == "shutting-down"

        await asyncio.wait_for(shutdown_task, timeout=60)
    finally:
        await asyncio.wait_for(run_task, timeout=60)


async def test_shutdown_drains_parent_and_child(mongo_db):
    prefix = "shut3"
    parent_running = asyncio.Event()
    child_running = asyncio.Event()
    child_captured = asyncio.Event()

    async def child(ctx):
        child_running.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        child_captured.set()

    async def parent(ctx):
        parent_running.set()
        await ctx.run_child(execute=child, process_id="p.child", name="Child", params={})

    parent_ti = TaskInstance(process_id="p.parent", name="Parent", params={}, execute=parent)

    async def gen(_s, _f):
        return [parent_ti]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.parent", session_id=None)
        await asyncio.wait_for(parent_running.wait(), timeout=60)
        await asyncio.wait_for(child_running.wait(), timeout=60)

        shutdown_task = asyncio.create_task(optio.shutdown())
        await asyncio.wait_for(optio._shutdown_event.wait(), timeout=60)

        # The child cooperated and captured before the event fired.
        assert child_captured.is_set()

        await asyncio.wait_for(shutdown_task, timeout=60)
    finally:
        await asyncio.wait_for(run_task, timeout=60)
