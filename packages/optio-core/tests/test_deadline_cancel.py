"""Tests for deadline-driven cooperative cancel.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio


pytestmark = pytest.mark.asyncio


async def test_init_accepts_cancel_grace_seconds(mongo_db):
    """Optio.init forwards cancel_grace_seconds onto OptioConfig."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsinit", cancel_grace_seconds=2.5)
    assert optio._config.cancel_grace_seconds == 2.5


async def test_init_default_cancel_grace_seconds(mongo_db):
    """Default cancel_grace_seconds is 5.0."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsdefault")
    assert optio._config.cancel_grace_seconds == 5.0


async def test_executor_tracks_running_task_and_cancel_entry(mongo_db):
    """While a process is running, _running_tasks and _cancellation_flags both
    have an entry; the flag value is a _CancelEntry, not a bare Event."""
    from optio_core.executor import Executor, _CancelEntry
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    prefix = "ctxtrack"
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_until_release(ctx):  # noqa: ARG001
        started.set()
        await release.wait()

    task_inst = TaskInstance(
        process_id="p.hold", name="Hold", params={}, execute=hold_until_release,
    )
    await upsert_process(mongo_db, prefix, task_inst)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks([task_inst])

    runner = asyncio.create_task(executor.launch_process("p.hold"))
    await started.wait()

    # Find the oid via the registry's only entry.
    assert len(executor._running_tasks) == 1
    oid = next(iter(executor._running_tasks))
    assert isinstance(executor._running_tasks[oid], asyncio.Task)
    entry = executor._cancellation_flags[oid]
    assert isinstance(entry, _CancelEntry)
    assert entry.deadline is None
    assert isinstance(entry.flag, asyncio.Event)

    release.set()
    await runner

    # After completion both registries are empty.
    assert executor._running_tasks == {}
    assert executor._cancellation_flags == {}
