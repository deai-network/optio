"""controlUpstream store roundtrip (the agent-input listener registration)."""
from optio_core.models import TaskInstance
from optio_core.store import (
    upsert_process, _collection,
    update_control_upstream, clear_control_upstream,
)


async def dummy_execute(ctx):
    pass


async def test_control_upstream_set_and_clear(mongo_db):
    task = TaskInstance(execute=dummy_execute, process_id="ctl_1", name="Ctl")
    proc = await upsert_process(mongo_db, "test", task)

    await update_control_upstream(mongo_db, "test", proc["_id"], "http://engine:54321")
    doc = await _collection(mongo_db, "test").find_one({"_id": proc["_id"]})
    assert doc["controlUpstream"] == {"url": "http://engine:54321", "innerAuth": None}

    await clear_control_upstream(mongo_db, "test", proc["_id"])
    doc = await _collection(mongo_db, "test").find_one({"_id": proc["_id"]})
    assert doc["controlUpstream"] is None
