"""Outcome dataclass smoke + Optio._resolve + public-verb outcome coverage."""

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
    assert out == CancelOutcome(ok=True)

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
    assert out == DismissOutcome(ok=True)

    proc = await fw.get_process("done1")
    assert proc["status"]["state"] == "idle"
