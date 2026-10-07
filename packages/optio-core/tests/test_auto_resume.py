"""Tests for auto-resume-on-restart.

Spec: docs/superpowers/specs/2026-06-06-auto-resume-on-restart-design.md
"""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, OptioConfig
from optio_core.store import get_process_by_process_id, set_auto_resume_scheduled


async def _noop(ctx):  # noqa: ARG001
    pass


def test_task_instance_auto_resume_defaults_false():
    ti = TaskInstance(execute=_noop, process_id="t", name="T")
    assert ti.auto_resume is False
    ti2 = TaskInstance(
        execute=_noop, process_id="t2", name="T2",
        supports_resume=True, auto_resume=True,
    )
    assert ti2.auto_resume is True


def test_optio_config_auto_resume_delay_default():
    cfg = OptioConfig(mongo_db=None)
    assert cfg.auto_resume_delay_seconds == 300.0


async def test_init_threads_auto_resume_delay(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=_noop, process_id="p", name="P")]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix="ardelay",
        get_task_definitions=get_tasks, auto_resume_delay_seconds=0.05,
    )
    try:
        assert fw._config.auto_resume_delay_seconds == 0.05
    finally:
        await fw.shutdown()


async def test_upsert_sets_auto_resume_scheduled_false(mongo_db):
    from optio_core.store import upsert_process
    prefix = "arstore"
    ti = TaskInstance(execute=_noop, process_id="p", name="P")
    proc = await upsert_process(mongo_db, prefix, ti)
    assert proc["autoResumeScheduled"] is False


async def test_set_auto_resume_scheduled_flips_flag(mongo_db):
    from optio_core.store import upsert_process
    prefix = "arstore2"
    ti = TaskInstance(execute=_noop, process_id="p", name="P")
    proc = await upsert_process(mongo_db, prefix, ti)

    await set_auto_resume_scheduled(mongo_db, prefix, proc["_id"], True)
    again = await get_process_by_process_id(mongo_db, prefix, "p")
    assert again["autoResumeScheduled"] is True

    await set_auto_resume_scheduled(mongo_db, prefix, proc["_id"], False)
    again2 = await get_process_by_process_id(mongo_db, prefix, "p")
    assert again2["autoResumeScheduled"] is False


async def test_auto_resume_without_supports_resume_hard_fails(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(
                execute=_noop, process_id="bad", name="Bad",
                auto_resume=True, supports_resume=False,
            )
        ]

    fw = Optio()
    with pytest.raises(ValueError, match="auto_resume"):
        await fw.init(mongo_db=mongo_db, prefix="arvalid", get_task_definitions=get_tasks)


async def test_auto_resume_with_supports_resume_is_accepted(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(
                execute=_noop, process_id="good", name="Good",
                auto_resume=True, supports_resume=True,
            )
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="arvalid_ok", get_task_definitions=get_tasks)
    try:
        proc = await get_process_by_process_id(mongo_db, "arvalid_ok", "good")
        assert proc is not None
    finally:
        await fw.shutdown()


async def test_shutdown_stamps_eligible_top_level_process(mongo_db):
    """A root process of an auto_resume task that saves state and cancels
    gracefully ends 'cancelled' + hasSavedState + autoResumeScheduled."""
    prefix = "arstamp"
    started = asyncio.Event()

    async def cooperative(ctx):
        await ctx.mark_has_saved_state()
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=cooperative, process_id="ana", name="Ana",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("ana", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "ana")
    assert proc["status"]["state"] == "cancelled", proc["status"]
    assert proc["hasSavedState"] is True
    assert proc["autoResumeScheduled"] is True


async def test_shutdown_does_not_stamp_non_auto_resume(mongo_db):
    prefix = "arstamp_neg"
    started = asyncio.Event()

    async def cooperative(ctx):
        await ctx.mark_has_saved_state()
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=cooperative, process_id="plain", name="Plain",
            supports_resume=True, auto_resume=False,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("plain", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "plain")
    assert proc["status"]["state"] == "cancelled"
    assert proc.get("autoResumeScheduled") is False


