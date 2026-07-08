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
        # Optio.launch returns an outcome with a typed (non-message) reason;
        # the human-readable block reason lives on the raising surfaces.
        # Use launch_and_wait (still raises) to assert the reason text.
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio.launch_and_wait("p_banned", session_id=None)
        assert "; reason=r" in str(excinfo.value)
    finally:
        await _stop_optio(optio, run_task)


# ---------- Load-on-init ----------

async def test_init_loads_persisted_blocks(mongo_db):
    """Pre-existing records in `{prefix}_launch_blocks` block matching launches after init."""
    # Seed the DB before init.
    coll = store.collection(mongo_db, "optio_init1")
    await store.upsert_block(coll, {"tenant": "banned"}, reason="precooked")

    async def _noop_execute(ctx):  # pragma: no cover - never reached
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_x",
        name="x",
        metadata={"tenant": "banned"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_init1", tasks=(task,))
    try:
        # The pre-existing block must be loaded.
        assert any(
            entry.filter == {"tenant": "banned"} and entry.reason == "precooked"
            for entry in optio._launch_blocks.values()
        )
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio.launch_and_wait("p_x", session_id=None)
        assert "; reason=precooked" in str(excinfo.value)
    finally:
        await _stop_optio(optio, run_task)


async def test_init_with_no_blocks_works(mongo_db):
    """Empty / missing collection produces an empty load."""
    optio, run_task = await _start_optio(mongo_db, "optio_init2", tasks=())
    try:
        assert optio._launch_blocks == {}
    finally:
        await _stop_optio(optio, run_task)


# ---------- unblock_launches ----------

async def test_unblock_launches_removes_memory_and_db(mongo_db):
    """unblock_launches removes matching memory entries and DB rows; returns count."""
    optio, run_task = await _start_optio(mongo_db, "optio_u1", tasks=())
    try:
        async with optio.block_launches({"a": 1}, persist=True, reason="r1"):
            pass
        async with optio.block_launches({"a": 1}, persist=True, reason="r2"):
            pass
        async with optio.block_launches({"b": 2}, persist=True, reason=None):
            pass
        # Sanity: 3 memory entries, 2 DB rows ({a:1} dedup'd, {b:2} separate).
        assert len(optio._launch_blocks) == 3
        rows = await store.load_all(store.collection(mongo_db, "optio_u1"))
        assert len(rows) == 2

        removed = await optio.unblock_launches({"a": 1})
        assert removed == 2  # two memory entries with filter {a:1}

        # Memory: only {b:2} remains.
        assert all(e.filter != {"a": 1} for e in optio._launch_blocks.values())
        # DB: only {b:2} remains.
        rows = await store.load_all(store.collection(mongo_db, "optio_u1"))
        assert len(rows) == 1
        assert rows[0].filter == {"b": 2}
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_launches_no_match_returns_zero(mongo_db):
    """No match -> 0; no error."""
    optio, run_task = await _start_optio(mongo_db, "optio_u2", tasks=())
    try:
        removed = await optio.unblock_launches({"never": "set"})
        assert removed == 0
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_removes_transient_block_too(mongo_db):
    """Transient (persist=False) blocks with the same filter are also removed."""
    optio, run_task = await _start_optio(mongo_db, "optio_u3", tasks=())
    try:
        # Install a persistent and a transient block with same filter.
        async with optio.block_launches({"x": 1}, persist=True, reason=None):
            pass
        async with optio.block_launches({"x": 1}):
            assert len(optio._launch_blocks) == 2

            removed = await optio.unblock_launches({"x": 1})
            # Both removed (persistent + transient).
            assert removed == 2
            assert len(optio._launch_blocks) == 0
        # Transient CM exit tolerates the missing token (pop with default).
        assert len(optio._launch_blocks) == 0
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_persisted_block_then_launch_succeeds(mongo_db):
    """After unblock, matching launches go through."""
    completed = asyncio.Event()

    async def _execute(ctx):
        completed.set()

    task = TaskInstance(
        execute=_execute,
        process_id="p_y",
        name="y",
        metadata={"tenant": "banned"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_u4", tasks=(task,))
    try:
        async with optio.block_launches({"tenant": "banned"}, persist=True, reason=None):
            pass
        out = await optio.launch("p_y", session_id=None)
        assert out.ok is False
        assert out.reason == "launch-blocked"

        await optio.unblock_launches({"tenant": "banned"})

        await optio.launch_and_wait("p_y", session_id=None)
        assert completed.is_set()
    finally:
        await _stop_optio(optio, run_task)


# ---------- group_cancel(persist=...) ----------

async def test_group_cancel_persist_requires_block_new_launches(mongo_db):
    """persist=True with block_new_launches=False raises ValueError."""
    optio = Optio()
    with pytest.raises(ValueError, match="block_new_launches"):
        await optio.group_cancel(
            {"tenant": "x"}, block_new_launches=False, persist=True, reason=None,
        )


async def test_group_cancel_persist_installs_persistent_block(mongo_db):
    """persist=True + block_new_launches=True writes a record and the block survives."""
    completed = asyncio.Event()

    async def _execute(ctx):
        completed.set()

    task = TaskInstance(
        execute=_execute,
        process_id="p_gc",
        name="gc",
        metadata={"tenant": "acme"},
    )
    optio, run_task = await _start_optio(mongo_db, "optio_gc1", tasks=(task,))
    try:
        await optio.group_cancel(
            {"tenant": "acme"},
            block_new_launches=True,
            persist=True,
            reason="banned",
        )
        # After group_cancel returns, the block must still be in memory.
        assert any(
            e.filter == {"tenant": "acme"} and e.reason == "banned"
            for e in optio._launch_blocks.values()
        )
        # And in the DB.
        rows = await store.load_all(store.collection(mongo_db, "optio_gc1"))
        assert len(rows) == 1
        assert rows[0].reason == "banned"

        # Subsequent launches blocked.
        out = await optio.launch("p_gc", session_id=None)
        assert out.ok is False
        assert out.reason == "launch-blocked"
    finally:
        await _stop_optio(optio, run_task)


# ---------- group_cancel_and_wait(persist=...) ----------

async def test_group_cancel_and_wait_persist_requires_block_new_launches(mongo_db):
    """persist=True with block_new_launches=False raises ValueError."""
    optio = Optio()
    with pytest.raises(ValueError, match="block_new_launches"):
        await optio.group_cancel_and_wait(
            {"tenant": "x"}, block_new_launches=False, persist=True, reason=None,
        )


async def test_group_cancel_and_wait_persist_installs_persistent_block(mongo_db):
    """persist=True + block_new_launches=True: blocks survives the wait, record written."""
    started = asyncio.Event()

    async def _execute(ctx):
        started.set()
        try:
            await asyncio.sleep(60)  # will be cancelled
        except asyncio.CancelledError:
            raise

    task = TaskInstance(
        execute=_execute,
        process_id="p_gcw",
        name="gcw",
        metadata={"tenant": "acme"},
    )
    optio, run_task = await _start_optio(mongo_db, "optio_gcw1", tasks=(task,))
    try:
        # Launch one process; wait for it to start.
        await optio.launch("p_gcw", session_id=None)
        await asyncio.wait_for(started.wait(), timeout=60.0)

        await optio.group_cancel_and_wait(
            {"tenant": "acme"},
            block_new_launches=True,
            persist=True,
            reason="bw",
        )
        # Persistent block survives the wait.
        assert any(
            e.filter == {"tenant": "acme"} and e.reason == "bw"
            for e in optio._launch_blocks.values()
        )
        rows = await store.load_all(store.collection(mongo_db, "optio_gcw1"))
        assert len(rows) == 1

        # Subsequent launch blocked.
        out = await optio.launch("p_gcw", session_id=None)
        assert out.ok is False
        assert out.reason == "launch-blocked"
    finally:
        await _stop_optio(optio, run_task)


# ---------- Restart survives ----------

async def test_persistent_block_survives_restart(mongo_db):
    """Install a persistent block, dispose Optio, instantiate a new one
    against the same DB — block is reloaded and matching launches stay blocked.
    """
    async def _noop_execute(ctx):  # pragma: no cover
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_z",
        name="z",
        metadata={"tenant": "banned"},
    )

    # First run: install the block.
    optio, run_task = await _start_optio(mongo_db, "optio_restart1", tasks=(task,))
    try:
        async with optio.block_launches(
            {"tenant": "banned"}, persist=True, reason="bad",
        ):
            pass
    finally:
        await _stop_optio(optio, run_task)

    # Second run: same DB, fresh Optio instance.
    optio2, run_task2 = await _start_optio(mongo_db, "optio_restart1", tasks=(task,))
    try:
        # Block is reloaded.
        assert any(
            e.filter == {"tenant": "banned"} and e.reason == "bad"
            for e in optio2._launch_blocks.values()
        )
        # Optio.launch returns a typed outcome; the human-readable reason
        # text only surfaces via launch_and_wait (which still raises).
        out = await optio2.launch("p_z", session_id=None)
        assert out.ok is False
        assert out.reason == "launch-blocked"
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio2.launch_and_wait("p_z", session_id=None)
        assert "; reason=bad" in str(excinfo.value)
    finally:
        await _stop_optio(optio2, run_task2)
