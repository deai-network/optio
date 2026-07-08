"""Tests for TaskInstance.ttl_seconds behaviour (B2)."""

import asyncio
from datetime import datetime, timezone

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _wait_state(mongo_db, prefix, process_id, target, timeout_s=60.0):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        doc = await mongo_db[f"{prefix}_processes"].find_one({"processId": process_id})
        if doc and doc["status"]["state"] == target:
            return doc
        await asyncio.sleep(0.02)
    pytest.fail(f"task {process_id} did not reach state {target}")


async def test_done_task_with_ttl_sets_expire_at_approximately(mongo_db):
    async def quick(ctx):
        return

    task = TaskInstance(
        execute=quick, process_id="t1", name="t1", ttl_seconds=60,
    )

    async def gen(services, metadata_filter=None):
        return [task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen,
    )
    try:
        await optio.launch("t1", session_id=None)
        doc = await _wait_state(mongo_db, "test", "t1", "done")
        assert "expireAt" in doc, f"expireAt missing on done: {doc!r}"
        # expireAt is timezone-naive when retrieved by motor with default
        # codec_options; normalize for comparison.
        expire_at = doc["expireAt"]
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        # Anchor the window to the task's OWN doneAt rather than a wall-clock
        # stamp taken before launch: under CPU starvation the task can take
        # many seconds to complete, which would inflate a before-launch delta
        # past the ceiling even though expireAt == doneAt + ttl_seconds holds.
        done_at = doc["status"]["doneAt"]
        if done_at.tzinfo is None:
            done_at = done_at.replace(tzinfo=timezone.utc)
        delta = (expire_at - done_at).total_seconds()
        assert 55 <= delta <= 65, f"expireAt - doneAt = {delta}"
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_done_task_without_ttl_omits_expire_at(mongo_db):
    async def quick(ctx):
        return

    task = TaskInstance(
        execute=quick, process_id="t1", name="t1",
    )  # no ttl_seconds

    async def gen(services, metadata_filter=None):
        return [task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen,
    )
    try:
        await optio.launch("t1", session_id=None)
        doc = await _wait_state(mongo_db, "test", "t1", "done")
        assert "expireAt" not in doc, f"expireAt should be absent: {doc!r}"
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_failed_task_with_ttl_sets_expire_at(mongo_db):
    async def boom(ctx):
        raise RuntimeError("kaboom")

    task = TaskInstance(
        execute=boom, process_id="t1", name="t1", ttl_seconds=30,
    )

    async def gen(services, metadata_filter=None):
        return [task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen,
    )
    try:
        await optio.launch("t1", session_id=None)
        doc = await _wait_state(mongo_db, "test", "t1", "failed")
        assert "expireAt" in doc
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_cancelled_task_with_ttl_sets_expire_at(mongo_db):
    started = asyncio.Event()

    async def slow(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.02)

    task = TaskInstance(
        execute=slow, process_id="t1", name="t1", ttl_seconds=45,
    )

    async def gen(services, metadata_filter=None):
        return [task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    try:
        await optio.launch("t1", session_id=None)
        await asyncio.wait_for(started.wait(), timeout=60.0)
        await _wait_state(mongo_db, "test", "t1", "running")
        terminal = await optio.cancel_and_wait("t1")
        assert terminal == "cancelled", terminal
        doc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert doc["status"]["state"] == "cancelled"
        assert "expireAt" in doc, f"expireAt missing on cancelled: {doc!r}"
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def test_early_cancel_scheduled_task_with_ttl_sets_expire_at(mongo_db):
    """The lifecycle._handle_cancel `scheduled -> cancelled` path also sets expireAt.

    A task that is scheduled but not yet running is cancelled directly to
    `cancelled` (no cooperative-cancel cycle). That terminal-state writer
    must also honour ttl_seconds — otherwise the early-cancel path leaks a
    record that the TTL feature would otherwise have evicted.
    """
    # Use a hold event to keep the task in `scheduled` state long enough
    # to issue the cancel — this requires inserting an artificial delay
    # between the upsert and launch. The scheduled-state window is
    # naturally tiny in a real launch; the simplest test is to seed
    # the DB record with state=scheduled directly.
    from optio_core.store import update_status, upsert_process
    from optio_core.models import ProcessStatus

    async def quick(ctx):
        return

    task = TaskInstance(
        execute=quick, process_id="t1", name="t1", ttl_seconds=20,
    )

    async def gen(services, metadata_filter=None):
        return [task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix="test",
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    try:
        # Force the DB record into 'scheduled' state without launching.
        proc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        await update_status(
            mongo_db, "test", proc["_id"], ProcessStatus(state="scheduled"),
        )

        await optio.cancel("t1")
        doc = await mongo_db["test_processes"].find_one({"processId": "t1"})
        assert doc["status"]["state"] == "cancelled"
        assert "expireAt" in doc, (
            f"early-cancel from scheduled must also set expireAt: {doc!r}"
        )
    finally:
        await optio.shutdown(grace_seconds=1.0)
