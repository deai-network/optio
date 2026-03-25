"""Tests for MongoDB store operations."""

from feldwebel.models import TaskInstance, ProcessStatus, Progress
from feldwebel.store import (
    upsert_process, remove_stale_processes,
    get_process_by_process_id, update_status, update_progress,
    create_child_process, clear_result_fields, get_children,
)


async def dummy_execute(ctx):
    pass


async def test_upsert_creates_new_process(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="test_1", name="Test Process", params={"key": "value"})
    result = await upsert_process(mongo_db, "test", task)
    assert result["processId"] == "test_1"
    assert result["name"] == "Test Process"
    assert result["status"]["state"] == "idle"
    assert result["rootId"] == result["_id"]


async def test_upsert_preserves_runtime_state(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="test_2", name="V1")
    await upsert_process(mongo_db, "test", task)

    proc = await get_process_by_process_id(mongo_db, "test", "test_2")
    await update_status(mongo_db, "test", proc["_id"], ProcessStatus(state="running"))

    task.name = "V2"
    result = await upsert_process(mongo_db, "test", task)
    assert result["name"] == "V2"
    assert result["status"]["state"] == "running"


async def test_remove_stale_processes(mongo_db):
    t1 = TaskInstance(execute=dummy_execute, process_id="keep", name="Keep")
    t2 = TaskInstance(execute=dummy_execute, process_id="remove", name="Remove")
    await upsert_process(mongo_db, "test", t1)
    await upsert_process(mongo_db, "test", t2)

    count = await remove_stale_processes(mongo_db, "test", {"keep"})
    assert count == 1
    assert await get_process_by_process_id(mongo_db, "test", "keep") is not None
    assert await get_process_by_process_id(mongo_db, "test", "remove") is None


async def test_create_child_process(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="parent", name="Parent")
    parent = await upsert_process(mongo_db, "test", task)

    child = await create_child_process(
        mongo_db, "test",
        parent_oid=parent["_id"], root_oid=parent["_id"],
        process_id="child_1", name="Child 1", params={"x": 1},
        depth=1, order=0,
    )
    assert child["parentId"] == parent["_id"]
    assert child["rootId"] == parent["_id"]
    assert child["depth"] == 1


async def test_update_progress(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="prog", name="Progress Test")
    proc = await upsert_process(mongo_db, "test", task)

    await update_progress(mongo_db, "test", proc["_id"], Progress(percent=50, message="Half"))
    updated = await get_process_by_process_id(mongo_db, "test", "prog")
    assert updated["progress"]["percent"] == 50
    assert updated["progress"]["message"] == "Half"


async def test_clear_result_fields(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="clear", name="Clear Test")
    proc = await upsert_process(mongo_db, "test", task)

    await update_status(mongo_db, "test", proc["_id"], ProcessStatus(state="failed", error="boom"))
    await clear_result_fields(mongo_db, "test", proc["_id"])
    updated = await get_process_by_process_id(mongo_db, "test", "clear")
    assert updated["status"]["error"] is None
    assert updated["progress"]["percent"] == 0


async def test_get_children(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="p", name="Parent")
    parent = await upsert_process(mongo_db, "test", task)

    await create_child_process(mongo_db, "test", parent["_id"], parent["_id"], "c1", "C1", {}, 1, 0)
    await create_child_process(mongo_db, "test", parent["_id"], parent["_id"], "c2", "C2", {}, 1, 1)

    children = await get_children(mongo_db, "test", parent["_id"])
    assert len(children) == 2
    assert children[0]["processId"] == "c1"
    assert children[1]["processId"] == "c2"