async def test_stamp_eligibility_is_top_level_only(mongo_db):
    """_stamp_auto_resume_if_eligible stamps depth-0 but not depth-1 docs."""
    prefix = "arstamp_depth"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return []

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        task = TaskInstance(
            execute=_noop, process_id="dep", name="Dep",
            supports_resume=True, auto_resume=True,
        )
        fw._executor._task_registry["dep"] = task

        root = await coll.insert_one({
            "processId": "dep", "depth": 0, "status": {"state": "cancelled"},
            "autoResumeScheduled": False, "log": [],
        })
        child = await coll.insert_one({
            "processId": "dep", "depth": 1, "status": {"state": "cancelled"},
            "autoResumeScheduled": False, "log": [],
        })

        await fw._stamp_auto_resume_if_eligible(root.inserted_id)
        await fw._stamp_auto_resume_if_eligible(child.inserted_id)

        root_doc = await coll.find_one({"_id": root.inserted_id})
        child_doc = await coll.find_one({"_id": child.inserted_id})
        assert root_doc["autoResumeScheduled"] is True
        assert child_doc["autoResumeScheduled"] is False
    finally:
        await fw.shutdown()


async def test_force_cancel_clears_stamp(mongo_db):
    """An uncooperative auto_resume root is stamped at shutdown, then
    force-cancelled to failed — the stamp must be cleared."""
    prefix = "arclear_force"
    started = asyncio.Event()

    async def uncooperative(ctx):
        started.set()
        await asyncio.sleep(30)  # ignore cancellation

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=uncooperative, process_id="stuck", name="Stuck",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("stuck", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=60.0)

    await fw.shutdown(grace_seconds=0.2)

    proc = await get_process_by_process_id(mongo_db, prefix, "stuck")
    assert proc["status"]["state"] == "failed"
    assert proc.get("autoResumeScheduled") is False


async def test_reconcile_clears_stamp(mongo_db):
    """A stamped, still-running process from a previous session is reconciled
    to failed on init — the stamp must be cleared."""
    prefix = "arclear_recon"
    coll = mongo_db[f"{prefix}_processes"]
    await coll.insert_one({
        "processId": "ghost", "name": "Ghost", "params": {}, "metadata": {},
        "parentId": None, "rootId": None, "depth": 0, "order": 0,
        "adhoc": False, "ephemeral": False,
        "status": {"state": "running", "runningSince": datetime.now(timezone.utc)},
        "progress": {"percent": None, "message": None}, "log": [],
        "createdAt": datetime.now(timezone.utc),
        "autoResumeScheduled": True,
    })

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="ghost", name="Ghost",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        proc = await get_process_by_process_id(mongo_db, prefix, "ghost")
        assert proc["status"]["state"] == "failed"
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()


async def test_launch_clears_stamp(mongo_db):
    """Launching a stamped process clears the stamp (human beat the timer)."""
    prefix = "arlaunch_clear"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        # Put the synced doc into a stamped, resumable, cancelled state.
        await coll.update_one(
            {"processId": "r"},
            {"$set": {
                "status": {"state": "cancelled"},
                "hasSavedState": True,
                "autoResumeScheduled": True,
            }},
        )
        outcome = await fw.launch("r", resume=True, session_id=None)
        assert outcome.ok, outcome.reason

        proc = await get_process_by_process_id(mongo_db, prefix, "r")
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()


async def test_sweep_resumes_eligible_and_clears_stamp(mongo_db):
    """_auto_resume_scheduled_processes launches cancelled+saved+stamped roots."""
    prefix = "arsweep"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        await coll.update_one(
            {"processId": "r"},
            {"$set": {
                "status": {"state": "cancelled"},
                "hasSavedState": True,
                "autoResumeScheduled": True,
            }},
        )
        await fw._auto_resume_scheduled_processes()
        # Poll until the fire-and-forget relaunch has advanced the process out
        # of 'cancelled', instead of sleeping a fixed margin that a starved CPU
        # can outrun.
        deadline = time.monotonic() + 60.0
        while True:
            proc = await get_process_by_process_id(mongo_db, prefix, "r")
            if proc["status"]["state"] != "cancelled":
                break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"process was not resumed (state={proc['status']['state']})"
                )
            await asyncio.sleep(0.02)
        assert proc["status"]["state"] != "cancelled"  # got (re)launched
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()


async def test_sweep_ignores_failed_and_unsaved(mongo_db):
    prefix = "arsweep_neg"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(execute=_noop, process_id="f", name="F",
                         supports_resume=True, auto_resume=True),
            TaskInstance(execute=_noop, process_id="u", name="U",
                         supports_resume=True, auto_resume=True),
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        # 'f' is stamped but failed (force-killed) — must not resume.
        await coll.update_one({"processId": "f"}, {"$set": {
            "status": {"state": "failed"}, "hasSavedState": False,
            "autoResumeScheduled": True}})
        # 'u' is stamped + cancelled but has no saved state — must not resume.
        await coll.update_one({"processId": "u"}, {"$set": {
            "status": {"state": "cancelled"}, "hasSavedState": False,
            "autoResumeScheduled": True}})

        await fw._auto_resume_scheduled_processes()
        await asyncio.sleep(0.1)

        f = await get_process_by_process_id(mongo_db, prefix, "f")
        u = await get_process_by_process_id(mongo_db, prefix, "u")
        assert f["status"]["state"] == "failed"
        assert u["status"]["state"] == "cancelled"
    finally:
        await fw.shutdown()


