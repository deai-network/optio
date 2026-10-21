"""Tests for startup reconciliation and shutdown completeness.

Spec: docs/2026-04-22-process-reconciliation-design.md
"""
import asyncio
from datetime import datetime, timezone

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id


async def _noop(ctx):  # noqa: ARG001
    pass


def _make_active_seed(process_id: str, name: str, state: str) -> dict:
    """Minimal process doc to seed the DB in a given active state."""
    status: dict = {"state": state}
    if state == "running":
        status["runningSince"] = datetime.now(timezone.utc)
    return {
        "processId": process_id,
        "name": name,
        "params": {},
        "metadata": {},
        "parentId": None,
        "rootId": None,
        "depth": 0,
        "order": 0,
        "adhoc": False,
        "ephemeral": False,
        "status": status,
        "progress": {"percent": None, "message": None},
        "log": [],
        "createdAt": datetime.now(timezone.utc),
        "widgetUpstream": {
            "url": "http://127.0.0.1:45678",
            "innerAuth": None,
        },
        "widgetData": {"iframe": True},
    }


async def test_startup_reconciles_all_active_states(mongo_db):
    """Any process in scheduled/running/cancel_requested/cancelling is reconciled to failed."""
    prefix = "recontest"
    coll = mongo_db[f"{prefix}_processes"]

    await coll.insert_many([
        _make_active_seed("p_sched", "Scheduled", "scheduled"),
        _make_active_seed("p_run", "Running", "running"),
        _make_active_seed("p_creq", "CancelReq", "cancel_requested"),
        _make_active_seed("p_cing", "Cancelling", "cancelling"),
        # Terminal states must NOT be touched
        {**_make_active_seed("p_done", "Done", "running"), "status": {"state": "done"}},
        {**_make_active_seed("p_idle", "Idle", "running"), "status": {"state": "idle"}},
    ])

    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(execute=_noop, process_id=pid, name=pid)
            for pid in ("p_sched", "p_run", "p_creq", "p_cing", "p_done", "p_idle")
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        for pid in ("p_sched", "p_run", "p_creq", "p_cing"):
            proc = await get_process_by_process_id(mongo_db, prefix, pid)
            assert proc is not None, pid
            assert proc["status"]["state"] == "failed", f"{pid}: {proc['status']}"
            assert "restart" in (proc["status"]["error"] or "").lower(), pid
            assert proc["status"]["failedAt"] is not None, pid
            assert any(
                "reconcil" in entry["message"].lower() for entry in proc["log"]
            ), f"{pid}: no reconcile log entry"
            # widgetUpstream must be cleared; widgetData must be preserved.
            assert proc.get("widgetUpstream") is None, (
                f"{pid}: widgetUpstream not cleared: {proc.get('widgetUpstream')!r}"
            )
            assert proc.get("widgetData") == {"iframe": True}, (
                f"{pid}: widgetData must be preserved across terminal: "
                f"{proc.get('widgetData')!r}"
            )

        # Untouched
        done = await get_process_by_process_id(mongo_db, prefix, "p_done")
        assert done["status"]["state"] == "done"
        # Terminal rows that were already terminal must not have widgetUpstream
        # touched by reconciliation — the teardown path owns them.
        assert done.get("widgetUpstream") == {
            "url": "http://127.0.0.1:45678", "innerAuth": None,
        }, f"p_done's widgetUpstream should not be touched: {done.get('widgetUpstream')!r}"
        idle = await get_process_by_process_id(mongo_db, prefix, "p_idle")
        assert idle["status"]["state"] == "idle"
        assert idle.get("widgetUpstream") == {
            "url": "http://127.0.0.1:45678", "innerAuth": None,
        }, f"p_idle's widgetUpstream should not be touched: {idle.get('widgetUpstream')!r}"
    finally:
        await fw.shutdown()


async def test_startup_deletes_interrupted_ephemeral_processes(mongo_db):
    """An ephemeral process interrupted by a restart has ended, so it is
    deleted (with its descendants) instead of being kept as failed."""
    prefix = "recontest_eph"
    coll = mongo_db[f"{prefix}_processes"]
    eph = await coll.insert_one(
        {**_make_active_seed("p_eph", "Ephemeral", "running"), "adhoc": True, "ephemeral": True},
    )
    await coll.insert_one({
        **_make_active_seed("p_eph_child", "Child", "running"),
        "parentId": eph.inserted_id, "rootId": eph.inserted_id, "depth": 1,
    })
    await coll.insert_one(_make_active_seed("p_kept", "Kept", "running"))

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    try:
        assert await get_process_by_process_id(mongo_db, prefix, "p_eph") is None
        assert await get_process_by_process_id(mongo_db, prefix, "p_eph_child") is None
        kept = await get_process_by_process_id(mongo_db, prefix, "p_kept")
        assert kept["status"]["state"] == "failed"
    finally:
        await fw.shutdown()


