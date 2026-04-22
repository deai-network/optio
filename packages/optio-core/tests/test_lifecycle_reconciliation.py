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

    async def get_tasks(_services):
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

        # Untouched
        done = await get_process_by_process_id(mongo_db, prefix, "p_done")
        assert done["status"]["state"] == "done"
        idle = await get_process_by_process_id(mongo_db, prefix, "p_idle")
        assert idle["status"]["state"] == "idle"
    finally:
        await fw.shutdown()


async def test_startup_reconciliation_is_noop_on_fresh_db(mongo_db):
    """Startup reconciliation does nothing when no active-state rows exist."""
    prefix = "recontest_clean"

    async def get_tasks(_services):
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
    """A task that does not respond to cancellation in time is marked failed."""
    prefix = "shutdowntest"

    started = asyncio.Event()

    async def uncooperative(_ctx):
        started.set()
        await asyncio.sleep(30)  # ignore any cancellation signal

    async def get_tasks(_services):
        return [TaskInstance(execute=uncooperative, process_id="stuck", name="Stuck")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("stuck")
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await fw.shutdown(grace_seconds=0.2)

    proc = await get_process_by_process_id(mongo_db, prefix, "stuck")
    assert proc["status"]["state"] == "failed"
    assert "grace" in (proc["status"]["error"] or "").lower(), proc["status"]["error"]
    assert proc["status"]["failedAt"] is not None


async def test_shutdown_leaves_cooperative_task_alone(mongo_db):
    """Shutdown does not overwrite the terminal state a task flushed itself."""
    prefix = "shutdowncoop"

    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services):
        return [TaskInstance(execute=cooperative, process_id="nice", name="Nice")]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("nice")
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "nice")
    assert proc["status"]["state"] == "cancelled", proc["status"]
    # Explicitly: should NOT carry the shutdown-timeout error
    assert not proc["status"].get("error")