async def test_sweep_skips_blocked_and_clears_stamp(mongo_db):
    """A blocked launch is logged, skipped, and un-stamped (no retry)."""
    prefix = "arsweep_block"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="b", name="B",
            metadata={"banned": "yes"},
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        await coll.update_one({"processId": "b"}, {"$set": {
            "status": {"state": "cancelled"}, "hasSavedState": True,
            "autoResumeScheduled": True}})
        # Register a persistent-style in-memory block matching the task metadata.
        async with fw.block_launches({"banned": "yes"}):
            await fw._auto_resume_scheduled_processes()

        proc = await get_process_by_process_id(mongo_db, prefix, "b")
        assert proc["status"]["state"] == "cancelled"  # not launched
        assert proc.get("autoResumeScheduled") is False  # un-stamped
    finally:
        await fw.shutdown()


async def test_timer_fires_after_delay_via_run(mongo_db):
    """End-to-end: run() arms the one-shot timer; after the (tiny) delay the
    eligible process is resumed."""
    prefix = "artimer"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks,
        auto_resume_delay_seconds=0.2,
    )
    # Seed eligible state AFTER init (init's reconcile leaves 'cancelled' alone).
    await coll.update_one({"processId": "r"}, {"$set": {
        "status": {"state": "cancelled"}, "hasSavedState": True,
        "autoResumeScheduled": True}})

    run_task = asyncio.create_task(fw.run())
    try:
        # The one-shot timer fires after auto_resume_delay_seconds (0.2s) and
        # resumes the process via launch(), which also clears the stamp before
        # the state leaves 'cancelled'. Poll for the resume to land rather than
        # guessing a fixed delay+advance margin.
        deadline = time.monotonic() + 60.0
        while True:
            proc = await get_process_by_process_id(mongo_db, prefix, "r")
            if proc["status"]["state"] != "cancelled":
                break
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "process was not auto-resumed within the deadline "
                    f"(state={proc['status']['state']})"
                )
            await asyncio.sleep(0.02)
        assert proc["status"]["state"] != "cancelled"
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()
        await asyncio.wait_for(run_task, timeout=60.0)


async def test_timer_does_not_fire_if_shutdown_first(mongo_db):
    """Shutdown before the delay elapses cancels the one-shot timer; the
    stamped process is NOT resumed and the stamp persists for next boot."""
    prefix = "artimer_cancel"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks,
        auto_resume_delay_seconds=10.0,
    )
    await coll.update_one({"processId": "r"}, {"$set": {
        "status": {"state": "cancelled"}, "hasSavedState": True,
        "autoResumeScheduled": True}})

    run_task = asyncio.create_task(fw.run())
    # Wait until run() has actually armed the one-shot timer (created the task),
    # so shutdown provably cancels a pending timer — vs. guessing with a sleep.
    deadline = time.monotonic() + 60.0
    while fw._auto_resume_task is None:
        if time.monotonic() >= deadline:
            raise AssertionError("run() did not arm the auto-resume timer")
        await asyncio.sleep(0.005)
    await fw.shutdown()
    await asyncio.wait_for(run_task, timeout=60.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "r")
    assert proc["status"]["state"] == "cancelled"  # not resumed
    assert proc.get("autoResumeScheduled") is True  # stamp survives


async def _noop_hook(ctx):  # noqa: ARG001
    pass


async def _sweep_unsaved_setup(mongo_db, prefix, *, hook, supports_resurrect_doc=None):
    """A stamped, cancelled, saved process whose host still holds unsaved
    work (a teardown capture raised during shutdown). Spies on launch and
    resurrect; returns (fw, coll, launched, resurrected)."""
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True, resurrect=hook,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    fields = {
        "status": {"state": "cancelled"},
        "hasSavedState": True,
        "hasUnsavedWork": True,
        "autoResumeScheduled": True,
    }
    if supports_resurrect_doc is not None:
        fields["supportsResurrect"] = supports_resurrect_doc
    await coll.update_one({"processId": "r"}, {"$set": fields})
    launched, resurrected = [], []

    async def _launch_spy(process_id, resume=False, *, session_id):
        launched.append((process_id, resume, session_id))
        raise AssertionError("auto-resume must not launch a process with unsaved work")

    async def _resurrect_spy(process_id, *, session_id):
        from optio_core import ResurrectOutcome
        resurrected.append((process_id, session_id))
        return ResurrectOutcome(ok=True, proc=None)

    fw.launch = _launch_spy
    fw.resurrect = _resurrect_spy
    return fw, coll, launched, resurrected


