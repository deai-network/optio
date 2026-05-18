"""Outcome dataclass smoke + Optio._resolve + public-verb outcome coverage."""

import logging

import pytest
from bson import ObjectId

from optio_core.lifecycle import Optio
from optio_core.models import (
    TaskInstance,
    LaunchOutcome, CancelOutcome, DismissOutcome,
)


def test_launch_outcome_ok():
    out = LaunchOutcome(ok=True)
    assert out.ok is True
    assert out.reason is None


def test_launch_outcome_failure_reason():
    out = LaunchOutcome(ok=False, reason="not-found")
    assert out.ok is False
    assert out.reason == "not-found"


def test_cancel_outcome_failure_reason():
    out = CancelOutcome(ok=False, reason="not-cancellable")
    assert out.ok is False
    assert out.reason == "not-cancellable"


def test_dismiss_outcome_failure_reason():
    out = DismissOutcome(ok=False, reason="not-dismissable")
    assert out.ok is False
    assert out.reason == "not-dismissable"


def test_outcomes_top_level_reexport():
    import optio_core
    assert optio_core.LaunchOutcome is LaunchOutcome
    assert optio_core.CancelOutcome is CancelOutcome
    assert optio_core.DismissOutcome is DismissOutcome


# -------------------------------------------------------------------- _resolve

@pytest.mark.asyncio
async def test_resolve_by_process_id(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="probe", name="Probe"),
    )

    doc = await fw._resolve("probe")
    assert doc is not None
    assert doc["processId"] == "probe"


@pytest.mark.asyncio
async def test_resolve_by_objectid_hex(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest2")
    async def noop(ctx):
        pass
    proc = await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="probe", name="Probe"),
    )

    doc = await fw._resolve(str(proc["_id"]))
    assert doc is not None
    assert doc["_id"] == proc["_id"]


@pytest.mark.asyncio
async def test_resolve_missing(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest3")
    assert await fw._resolve("does-not-exist") is None
    assert await fw._resolve(str(ObjectId())) is None


# ---------------------------------------------------------------------- cancel


@pytest.mark.asyncio
async def test_cancel_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest")
    out = await fw.cancel("nonexistent")
    assert out == CancelOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_cancel_not_cancellable_when_idle(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest2")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="idle1", name="Idle"),
    )

    out = await fw.cancel("idle1")
    assert out == CancelOutcome(ok=False, reason="not-cancellable")


@pytest.mark.asyncio
async def test_cancel_returns_ok_when_scheduled(mongo_db):
    """Direct DB seed of a 'scheduled' process — cancel transitions it to 'cancelled'."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest3")
    coll = mongo_db["canceltest3_processes"]
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "sched1",
        "status": {"state": "scheduled"},
        "cancellable": True,
    })

    out = await fw.cancel("sched1")
    assert out.ok is True
    assert out.proc is not None
    assert out.proc["status"]["state"] == "cancelled"

    proc = await fw.get_process("sched1")
    assert proc["status"]["state"] == "cancelled"


# --------------------------------------------------------------------- dismiss


@pytest.mark.asyncio
async def test_dismiss_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest")
    out = await fw.dismiss("nonexistent")
    assert out == DismissOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_dismiss_not_dismissable_when_idle(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest2")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="idle1", name="Idle"),
    )

    out = await fw.dismiss("idle1")
    assert out == DismissOutcome(ok=False, reason="not-dismissable")


@pytest.mark.asyncio
async def test_dismiss_ok_from_done(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest3")
    coll = mongo_db["dismisstest3_processes"]
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "done1",
        "status": {"state": "done"},
    })

    out = await fw.dismiss("done1")
    assert out.ok is True
    assert out.proc is not None
    assert out.proc["status"]["state"] == "idle"

    proc = await fw.get_process("done1")
    assert proc["status"]["state"] == "idle"


# ---------------------------------------------------------------------- launch


@pytest.mark.asyncio
async def test_launch_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest")
    out = await fw.launch("nonexistent")
    assert out == LaunchOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_launch_not_launchable_when_already_running(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest2")
    coll = mongo_db["launchtest2_processes"]
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "running1",
        "status": {"state": "running"},
    })

    out = await fw.launch("running1")
    assert out == LaunchOutcome(ok=False, reason="not-launchable")


@pytest.mark.asyncio
async def test_launch_no_resume_support(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest3")
    coll = mongo_db["launchtest3_processes"]
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "noresume1",
        "status": {"state": "idle"},
        "supportsResume": False,
    })

    out = await fw.launch("noresume1", resume=True)
    assert out == LaunchOutcome(ok=False, reason="no-resume-support")


@pytest.mark.asyncio
async def test_launch_blocked_outcome(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest4")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(
            execute=noop, process_id="blocked1", name="Blocked",
            metadata={"project": "p1"},
        ),
    )

    async with fw.block_launches({"project": "p1"}):
        out = await fw.launch("blocked1")

    assert out == LaunchOutcome(ok=False, reason="launch-blocked")


# -------------------------------------------------------- scheduler adapter


@pytest.mark.asyncio
async def test_scheduler_adapter_logs_warning_when_blocked(mongo_db, caplog):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="schedtest")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(
            execute=noop, process_id="sched-blocked", name="Sched-Blocked",
            metadata={"project": "p1"},
        ),
    )

    caplog.set_level(logging.WARNING)
    async with fw.block_launches({"project": "p1"}):
        await fw._scheduler_launch_adapter("sched-blocked")

    assert any(
        rec.levelno == logging.WARNING
        and "sched-blocked" in rec.message
        and "launch-blocked" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
