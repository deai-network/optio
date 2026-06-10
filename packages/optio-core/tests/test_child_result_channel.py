"""run_child_with_result / ChildHandle matrix.

Spec: docs/2026-06-10-child-result-channel-design.md
"""
import asyncio

import pytest

from optio_core.exceptions import ChildProcessFailed, ResultNotPublished
from optio_core.lifecycle import Optio
from optio_core.models import ChildHandle, ChildOutcome, TaskInstance, TaskInstanceCore


async def test_childhandle_outcome_awaitable_repeatedly():
    """outcome() awaits the wrapped task; repeat awaits return the same value."""
    async def body() -> ChildOutcome:
        return ChildOutcome(state="done")

    task = asyncio.ensure_future(body())
    handle = ChildHandle(result={"x": 1}, task=task)
    assert handle.result == {"x": 1}
    out1 = await handle.outcome()
    out2 = await handle.outcome()
    assert out1.state == "done"
    assert out2 is out1


def test_result_not_published_carries_state():
    e = ResultNotPublished("pid-1", state="cancelled")
    assert e.process_id == "pid-1"
    assert e.state == "cancelled"
    # Old single-arg form still works (used by executor.py).
    e2 = ResultNotPublished("pid-2")
    assert e2.state is None


import time as _time


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _define(optio: Optio, process_id: str, execute) -> None:
    await optio.adhoc_define(
        TaskInstance(execute=execute, process_id=process_id, name=process_id),
    )


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 5.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def test_child_publish_then_await(mongo_db):
    """Child publishes immediately (before parent awaits); object delivered;
    child keeps running until released; outcome() returns done."""
    release = asyncio.Event()
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result({"conv": 42})
        await release.wait()

    async def parent_exec(ctx):
        handle = await ctx.run_child_with_result(child_exec, "child-pub-1", "Child")
        seen["result"] = handle.result
        seen["live"] = True
        release.set()
        out = await handle.outcome()
        seen["state"] = out.state

    optio = await _make_optio(mongo_db, "chres1")
    await _define(optio, "parent-1", parent_exec)
    await optio.launch_and_wait("parent-1", session_id=None)
    assert seen["result"] == {"conv": 42}
    assert seen["state"] == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_child_await_then_publish(mongo_db):
    """Parent awaits first; child publishes after a delay."""
    seen: dict = {}

    async def child_exec(ctx):
        await asyncio.sleep(0.2)
        ctx.publish_result("late")

    async def parent_exec(ctx):
        handle = await ctx.run_child_with_result(child_exec, "child-pub-2", "Child")
        seen["result"] = handle.result
        out = await handle.outcome()
        seen["state"] = out.state

    optio = await _make_optio(mongo_db, "chres2")
    await _define(optio, "parent-2", parent_exec)
    await optio.launch_and_wait("parent-2", session_id=None)
    assert seen["result"] == "late"
    assert seen["state"] == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_run_child_task_with_result_sugar(mongo_db):
    """The TaskInstanceCore variant unpacks and delegates."""
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result(ctx.params.get("tag"))

    async def parent_exec(ctx):
        task = TaskInstanceCore(
            execute=child_exec, process_id="child-sugar-1",
            name="Sugar child", params={"tag": "via-task"},
        )
        handle = await ctx.run_child_task_with_result(task)
        seen["result"] = handle.result
        await handle.outcome()

    optio = await _make_optio(mongo_db, "chres3")
    await _define(optio, "parent-3", parent_exec)
    await optio.launch_and_wait("parent-3", session_id=None)
    assert seen["result"] == "via-task"
    await optio.shutdown(grace_seconds=0.5)


async def test_child_done_without_publish(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        return  # ends without publishing

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(child_exec, "child-np-1", "Child")
        except ResultNotPublished as e:
            seen["exc"] = e

    optio = await _make_optio(mongo_db, "chres4")
    await _define(optio, "parent-4", parent_exec)
    await optio.launch_and_wait("parent-4", session_id=None)
    assert seen["exc"].process_id == "child-np-1"
    assert seen["exc"].state == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_child_fails_before_publish(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        raise ValueError("boom")

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(child_exec, "child-fail-1", "Child")
        except ChildProcessFailed as e:
            seen["exc"] = e

    optio = await _make_optio(mongo_db, "chres5")
    await _define(optio, "parent-5", parent_exec)
    await optio.launch_and_wait("parent-5", session_id=None)
    assert isinstance(seen["exc"].original, ValueError)
    await optio.shutdown(grace_seconds=0.5)


async def test_refused_spawn_when_parent_cancelled(mongo_db):
    """Parent's cancellation flag is set before spawning: run_child refuses
    (no process doc), and the wrapper raises ResultNotPublished promptly
    with state='cancelled' and cleans up its pre-registered future."""
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result("never")

    async def parent_exec(ctx):
        ctx._cancellation_flag.set()  # simulate cancel arriving first
        try:
            await asyncio.wait_for(
                ctx.run_child_with_result(child_exec, "child-ref-1", "Child"),
                timeout=5,
            )
        except ResultNotPublished as e:
            seen["exc"] = e
        seen["future_cleaned"] = (
            "child-ref-1" not in ctx._executor._result_futures
        )

    optio = await _make_optio(mongo_db, "chres6")
    await _define(optio, "parent-6", parent_exec)
    await optio.launch_and_wait("parent-6", session_id=None)
    assert seen["exc"].state == "cancelled"
    assert seen["future_cleaned"] is True
    await optio.shutdown(grace_seconds=0.5)


async def test_timeout_keeps_child_running(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        await asyncio.sleep(0.5)
        ctx.publish_result("eventually")
        await asyncio.sleep(0.2)

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(
                child_exec, "child-to-1", "Child", timeout=0.1,
            )
        except asyncio.TimeoutError:
            seen["timed_out"] = True
        # The child keeps running; the object is retrievable once published.
        for _ in range(100):
            obj = ctx._executor.get_published_result("child-to-1")
            if obj is not None:
                seen["late"] = obj
                break
            await asyncio.sleep(0.02)

    optio = await _make_optio(mongo_db, "chres7")
    await _define(optio, "parent-7", parent_exec)
    await optio.launch_and_wait("parent-7", session_id=None)
    assert seen["timed_out"] is True
    assert seen["late"] == "eventually"
    await optio.shutdown(grace_seconds=0.5)
