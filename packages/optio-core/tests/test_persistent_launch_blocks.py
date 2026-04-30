"""Tests for persistent launch blocks ("perma-ban").

Spec: docs/2026-04-30-persistent-launch-blocks-design.md
"""
import asyncio
import pytest

from optio_core import _launch_block_store as store
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


async def _start_optio(mongo_db, prefix, tasks=(), cancel_grace_seconds=2.0):
    """Helper: init an Optio with the given tasks, return (optio, run_task)."""
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run())
    return optio, run_task


async def _stop_optio(optio, run_task):
    await optio.shutdown()
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass


# ---------- Store-level tests ----------

def _coll(mongo_db, prefix="optio_test"):
    return store.collection(mongo_db, prefix)


async def test_load_all_empty(mongo_db):
    """load_all returns [] for a fresh / missing collection."""
    rows = await store.load_all(_coll(mongo_db))
    assert rows == []


async def test_upsert_inserts_record(mongo_db):
    """First upsert with a new filter inserts a record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="bad behavior")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    row = rows[0]
    assert row.filter == {"tenant": "acme"}
    assert row.reason == "bad behavior"
    # createdAt is set
    assert row.created_at is not None


async def test_upsert_dedupes_by_exact_filter(mongo_db):
    """Second upsert with the same filter does NOT create a new record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="x")
    await store.upsert_block(coll, {"tenant": "acme"}, reason="y")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    # Reason concatenated when both sides non-null.
    assert rows[0].reason == "x AND y"


async def test_upsert_reason_concat_null_handling(mongo_db):
    """Reason concat skips when either side is null."""
    coll = _coll(mongo_db)

    # Existing reason None, new reason set -> existing kept (None).
    await store.upsert_block(coll, {"a": 1}, reason=None)
    await store.upsert_block(coll, {"a": 1}, reason="later")
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"a": 1}] == [None]

    # Existing reason set, new reason None -> existing kept.
    await store.upsert_block(coll, {"b": 2}, reason="initial")
    await store.upsert_block(coll, {"b": 2}, reason=None)
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"b": 2}] == ["initial"]


async def test_upsert_different_filter_inserts_separate_record(mongo_db):
    """Different filters yield separate records."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    rows = await store.load_all(coll)
    filters = sorted([tuple(sorted(r.filter.items())) for r in rows])
    assert filters == [(("tenant", "acme"),), (("tenant", "globex"),)]


async def test_delete_by_filter_removes_matching(mongo_db):
    """delete_by_filter deletes all matching rows; returns count."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "acme"})
    assert deleted == 1

    rows = await store.load_all(coll)
    assert len(rows) == 1
    assert rows[0].filter == {"tenant": "globex"}


async def test_delete_by_filter_no_match_is_noop(mongo_db):
    """delete_by_filter returns 0 when no record matches."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "nonexistent"})
    assert deleted == 0
    rows = await store.load_all(coll)
    assert len(rows) == 1


# ---------- block_launches(persist=True) ----------

async def test_block_launches_persist_writes_record_and_blocks(mongo_db):
    """persist=True writes a Mongo record and the block stays after exit."""
    optio, run_task = await _start_optio(mongo_db, "optio_p1", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="r"):
            pass  # exit immediately
        # Memory: block is still there.
        assert any(
            entry.filter == {"tenant": "acme"} and entry.reason == "r"
            for entry in optio._launch_blocks.values()
        )
        # DB: one record.
        rows = await store.load_all(store.collection(mongo_db, "optio_p1"))
        assert len(rows) == 1
        assert rows[0].reason == "r"
    finally:
        await _stop_optio(optio, run_task)


async def test_block_launches_persist_false_unchanged(mongo_db):
    """persist=False (default) behaves exactly as today: block lifted on exit, no DB record."""
    optio, run_task = await _start_optio(mongo_db, "optio_p2", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}):
            assert len(optio._launch_blocks) == 1
        assert len(optio._launch_blocks) == 0
        rows = await store.load_all(store.collection(mongo_db, "optio_p2"))
        assert rows == []
    finally:
        await _stop_optio(optio, run_task)


async def test_block_launches_persist_dedupes_db_keeps_memory(mongo_db):
    """Two persist calls with same filter -> one DB row, two memory entries."""
    optio, run_task = await _start_optio(mongo_db, "optio_p3", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="a"):
            pass
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="b"):
            pass
        rows = await store.load_all(store.collection(mongo_db, "optio_p3"))
        assert len(rows) == 1
        assert rows[0].reason == "a AND b"
        # Memory: two entries (one per call).
        matching = [e for e in optio._launch_blocks.values() if e.filter == {"tenant": "acme"}]
        assert len(matching) == 2
    finally:
        await _stop_optio(optio, run_task)


async def test_launch_blocked_message_includes_reason(mongo_db):
    """LaunchBlocked.args[0] includes '; reason=...' when block has a reason."""
    async def _noop_execute(ctx):  # pragma: no cover - never reached
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_banned",
        name="banned task",
        metadata={"tenant": "acme"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_p4", tasks=(task,))
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="r"):
            pass  # block stays
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio.launch("p_banned")
        assert "; reason=r" in str(excinfo.value)
    finally:
        await _stop_optio(optio, run_task)