async def test_sweep_resurrects_unsaved_work_instead_of_resuming(mongo_db):
    """A resume would wipe the kept workdir: the sweep calls resurrect (save,
    then resume) and clears the stamp."""
    prefix = "arsweep_unsaved"
    fw, coll, launched, resurrected = await _sweep_unsaved_setup(
        mongo_db, prefix, hook=_noop_hook,
    )
    try:
        doc = await coll.find_one({"processId": "r"})
        assert doc["supportsResurrect"] is True  # written by task sync
        await fw._auto_resume_scheduled_processes()
        assert launched == []
        assert resurrected == [(str(doc["_id"]), None)]
        doc = await coll.find_one({"processId": "r"})
        assert doc["autoResumeScheduled"] is False
        assert doc["hasUnsavedWork"] is True  # the resurrect clears it, not the sweep
    finally:
        await fw.shutdown()


@pytest.mark.parametrize("supports_resurrect_doc", [None, True])
async def test_sweep_skips_unsaved_work_without_resurrect_hook(
    mongo_db, caplog, supports_resurrect_doc,
):
    """No resurrect hook registered (whatever the document says): neither
    launch nor resurrect; the stamp is cleared and the skip logged."""
    import logging
    prefix = f"arsweep_unsaved_nohook_{supports_resurrect_doc}"
    fw, coll, launched, resurrected = await _sweep_unsaved_setup(
        mongo_db, prefix, hook=None, supports_resurrect_doc=supports_resurrect_doc,
    )
    try:
        with caplog.at_level(logging.WARNING, logger="optio_core_core"):
            await fw._auto_resume_scheduled_processes()
        assert launched == []
        assert resurrected == []
        doc = await coll.find_one({"processId": "r"})
        assert doc["autoResumeScheduled"] is False
        assert doc["status"]["state"] == "cancelled"
        assert doc["hasUnsavedWork"] is True
        assert any(
            "auto-resume skipped" in r.getMessage().lower()
            and "unsaved work on the host (use Resurrect)" in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]
    finally:
        await fw.shutdown()


async def test_sweep_resurrect_refused_still_clears_stamp(mongo_db, caplog):
    """A refused resurrect (e.g. launch-blocked) is logged and un-stamped
    like a refused launch; no launch is attempted."""
    import logging
    from optio_core import ResurrectOutcome
    prefix = "arsweep_unsaved_refused"
    fw, coll, launched, resurrected = await _sweep_unsaved_setup(
        mongo_db, prefix, hook=_noop_hook,
    )

    async def _refuse(process_id, *, session_id):
        resurrected.append((process_id, session_id))
        return ResurrectOutcome(ok=False, reason="launch-blocked")
    fw.resurrect = _refuse
    try:
        with caplog.at_level(logging.WARNING, logger="optio_core_core"):
            await fw._auto_resume_scheduled_processes()
        assert launched == []
        assert len(resurrected) == 1
        doc = await coll.find_one({"processId": "r"})
        assert doc["autoResumeScheduled"] is False
        assert any("launch-blocked" in r.getMessage() for r in caplog.records)
    finally:
        await fw.shutdown()


async def test_sweep_resurrect_end_to_end_saves_then_resumes(mongo_db):
    """Real resurrect path: the hook runs (saving the work) before the resume
    launch, and the flag is cleared; nothing launches the process first."""
    prefix = "arsweep_unsaved_e2e"
    coll = mongo_db[f"{prefix}_processes"]
    order = []

    async def hook(ctx):
        order.append("hook")

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True, resurrect=hook,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        await coll.update_one({"processId": "r"}, {"$set": {
            "status": {"state": "cancelled"}, "hasSavedState": True,
            "hasUnsavedWork": True, "autoResumeScheduled": True,
        }})

        async def _spy(oid, *, resume, session_id):
            order.append(("launch", resume))
        fw._executor.launch_process = _spy
        await fw._auto_resume_scheduled_processes()
        deadline = time.monotonic() + 60.0
        while len(order) < 2:
            if time.monotonic() >= deadline:
                raise AssertionError(f"resurrect did not resume: {order}")
            await asyncio.sleep(0.02)
        assert order == ["hook", ("launch", True)]
        doc = await coll.find_one({"processId": "r"})
        assert doc["hasUnsavedWork"] is False
        assert doc["autoResumeScheduled"] is False
    finally:
        await fw.shutdown()
