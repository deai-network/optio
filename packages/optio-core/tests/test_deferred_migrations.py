"""Tests for optio_core.deferred_migrations.

Spec: docs/2026-10-11-conversation-buffer-encryption-design.md §5.
"""

import pytest

from optio_core.deferred_migrations import (
    DeferredMigrationContext,
    DeferredMigrationRegistry,
)
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def dummy_execute(ctx):
    pass


def test_task_instance_session_crypto_default_none():
    task = TaskInstance(execute=dummy_execute, process_id="t", name="T")
    assert task.session_crypto is None


async def test_runs_with_task_lookup(mongo_db):
    registry = DeferredMigrationRegistry()
    seen: dict[str, object] = {}

    p1_task = TaskInstance(execute=dummy_execute, process_id="p1", name="P1")

    @registry.register("m")
    async def m(ctx: DeferredMigrationContext) -> None:
        seen["ctx"] = ctx
        seen["found"] = ctx.task_for("p1")
        seen["missing"] = ctx.task_for("nope")

    def task_for(pid):
        return {"p1": p1_task}.get(pid)

    await registry.run(mongo_db, "myapp", task_for)

    assert seen["found"] is p1_task
    assert seen["missing"] is None

    docs = await mongo_db["myapp_fw_deferred_migrations"].find({}).to_list(None)
    assert len(docs) == 1
    assert docs[0]["name"] == "m"


async def test_runs_once(mongo_db):
    registry = DeferredMigrationRegistry()
    calls = []

    @registry.register("m")
    async def m(ctx: DeferredMigrationContext) -> None:
        calls.append(1)

    await registry.run(mongo_db, "myapp", lambda pid: None)
    await registry.run(mongo_db, "myapp", lambda pid: None)

    assert len(calls) == 1


async def test_raising_migration_is_retried(mongo_db):
    registry = DeferredMigrationRegistry()
    state = {"should_raise": True}
    calls = []

    @registry.register("m")
    async def m(ctx: DeferredMigrationContext) -> None:
        calls.append(1)
        if state["should_raise"]:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await registry.run(mongo_db, "myapp", lambda pid: None)

    docs = await mongo_db["myapp_fw_deferred_migrations"].find({}).to_list(None)
    assert docs == []
    assert len(calls) == 1

    state["should_raise"] = False
    await registry.run(mongo_db, "myapp", lambda pid: None)

    docs = await mongo_db["myapp_fw_deferred_migrations"].find({}).to_list(None)
    assert len(docs) == 1
    assert docs[0]["name"] == "m"
    assert len(calls) == 2


async def test_init_runs_after_initial_sync(mongo_db):
    from optio_core.deferred_migrations import deferred_migrations as global_registry

    seen: dict[str, object] = {}

    @global_registry.register("test_init_runs_after_initial_sync_migration")
    async def _mig(ctx: DeferredMigrationContext) -> None:
        seen["task"] = ctx.task_for("init_test_task")

    try:
        async def get_tasks(services, metadata_filter=None):
            return [
                TaskInstance(
                    execute=dummy_execute, process_id="init_test_task", name="Init Test Task",
                ),
            ]

        optio = Optio()
        await optio.init(
            mongo_db=mongo_db,
            prefix="test_deferred_init",
            get_task_definitions=get_tasks,
        )
    finally:
        global_registry.unregister("test_init_runs_after_initial_sync_migration")

    assert seen["task"] is not None
    assert seen["task"].process_id == "init_test_task"
