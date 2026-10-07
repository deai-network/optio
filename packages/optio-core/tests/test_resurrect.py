"""Optio.resurrect: preconditions, background save, resume, failure handling."""

import asyncio
import time

import pytest

from optio_core import NothingToResurrect, ResurrectOutcome
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _noop(ctx):
    pass


async def _wait_until(pred, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached within the hang ceiling")


async def _setup(
    mongo_db, prefix, hook, *, state="failed", unsaved=True, metadata=None, **init_kwargs,
):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, **init_kwargs)
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="r1", name="R1", supports_resume=True,
        resurrect=hook, metadata=metadata or {},
    ))
    coll = mongo_db[f"{prefix}_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": state, "hasUnsavedWork": unsaved, "hasSavedState": True,
    }})
    launched = []

    async def _spy(oid, *, resume, session_id):
        launched.append((oid, resume, session_id))
    fw._executor.launch_process = _spy
    return fw, coll, proc, launched


@pytest.mark.asyncio
async def test_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="res0")
    assert await fw.resurrect("missing", session_id=None) == ResurrectOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_no_resurrect_support(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="res1")
    await fw.adhoc_define(TaskInstance(execute=_noop, process_id="plain", name="Plain"))
    out = await fw.resurrect("plain", session_id=None)
    assert out == ResurrectOutcome(ok=False, reason="no-resurrect-support")


@pytest.mark.asyncio
async def test_not_resurrectable_without_flag_or_when_active(mongo_db):
    async def hook(ctx):
        raise AssertionError("must not run")
    fw, coll, proc, _ = await _setup(mongo_db, "res2", hook, unsaved=False)
    assert (await fw.resurrect("r1", session_id=None)).reason == "not-resurrectable"
    await coll.update_one({"_id": proc["_id"]}, {"$set": {"hasUnsavedWork": True, "status.state": "running"}})
    assert (await fw.resurrect("r1", session_id=None)).reason == "not-resurrectable"


@pytest.mark.asyncio
async def test_success_clears_flag_and_resumes(mongo_db):
    calls = []

    async def hook(ctx):
        calls.append(ctx.process_id)
        ctx.report_progress(None, "hook ran")
    fw, coll, proc, launched = await _setup(mongo_db, "res3", hook)
    out = await fw.resurrect("r1", session_id="s1")
    assert out.ok is True and out.proc["_id"] == proc["_id"]

    async def done():
        return bool(launched)
    await _wait_until(done)
    assert calls == ["r1"]
    assert launched == [(str(proc["_id"]), True, "s1")]
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is False
    messages = [e["message"] for e in doc["log"]]
    assert "Resurrect requested" in messages
    assert any(m.startswith("Resurrected") for m in messages)
    assert proc["_id"] not in fw._resurrecting


