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
