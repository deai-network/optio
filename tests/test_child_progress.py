"""Tests for child progress callback mechanism."""

import asyncio
import os
from unittest.mock import patch
from feldwebel.models import TaskInstance
from feldwebel.executor import Executor
from feldwebel.store import upsert_process, get_process_by_process_id


async def test_configurable_flush_interval(mongo_db):
    """DB flush interval reads from FELDWEBEL_PROGRESS_FLUSH_INTERVAL_MS."""
    observed_interval = {}

    async def task_fn(ctx):
        observed_interval["value"] = ctx._flush_interval

    task = TaskInstance(execute=task_fn, process_id="flush_cfg", name="FlushCfg")
    await upsert_process(mongo_db, "test", task)

    with patch.dict(os.environ, {"FELDWEBEL_PROGRESS_FLUSH_INTERVAL_MS": "50"}):
        executor = Executor(mongo_db, "test", {})
        executor.register_tasks([task])
        result = await executor.launch_process("flush_cfg")

    assert result == "done"
    assert observed_interval["value"] == 0.05
