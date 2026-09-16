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
import logging

import pytest

from optio_core.context import (
    AVALANCHE_THRESHOLD,
    AVALANCHE_WINDOW,
    ProcessContext,
)
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id, upsert_process


pytestmark = pytest.mark.asyncio


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


async def test_avalanche_drops_intermediate_messages_and_emits_summary(
    mongo_db, fake_clock,
):
    """Many fast calls should produce the surviving last message preceded
    by a "(N messages dropped)" line."""
    task = TaskInstance(execute=_dummy, process_id="burst", name="Burst")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    # Emit a burst. The fake clock does not advance, so every call falls
    # inside AVALANCHE_WINDOW however slowly the loop runs.
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


class _FakeClock:
    """Stands in for `time` inside optio_core.context only, so a test can
    cross the avalanche window without sleeping (asyncio keeps its clock)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    from optio_core import context as context_module

    clock = _FakeClock()
    monkeypatch.setattr(context_module, "time", clock)
    return clock


async def test_quiet_message_during_avalanche_flush_is_written_once(
    mongo_db, fake_clock, monkeypatch,
):
    """A quiet report_progress arriving while a flush writes the drop summary
    must not make that flush write None, and every line must land exactly
    once, including the quiet one."""
    from optio_core import store

    task = TaskInstance(execute=_dummy, process_id="avalanche-race", name="Race")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    real_update_progress = store.update_progress
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def gated_update_progress(db, prefix, oid, progress):
        message = progress.message or ""
        if message.endswith(" messages dropped)") and not release.is_set():
            in_flight.set()
            await release.wait()
        await real_update_progress(db, prefix, oid, progress)

    monkeypatch.setattr(store, "update_progress", gated_update_progress)

    n_burst = 30
    for i in range(n_burst):
        ctx.report_progress(None, f"avalanche-{i}")
    await asyncio.wait_for(in_flight.wait(), 60)  # writing the drop summary

    fake_clock.advance(AVALANCHE_WINDOW + 0.05)
    ctx.report_progress(None, "after")  # quiet again, mid-flush
    release.set()
    await asyncio.wait_for(_wait_for_flush(ctx), 60)

    messages = await _log_messages(mongo_db, "test", "avalanche-race")
    assert messages[-2:] == [f"avalanche-{n_burst - 1}", "after"]
    assert messages[-3].endswith(" messages dropped)")
    assert sum(m.endswith(" messages dropped)") for m in messages) == 1


async def test_percent_update_during_flush_is_not_lost(mongo_db, monkeypatch):
    """A percent-only update made while a flush writes the previous one must
    be written by that same flush: it used to be cleared after the await,
    and then left waiting for the next report_progress call."""
    from optio_core import store

    task = TaskInstance(execute=_dummy, process_id="pct-race", name="Pct")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    real_update_progress = store.update_progress
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def gated_update_progress(db, prefix, oid, progress):
        if progress.percent == 50 and not release.is_set():
            in_flight.set()
            await release.wait()
        await real_update_progress(db, prefix, oid, progress)

    monkeypatch.setattr(store, "update_progress", gated_update_progress)

    ctx.report_progress(50)
    await asyncio.wait_for(in_flight.wait(), 60)  # writing 50
    ctx.report_progress(70)
    release.set()
    await asyncio.wait_for(_wait_for_flush(ctx), 60)

    proc = await get_process_by_process_id(mongo_db, "test", "pct-race")
    assert proc["progress"]["percent"] == 70


async def test_cancelling_the_final_flush_keeps_the_write_in_flight(
    mongo_db, monkeypatch,
):
    """If whoever called flush_final_progress is cancelled (force-cancel),
    the flush that is mid-write must still finish its line."""
    from optio_core import store

    task = TaskInstance(execute=_dummy, process_id="cancel-final", name="Cancel final")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    real_update_progress = store.update_progress
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def gated_update_progress(db, prefix, oid, progress):
        if progress.message == "msg-2" and not release.is_set():
            in_flight.set()
            await release.wait()
        await real_update_progress(db, prefix, oid, progress)

    monkeypatch.setattr(store, "update_progress", gated_update_progress)

    ctx.report_progress(None, "msg-1")
    await _wait_for_flush(ctx)
    ctx.report_progress(None, "msg-2")
    await asyncio.wait_for(in_flight.wait(), 60)  # the flush is now mid-write
    in_flight_flush = ctx._flush_task

    final = asyncio.create_task(ctx.flush_final_progress())
    await asyncio.sleep(0)  # let the final flush start waiting on it
    # Cancel it while it waits; if it had not started yet, this test would
    # pass without exercising the wait at all.
    assert not final.done() and not in_flight_flush.done()
    final.cancel()
    with pytest.raises(asyncio.CancelledError):
        await final

    release.set()
    await asyncio.wait_for(in_flight_flush, 60)

    messages = await _log_messages(mongo_db, "test", "cancel-final")
    assert messages == ["msg-1", "msg-2"]


async def test_flush_final_progress_returns_despite_a_running_percent_producer(
    mongo_db,
):
    """A coroutine still firing percent updates when the process finishes
    (e.g. a reader the task did not cancel) must not keep the final flush
    looping forever: the process would never get its terminal state."""
    task = TaskInstance(execute=_dummy, process_id="pct-producer", name="Producer")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    stop = asyncio.Event()

    async def producer():
        i = 0
        while not stop.is_set():
            i += 1
            ctx.report_progress(i % 100)
            await asyncio.sleep(0)

    running = asyncio.create_task(producer())
    try:
        ctx.report_progress(None, "last words")
        # 60 s bounds a true hang only: the final flush must return at once.
        await asyncio.wait_for(ctx.flush_final_progress(), 60)
    finally:
        stop.set()
        await running

    messages = await _log_messages(mongo_db, "test", "pct-producer")
    assert messages == ["last words"]


async def test_a_failed_flush_is_logged_when_it_fails(
    mongo_db, monkeypatch, caplog,
):
    """A flush whose write raises is logged right away, not only if and when
    the process finishes, and finishing the process still works."""
    from optio_core import store

    task = TaskInstance(execute=_dummy, process_id="flush-fails", name="Fails")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)
    caplog.set_level(logging.ERROR, logger="optio_core.context")

    real_update_progress = store.update_progress

    async def failing_update_progress(db, prefix, oid, progress):
        if progress.message == "boom":
            raise RuntimeError("db down")
        await real_update_progress(db, prefix, oid, progress)

    monkeypatch.setattr(store, "update_progress", failing_update_progress)

    ctx.report_progress(None, "boom")
    await asyncio.wait({ctx._flush_task})

    assert any("Progress flush failed" in r.getMessage() for r in caplog.records)

    ctx.report_progress(None, "after")
    await asyncio.wait_for(ctx.flush_final_progress(), 60)
    assert await _log_messages(mongo_db, "test", "flush-fails") == ["after"]


async def test_avalanche_then_quiet_drop_summary_emitted_before_survivor(
    mongo_db, fake_clock,
):
    """When the avalanche subsides, the drop summary must precede both
    the surviving avalanche message and any subsequent quiet message.

    Everything is queued before the flush starts here; the race with a flush
    already in progress is test_quiet_message_during_avalanche_flush_is_written_once."""
    task = TaskInstance(execute=_dummy, process_id="burst2quiet", name="B2Q")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    # Burst.
    n_burst = 30
    for i in range(n_burst):
        ctx.report_progress(None, f"avalanche-{i}")

    # Let the rolling window clear.
    fake_clock.advance(AVALANCHE_WINDOW + 0.05)

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


async def test_flush_final_progress_handles_pending_avalanche(
    mongo_db, fake_clock,
):
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


async def test_flush_final_progress_keeps_the_message_being_written(
    mongo_db, monkeypatch,
):
    """A task that ends while a flush is mid-write must not lose that line.

    The flush takes each message off the queue before awaiting its write, so
    cancelling it mid-write (as flush_final_progress used to) dropped that
    message from the log."""
    from optio_core import store

    task = TaskInstance(execute=_dummy, process_id="inflight", name="In flight")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    real_update_progress = store.update_progress
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def gated_update_progress(db, prefix, oid, progress):
        if progress.message == "msg-2" and not release.is_set():
            in_flight.set()
            await release.wait()
        await real_update_progress(db, prefix, oid, progress)

    monkeypatch.setattr(store, "update_progress", gated_update_progress)

    ctx.report_progress(None, "msg-1")
    await _wait_for_flush(ctx)
    ctx.report_progress(None, "msg-2")
    await asyncio.wait_for(in_flight.wait(), 60)  # the flush is now mid-write
    ctx.report_progress(None, "msg-3")
    ctx.report_progress(None, "msg-4")

    final = asyncio.create_task(ctx.flush_final_progress())
    await asyncio.sleep(0)  # let the final flush reach its first await
    release.set()
    await asyncio.wait_for(final, 60)

    messages = await _log_messages(mongo_db, "test", "inflight")
    assert messages == ["msg-1", "msg-2", "msg-3", "msg-4"]