async def test_startup_deletes_every_ephemeral_leftover_whatever_its_state(mongo_db):
    """An ephemeral record's TaskInstance lived in the memory of the engine
    that made it, so after a restart the record can never run again: init()
    deletes every one, with its descendants, whatever its state -- one whose
    deletion after its end was lost (here a settled orphan, kept as failed),
    and an idle one that was never launched (a crash between adhoc_define and
    launch). Other records stay (a kept factory launch among them: ad-hoc,
    but not ephemeral), and so does another engine's (another prefix)
    ephemeral one."""
    prefix = "recontest_eph_any"
    coll = mongo_db[f"{prefix}_processes"]
    ended = await coll.insert_one({
        **_make_active_seed("p_eph_failed", "Ended", "running"),
        "status": {"state": "failed"}, "adhoc": True, "ephemeral": True,
    })
    await coll.insert_one({
        **_make_active_seed("p_eph_failed_child", "Its child", "running"),
        "status": {"state": "done"},
        "parentId": ended.inserted_id, "rootId": ended.inserted_id, "depth": 1,
    })
    factory = await coll.insert_one({
        **_make_active_seed("p_factory", "Factory", "running"), "status": {"state": "idle"},
    })
    await coll.insert_one({
        **_make_active_seed("p_factory:0a1b2c3d", "Never launched", "running"),
        "status": {"state": "idle"}, "adhoc": True, "ephemeral": True,
        "parentId": factory.inserted_id, "rootId": factory.inserted_id, "depth": 1,
    })
    # A launch its factory's TTL keeps: ad-hoc, but not ephemeral.
    await coll.insert_one({
        **_make_active_seed("p_factory:4e5f6a7b", "Kept launch", "running"),
        "status": {"state": "done"}, "adhoc": True, "ephemeral": False,
        "metadata": {"taskFactory": "p_factory"}, "ttlSeconds": 3600,
        "parentId": factory.inserted_id, "rootId": factory.inserted_id, "depth": 1,
    })
    await coll.insert_one({
        **_make_active_seed("p_done", "Done", "running"), "status": {"state": "done"},
    })
    other = mongo_db["recontest_eph_other_processes"]
    await other.insert_one({
        **_make_active_seed("p_other", "Another engine's", "running"),
        "status": {"state": "failed"}, "adhoc": True, "ephemeral": True,
    })

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    try:
        for pid in ("p_eph_failed", "p_eph_failed_child", "p_factory:0a1b2c3d"):
            assert await get_process_by_process_id(mongo_db, prefix, pid) is None, pid
        factory_rec = await get_process_by_process_id(mongo_db, prefix, "p_factory")
        assert factory_rec["status"]["state"] == "idle"
        kept = await get_process_by_process_id(mongo_db, prefix, "p_factory:4e5f6a7b")
        assert kept is not None and kept["status"]["state"] == "done"
        done = await get_process_by_process_id(mongo_db, prefix, "p_done")
        assert done["status"]["state"] == "done"
        other_rec = await get_process_by_process_id(mongo_db, "recontest_eph_other", "p_other")
        assert other_rec is not None and other_rec["status"]["state"] == "failed"
    finally:
        await fw.shutdown()


async def test_startup_reconciliation_is_noop_on_fresh_db(mongo_db):
    """Startup reconciliation does nothing when no active-state rows exist."""
    prefix = "recontest_clean"

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=_noop, process_id="fresh", name="Fresh")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        proc = await get_process_by_process_id(mongo_db, prefix, "fresh")
        assert proc["status"]["state"] == "idle"
        assert proc["log"] == []
    finally:
        await fw.shutdown()


async def test_shutdown_force_finalizes_uncooperative_task(mongo_db):
    """A task that does not respond to cancellation in time is marked failed
    and has its widgetUpstream cleared."""
    prefix = "shutdowntest"

    started = asyncio.Event()

    async def uncooperative(ctx):
        # Pretend we registered an upstream; the stuck task never gets to
        # clear it via its own teardown, so force-finalize must.
        await ctx.set_widget_upstream("http://127.0.0.1:45678")
        started.set()
        await asyncio.sleep(30)  # ignore any cancellation signal

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=uncooperative, process_id="stuck", name="Stuck")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("stuck", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    # Sanity: upstream was actually set before shutdown.
    pre = await get_process_by_process_id(mongo_db, prefix, "stuck")
    assert pre["widgetUpstream"] is not None

    await fw.shutdown(grace_seconds=0.2)

    proc = await get_process_by_process_id(mongo_db, prefix, "stuck")
    assert proc["status"]["state"] == "failed"
    assert "grace" in (proc["status"]["error"] or "").lower(), proc["status"]["error"]
    assert proc["status"]["failedAt"] is not None
    assert proc.get("widgetUpstream") is None, (
        f"widgetUpstream should be cleared on force-finalize: {proc.get('widgetUpstream')!r}"
    )


async def test_shutdown_leaves_cooperative_task_alone(mongo_db):
    """Shutdown does not overwrite the terminal state a task flushed itself."""
    prefix = "shutdowncoop"

    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=cooperative, process_id="nice", name="Nice")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("nice", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "nice")
    assert proc["status"]["state"] == "cancelled", proc["status"]
    # Explicitly: should NOT carry the shutdown-timeout error
    assert not proc["status"].get("error")


async def test_shutdown_leaves_cooperative_task_widget_upstream_alone(mongo_db):
    """The force-finalize conditional update does not clobber widgetUpstream
    for a task that flushed its own terminal state inside the grace period.

    This pins the invariant that widgetUpstream clearing by
    _write_force_cancelled_state is scoped to the same conditional
    (status.state in ACTIVE_STATES) as the state write — we must not
    race a cooperative task's own teardown.
    """
    prefix = "shutdowncoop_widget"

    started = asyncio.Event()

    async def cooperative(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:45678")
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)
        # Teardown: the executor's normal-return path will clear
        # widgetUpstream via clear_widget_upstream in _execute_process.

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=cooperative, process_id="nice", name="Nice")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("nice", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "nice")
    # The cooperative task reaches a terminal state via the executor's
    # normal path, so status is cancelled, not failed, and
    # _force_finalize's conditional skips this row.
    assert proc["status"]["state"] == "cancelled", proc["status"]
    assert not proc["status"].get("error")
    # widgetUpstream is cleared by the executor's teardown, not by
    # _force_finalize — that's fine; the invariant we are pinning is
    # that the task owns the field through its terminal transition.
    assert proc.get("widgetUpstream") is None
