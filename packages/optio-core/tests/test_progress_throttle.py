"""Tests for adaptive `report_progress` throttling.

Covers the rate-detection switch:
- Quiet load (<= AVALANCHE_THRESHOLD calls per AVALANCHE_WINDOW): every
  message is preserved in the log.
- Avalanche load (> AVALANCHE_THRESHOLD per window): intermediate
  messages are dropped (the latest survives), and a synthetic
  "(N messages dropped)" line is written immediately before the
  surviving message.
"""

import asyncio

import pytest

from optio_core.context import (
    AVALANCHE_THRESHOLD,
    AVALANCHE_WINDOW,
    ProcessContext,
)
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id, upsert_process


# The throttle assertions depend on sub-second wall-clock windows and slip
# under heavy parallel CPU load; `serial` runs them in the final quiet phase.
pytestmark = [pytest.mark.asyncio, pytest.mark.serial]


async def _dummy(ctx) -> None:  # pragma: no cover - placeholder
    return None


def _make_context(mongo_db, prefix, proc) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix=prefix,
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
    )


async def _wait_for_flush(ctx: ProcessContext) -> None:
    """Await any in-flight flush task so the log is fully written."""
    if ctx._flush_task is not None:
        try:
            await ctx._flush_task
        except asyncio.CancelledError:
            pass


async def _log_messages(mongo_db, prefix, process_id) -> list[str]:
    proc = await get_process_by_process_id(mongo_db, prefix, process_id)
    return [entry["message"] for entry in (proc.get("log") or [])]


async def test_quiet_load_preserves_every_message(mongo_db):
    """A handful of slow-paced calls should all show up in the log."""
    task = TaskInstance(execute=_dummy, process_id="quiet", name="Quiet")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    ctx.report_progress(None, "msg-1")
    await _wait_for_flush(ctx)
    ctx.report_progress(None, "msg-2")
    await _wait_for_flush(ctx)
    ctx.report_progress(None, "msg-3")
    await _wait_for_flush(ctx)

    messages = await _log_messages(mongo_db, "test", "quiet")
    assert messages == ["msg-1", "msg-2", "msg-3"]


async def test_avalanche_drops_intermediate_messages_and_emits_summary(mongo_db):
    """Many fast calls should produce the surviving last message preceded
    by a "(N messages dropped)" line."""
    task = TaskInstance(execute=_dummy, process_id="burst", name="Burst")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    # Emit a tight burst — all calls happen synchronously within a single
    # event-loop tick, so they fall well within AVALANCHE_WINDOW.
    n_calls = 200
    for i in range(n_calls):
        ctx.report_progress(None, f"burst-{i}")

    # Drive the event loop until the flush task finishes.
    await ctx.flush_final_progress()

    messages = await _log_messages(mongo_db, "test", "burst")

    # Last entry must be the very last burst message.
    assert messages[-1] == f"burst-{n_calls - 1}"

    # The penultimate entry must be the drop summary.
    drop_msg = messages[-2]
    assert drop_msg.startswith("(") and drop_msg.endswith(" messages dropped)")
    drop_count = int(drop_msg.split()[0].lstrip("("))

    # There may be some messages that survived in the queue before the
    # avalanche threshold tripped (the first 11). Plus the final two
    # entries (drop summary + survivor).
    expected_pre = list(range(0, AVALANCHE_THRESHOLD + 1))  # 0..10 inclusive
    pre_burst = messages[: -2]
    assert all(
        msg.startswith("burst-") for msg in pre_burst
    ), f"unexpected pre-burst: {pre_burst!r}"

    # Drop count + survived (post-threshold) calls + pre-burst count
    # should add up to n_calls. (Pre-burst is everything before the
    # threshold trips; survived = 1, dropped = the rest.)
    assert len(pre_burst) + drop_count + 1 == n_calls


async def test_avalanche_then_quiet_drop_summary_emitted_before_survivor(mongo_db):
    """When the avalanche subsides, the drop summary must precede both
    the surviving avalanche message and any subsequent quiet message."""
    task = TaskInstance(execute=_dummy, process_id="burst2quiet", name="B2Q")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    # Burst.
    n_burst = 30
    for i in range(n_burst):
        ctx.report_progress(None, f"avalanche-{i}")

    # Wait for the rolling window to clear.
    await asyncio.sleep(AVALANCHE_WINDOW + 0.05)

    # A quiet call now — the surviving avalanche message and the drop
    # summary should be emitted as part of this call's flush, then the
    # new message.
    ctx.report_progress(None, "after")
    await _wait_for_flush(ctx)

    messages = await _log_messages(mongo_db, "test", "burst2quiet")

    # The post-burst quiet message is the final entry.
    assert messages[-1] == "after"

    # Some pre-burst entries (the calls that arrived before the threshold
    # tripped), then a drop summary, then the surviving avalanche
    # message, then "after".
    survived = messages[-2]
    assert survived == f"avalanche-{n_burst - 1}"
    drop_msg = messages[-3]
    assert drop_msg.endswith(" messages dropped)")


async def test_flush_final_progress_handles_pending_avalanche(mongo_db):
    """End-of-task flush surfaces any pending avalanche state, including
    the drop summary."""
    task = TaskInstance(execute=_dummy, process_id="final", name="Final")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    for i in range(50):
        ctx.report_progress(None, f"x-{i}")

    await ctx.flush_final_progress()

    messages = await _log_messages(mongo_db, "test", "final")
    assert messages[-1] == "x-49"
    assert messages[-2].endswith(" messages dropped)")


async def test_no_avalanche_no_drop_summary(mongo_db):
    """Below the threshold, no drop summary is ever emitted."""
    task = TaskInstance(execute=_dummy, process_id="noavalanche", name="NoA")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    # Exactly threshold calls — should NOT trigger avalanche mode.
    for i in range(AVALANCHE_THRESHOLD):
        ctx.report_progress(None, f"q-{i}")
    await _wait_for_flush(ctx)

    messages = await _log_messages(mongo_db, "test", "noavalanche")
    assert messages == [f"q-{i}" for i in range(AVALANCHE_THRESHOLD)]
    assert not any("dropped" in m for m in messages)
