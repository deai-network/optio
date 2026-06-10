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
