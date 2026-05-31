"""Tests for the `purge_records` flag on group_cancel / group_cancel_and_wait.

Spec: docs/superpowers/specs/2026-05-31-delete-customer-design.md (section 1).

When `purge_records=True`, the group-cancel family deletes every process
record matching the metadata filter (roots + descendants) after the in-scope
pids reach a terminal state. With the default `purge_records=False`, records
are left in place (back-compat).
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance

pytestmark = pytest.mark.asyncio


async def _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=2.0):
    """Helper: init an Optio with the given tasks, return (optio, run_task)."""
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run())
    return optio, run_task


async def _stop_optio(optio, run_task):
    await optio.shutdown()
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass


def _make_cooperative(started: asyncio.Event):
    async def fn(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)
    return fn


async def test_group_cancel_and_wait_purges_records_when_flag_set(mongo_db):
    """purge_records=True deletes every process record matching the filter
    after the in-scope pids reach a terminal state."""
    started = [asyncio.Event() for _ in range(2)]
    tasks = [
        TaskInstance(
            process_id=f"p.d1.{i}", name=f"D1-{i}", params={},
            execute=_make_cooperative(started[i]), metadata={"dataspace": "d1"},
        )
        for i in range(2)
    ]

    optio, run_task = await _start_optio(mongo_db, "gcp_purge", tasks)
    try:
        for i in range(2):
            await optio.launch(f"p.d1.{i}", session_id=None)
        for ev in started:
            await ev.wait()

        await optio.group_cancel_and_wait(
            {"dataspace": "d1"}, block_new_launches=True, purge_records=True,
        )

        count = await mongo_db["gcp_purge_processes"].count_documents(
            {"metadata.dataspace": "d1"}
        )
        assert count == 0
    finally:
        await _stop_optio(optio, run_task)


async def test_group_cancel_and_wait_keeps_records_by_default(mongo_db):
    """Default purge_records=False leaves the cancelled records in place."""
    started = asyncio.Event()
    task = TaskInstance(
        process_id="p.d2", name="D2", params={},
        execute=_make_cooperative(started), metadata={"dataspace": "d2"},
    )

    optio, run_task = await _start_optio(mongo_db, "gcp_keep", [task])
    try:
        await optio.launch("p.d2", session_id=None)
        await started.wait()

        await optio.group_cancel_and_wait(
            {"dataspace": "d2"}, block_new_launches=True,
        )

        count = await mongo_db["gcp_keep_processes"].count_documents(
            {"metadata.dataspace": "d2"}
        )
        assert count >= 1
    finally:
        await _stop_optio(optio, run_task)
