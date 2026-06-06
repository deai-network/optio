"""Tests for auto-resume-on-restart.

Spec: docs/superpowers/specs/2026-06-06-auto-resume-on-restart-design.md
"""
import asyncio
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
