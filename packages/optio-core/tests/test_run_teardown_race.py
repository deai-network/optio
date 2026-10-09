"""A run's teardown releases only what that run owns.

A relaunch reuses the process document, so run 2 of a process shares its
processId and OID with run 1. Run 2 can start in the window after run 1 wrote
its final state and before run 1's teardown (the `finally` of
`_execute_process`) runs. That teardown must not fail run 2's result future,
drop run 2's published result, or remove run 2's cancel entry and running task.

Each test holds run 1 inside that window deterministically: its ephemeral
cleanup (the last await before the teardown) waits on an event.
"""

import asyncio
import time

import pytest

from optio_core.exceptions import ResultNotPublished
from optio_core.executor import Executor
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

_HANG = 60  # a hang ceiling only; every wait ends on an event
PID = "race.p"


async def _setup(mongo_db, body):
    task = TaskInstance(execute=body, process_id=PID, name="Race")
    oid = (await upsert_process(mongo_db, "test", task))["_id"]
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    # Hold run 1 between its final-state write and its teardown.
    real_cleanup = executor._cleanup_ephemeral
    in_window, release = asyncio.Event(), asyncio.Event()
    calls = {"n": 0}

    async def gated_cleanup(process_id):
        calls["n"] += 1
        if calls["n"] == 1:
            in_window.set()
            await release.wait()
        await real_cleanup(process_id)

    executor._cleanup_ephemeral = gated_cleanup
    return executor, oid, in_window, release


def _launch(executor):
    return asyncio.create_task(executor.launch_process(PID, session_id=None))


async def test_a_new_launchers_future_survives_the_previous_runs_teardown(mongo_db):
    runs = {"n": 0}
    started2, go2, done2 = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def body(ctx):
        runs["n"] += 1
        if runs["n"] == 2:
            started2.set()
            await go2.wait()
            ctx.publish_result("r2")
            await done2.wait()

    executor, _, in_window, release = await _setup(mongo_db, body)
    fut1 = executor.ensure_result_future(PID)
    run1 = _launch(executor)
    await asyncio.wait_for(in_window.wait(), _HANG)

    fut2 = executor.ensure_result_future(PID)  # a resume right after run 1 ended
    run2 = _launch(executor)
    try:
        await asyncio.wait_for(started2.wait(), _HANG)
        release.set()
        await asyncio.wait_for(run1, _HANG)        # run 1's teardown has run
        go2.set()

        assert await asyncio.wait_for(fut2, _HANG) == "r2"
        with pytest.raises(ResultNotPublished):
            fut1.result()                          # run 1 never published
    finally:
        done2.set()
        await asyncio.wait_for(run2, _HANG)


async def test_the_next_runs_published_result_survives_the_previous_runs_teardown(mongo_db):
    runs = {"n": 0}
    published2, done2 = asyncio.Event(), asyncio.Event()
    errors: list[BaseException] = []

    async def body(ctx):
        runs["n"] += 1
        if runs["n"] == 1:
            ctx.publish_result("r1")
            return
        try:
            ctx.publish_result("r2")
        except Exception as e:  # noqa: BLE001 -- recorded for the assertion
            errors.append(e)
        published2.set()
        await done2.wait()

    executor, _, in_window, release = await _setup(mongo_db, body)
    run1 = _launch(executor)
    await asyncio.wait_for(in_window.wait(), _HANG)
    run2 = _launch(executor)
    try:
        await asyncio.wait_for(published2.wait(), _HANG)
        release.set()
        await asyncio.wait_for(run1, _HANG)

        assert errors == []
        assert executor.get_published_result(PID) == "r2"
    finally:
        done2.set()
        await asyncio.wait_for(run2, _HANG)


async def test_the_next_run_stays_cancellable_after_the_previous_runs_teardown(mongo_db):
    runs = {"n": 0}
    started2 = asyncio.Event()

    async def body(ctx):
        runs["n"] += 1
        if runs["n"] == 2:
            started2.set()
            await ctx.cancellation_flag.wait()

    executor, oid, in_window, release = await _setup(mongo_db, body)
    run1 = _launch(executor)
    await asyncio.wait_for(in_window.wait(), _HANG)
    run2 = _launch(executor)
    try:
        await asyncio.wait_for(started2.wait(), _HANG)
        release.set()
        await asyncio.wait_for(run1, _HANG)

        assert executor.owns(oid)
        assert executor.request_cancel_with_deadline(oid, deadline=time.monotonic() + 3600)
        assert await asyncio.wait_for(run2, _HANG) == "cancelled"
    finally:
        if not run2.done():
            run2.cancel()
