"""launch_and_await_result / publish_result matrix.

Spec: docs/2026-06-10-claudecode-conversation-gate-design.md
"""
import asyncio
import time as _time

import pytest

from optio_core.exceptions import LaunchError, ResultNotPublished
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _define(optio: Optio, process_id: str, execute) -> None:
    await optio.adhoc_define(
        TaskInstance(execute=execute, process_id=process_id, name=process_id),
    )


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 5.0) -> dict:
    """Poll until process_id reaches a terminal state or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def test_await_then_publish(mongo_db):
    """Caller awaits first; task publishes; object delivered, task continues."""
    release = asyncio.Event()

    async def execute(ctx):
        ctx.publish_result({"hello": "world"})
        await release.wait()

    optio = await _make_optio(mongo_db, "pubres1")
    await _define(optio, "pub-1", execute)
    result = await optio.launch_and_await_result(
        "pub-1", session_id=None, timeout=10,
    )
    assert result == {"hello": "world"}
    # Task is still running and the registry serves the live object.
    assert optio.get_published_result("pub-1") == {"hello": "world"}
    release.set()

    await _wait_terminal(optio, "pub-1")
    await optio.shutdown(grace_seconds=0.5)


async def test_terminal_without_publish_raises(mongo_db):
    async def execute(ctx):
        return  # ends without publishing

    optio = await _make_optio(mongo_db, "pubres2")
    await _define(optio, "pub-2", execute)
    with pytest.raises(ResultNotPublished):
        await optio.launch_and_await_result(
            "pub-2", session_id=None, timeout=10,
        )

    await optio.shutdown(grace_seconds=0.5)


async def test_double_publish_raises_inside_task(mongo_db):
    seen: list[BaseException] = []

    async def execute(ctx):
        ctx.publish_result(1)
        try:
            ctx.publish_result(2)
        except RuntimeError as e:
            seen.append(e)

    optio = await _make_optio(mongo_db, "pubres3")
    await _define(optio, "pub-3", execute)
    res = await optio.launch_and_await_result(
        "pub-3", session_id=None, timeout=10,
    )
    assert res == 1
    # allow the body to finish
    await asyncio.sleep(0.2)
    assert len(seen) == 1

    await _wait_terminal(optio, "pub-3")
    await optio.shutdown(grace_seconds=0.5)


async def test_launch_refused_raises_launcherror(mongo_db):
    optio = await _make_optio(mongo_db, "pubres4")
    with pytest.raises(LaunchError) as ei:
        await optio.launch_and_await_result(
            "no-such-process", session_id=None,
        )
    assert ei.value.reason == "not-found"

    await optio.shutdown(grace_seconds=0.5)


async def test_timeout_keeps_task_running(mongo_db):
    release = asyncio.Event()

    async def execute(ctx):
        await release.wait()  # never publishes until released

    optio = await _make_optio(mongo_db, "pubres5")
    await _define(optio, "pub-4", execute)
    with pytest.raises(asyncio.TimeoutError):
        await optio.launch_and_await_result(
            "pub-4", session_id=None, timeout=0.3,
        )
    proc = await optio.get_process("pub-4")
    assert proc["status"]["state"] == "running"
    release.set()

    await _wait_terminal(optio, "pub-4")
    await optio.shutdown(grace_seconds=0.5)


async def test_registry_cleared_on_terminal(mongo_db):
    async def execute(ctx):
        ctx.publish_result("x")

    optio = await _make_optio(mongo_db, "pubres6")
    await _define(optio, "pub-5", execute)
    await optio.launch_and_await_result(
        "pub-5", session_id=None, timeout=10,
    )
    await _wait_terminal(optio, "pub-5")
    # The terminal DB write lands before the executor's `finally` pops the
    # registry entry — poll briefly instead of asserting immediately.
    for _ in range(100):
        if optio.get_published_result("pub-5") is None:
            break
        await asyncio.sleep(0.02)
    assert optio.get_published_result("pub-5") is None

    await optio.shutdown(grace_seconds=0.5)
