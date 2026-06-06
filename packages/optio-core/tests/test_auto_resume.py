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
    await asyncio.wait_for(started.wait(), timeout=2.0)

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
    await asyncio.wait_for(started.wait(), timeout=2.0)

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
    await asyncio.wait_for(started.wait(), timeout=2.0)

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
        await asyncio.sleep(0.1)  # let the fire-and-forget executor advance

        proc = await get_process_by_process_id(mongo_db, prefix, "r")
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
        deadline = time.monotonic() + 5.0
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
        await asyncio.wait_for(run_task, timeout=5.0)


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
    deadline = time.monotonic() + 5.0
    while fw._auto_resume_task is None:
        if time.monotonic() >= deadline:
            raise AssertionError("run() did not arm the auto-resume timer")
        await asyncio.sleep(0.005)
    await fw.shutdown()
    await asyncio.wait_for(run_task, timeout=5.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "r")
    assert proc["status"]["state"] == "cancelled"  # not resumed
    assert proc.get("autoResumeScheduled") is True  # stamp survives