@pytest.mark.asyncio
async def test_nothing_to_resurrect_clears_flag_without_resume(mongo_db):
    async def hook(ctx):
        raise NothingToResurrect("workdir no longer on the host")
    fw, coll, proc, launched = await _setup(mongo_db, "res4", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True

    async def flag_cleared():
        doc = await coll.find_one({"_id": proc["_id"]})
        return doc["hasUnsavedWork"] is False and proc["_id"] not in fw._resurrecting
    await _wait_until(flag_cleared)
    assert launched == []
    doc = await coll.find_one({"_id": proc["_id"]})
    assert any("workdir no longer on the host" in e["message"] for e in doc["log"])
    assert doc["progress"]["message"] is None


@pytest.mark.asyncio
async def test_hook_error_keeps_flag(mongo_db):
    async def hook(ctx):
        raise RuntimeError("disk full")
    fw, coll, proc, launched = await _setup(mongo_db, "res5", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True

    async def finished():
        return proc["_id"] not in fw._resurrecting
    await _wait_until(finished)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is True
    assert launched == []
    assert any(e["level"] == "error" and "disk full" in e["message"] for e in doc["log"])


@pytest.mark.asyncio
async def test_second_resurrect_and_launch_refused_while_running(mongo_db):
    release = asyncio.Event()

    async def hook(ctx):
        await release.wait()
    fw, coll, proc, launched = await _setup(mongo_db, "res6", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    again = await fw.resurrect("r1", session_id=None)
    assert again.reason == "resurrect-in-progress"
    blocked = await fw.launch("r1", resume=True, session_id=None)
    assert blocked.ok is False and blocked.reason == "not-launchable"
    release.set()

    async def done():
        return bool(launched)
    await _wait_until(done)


@pytest.mark.asyncio
async def test_launch_blocked_refused_up_front(mongo_db):
    async def hook(ctx):
        raise AssertionError("must not run")
    fw, *_ = await _setup(mongo_db, "res7", hook, metadata={"project": "p1"})
    async with fw.block_launches({"project": "p1"}):
        out = await fw.resurrect("r1", session_id=None)
    assert out.reason == "launch-blocked"


@pytest.mark.asyncio
async def test_resurrect_success_but_launch_blocked(mongo_db):
    gate = asyncio.Event()

    async def hook(ctx):
        await gate.wait()
    fw, coll, proc, launched = await _setup(mongo_db, "res8", hook, metadata={"project": "p1"})
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    async with fw.block_launches({"project": "p1"}):
        gate.set()

        async def finished():
            return proc["_id"] not in fw._resurrecting and any(
                "Resume after resurrect not started" in e["message"]
                for e in (await coll.find_one({"_id": proc["_id"]}))["log"]
            )
        await _wait_until(finished)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is False
    assert launched == []


@pytest.mark.asyncio
async def test_shutdown_cancels_running_resurrect_and_keeps_flag(mongo_db):
    started = asyncio.Event()

    async def hook(ctx):
        started.set()
        await asyncio.Event().wait()
    fw, coll, proc, _ = await _setup(mongo_db, "res9", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    await asyncio.wait_for(started.wait(), 60)
    await fw.shutdown(grace_seconds=0.1)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is True
    assert fw._resurrecting == {}
    assert (await fw.resurrect("r1", session_id=None)).reason == "shutting-down"


@pytest.mark.asyncio
async def test_shutdown_wait_on_stubborn_resurrect_is_bounded(mongo_db):
    started = asyncio.Event()

    async def hook(ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()
    fw, coll, proc, _ = await _setup(
        mongo_db, "res10", hook, force_cancel_shield_seconds=0.2,
    )
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    stubborn = fw._resurrecting[proc["_id"]]
    await asyncio.wait_for(started.wait(), 60)
    await asyncio.wait_for(fw.shutdown(grace_seconds=0.1), 60)
    assert fw._resurrecting == {}
    assert not stubborn.done()
    stubborn.cancel()
    await asyncio.gather(stubborn, return_exceptions=True)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is True


async def _resurrecting_on_doc(ctx):
    doc = await ctx._db[f"{ctx._prefix}_processes"].find_one({"_id": ctx._process_oid})
    return doc.get("resurrecting")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "nothing", "error"])
async def test_resurrecting_marker_set_while_hook_runs(mongo_db, outcome):
    """`resurrecting` is True on the doc while the hook runs and False once the
    resurrect is over, whatever the hook did; False before the resume launch."""
    seen = []

    async def hook(ctx):
        seen.append(await _resurrecting_on_doc(ctx))
        if outcome == "nothing":
            raise NothingToResurrect("workdir no longer on the host")
        if outcome == "error":
            raise RuntimeError("disk full")
    fw, coll, proc, _ = await _setup(mongo_db, f"res11-{outcome}", hook)
    at_launch = []

    async def _spy(oid, *, resume, session_id):
        at_launch.append((await coll.find_one({"_id": proc["_id"]})).get("resurrecting"))
    fw._executor.launch_process = _spy
    assert (await fw.resurrect("r1", session_id=None)).ok is True

    async def finished():
        if proc["_id"] in fw._resurrecting:
            return False
        return outcome != "success" or bool(at_launch)
    await _wait_until(finished)
    assert seen == [True]
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["resurrecting"] is False
    assert at_launch == ([False] if outcome == "success" else [])


@pytest.mark.asyncio
async def test_init_clears_stale_resurrecting_marker(mongo_db):
    """A marker left by an engine that died mid-resurrect is cleared on startup."""
    prefix = "res12"
    coll = mongo_db[f"{prefix}_processes"]
    await coll.insert_one({
        "processId": "r1", "name": "R1", "params": {}, "metadata": {},
        "parentId": None, "rootId": None, "depth": 0, "order": 0,
        "adhoc": False, "ephemeral": False,
        "status": {"state": "failed"},
        "progress": {"percent": None, "message": None}, "log": [],
        "hasUnsavedWork": True, "resurrecting": True,
    })

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r1", name="R1", supports_resume=True,
            resurrect=_noop,
        )]
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    doc = await coll.find_one({"processId": "r1"})
    assert doc["resurrecting"] is False
    assert doc["status"]["state"] == "failed"
    assert doc["hasUnsavedWork"] is True


def test_resurrect_exported_from_package():
    """Bound to the module-level singleton, like launch."""
    import optio_core
    assert optio_core.resurrect == optio_core._instance.resurrect
    assert "resurrect" in optio_core.__all__


@pytest.mark.asyncio
async def test_background_failure_is_logged(mongo_db, monkeypatch, caplog):
    """An exception escaping the background run (here the post-hook Mongo
    write) is logged by the task's done-callback, and asyncio never reports
    "Task exception was never retrieved"."""
    import gc
    import logging
    from optio_core import lifecycle as L

    async def hook(ctx):
        pass
    fw, coll, proc, launched = await _setup(mongo_db, "res13", hook)

    async def _boom(db, prefix, oid, value):
        raise RuntimeError("mongo write failed")
    monkeypatch.setattr(L, "set_has_unsaved_work", _boom)
    caplog.set_level(logging.ERROR)
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    tasks = [fw._resurrecting[proc["_id"]]]

    async def finished():
        return tasks[0].done()
    await _wait_until(finished)
    await asyncio.sleep(0)  # a yield: done-callbacks run before this resumes
    tasks.clear()  # the last reference: the task is collected below
    gc.collect()
    messages = [r.getMessage() for r in caplog.records]
    ours = [
        r for r in caplog.records
        if r.name == "optio_core_core" and "r1" in r.getMessage()
        and r.exc_info and "mongo write failed" in str(r.exc_info[1])
    ]
    assert ours, messages
    assert not any("never retrieved" in m for m in messages), messages
    assert launched == []
