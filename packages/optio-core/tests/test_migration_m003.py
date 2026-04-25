"""Tests for m003_backfill_has_saved_state."""

from optio_core.migrations.m003_backfill_has_saved_state import backfill_has_saved_state


async def test_backfill_sets_false_on_missing_field(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "a", "status": {"state": "idle"}})
    await coll.insert_one({"processId": "b", "status": {"state": "idle"}})

    await backfill_has_saved_state(mongo_db)

    doc_a = await coll.find_one({"processId": "a"})
    doc_b = await coll.find_one({"processId": "b"})
    assert doc_a["hasSavedState"] is False
    assert doc_b["hasSavedState"] is False


async def test_backfill_preserves_existing_values(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "c", "hasSavedState": True, "status": {"state": "idle"}})

    await backfill_has_saved_state(mongo_db)

    doc = await coll.find_one({"processId": "c"})
    assert doc["hasSavedState"] is True


async def test_backfill_covers_multiple_prefix_collections(mongo_db):
    await mongo_db["app1_processes"].insert_one({"processId": "x", "status": {"state": "idle"}})
    await mongo_db["app2_processes"].insert_one({"processId": "y", "status": {"state": "idle"}})
    await mongo_db["unrelated_collection"].insert_one({"foo": "bar"})

    await backfill_has_saved_state(mongo_db)

    x = await mongo_db["app1_processes"].find_one({"processId": "x"})
    y = await mongo_db["app2_processes"].find_one({"processId": "y"})
    unrelated = await mongo_db["unrelated_collection"].find_one({"foo": "bar"})
    assert x["hasSavedState"] is False
    assert y["hasSavedState"] is False
    assert "hasSavedState" not in unrelated


async def test_backfill_idempotent(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "d", "status": {"state": "idle"}})
    await backfill_has_saved_state(mongo_db)
    # Second run should not flip the value.
    await backfill_has_saved_state(mongo_db)
    doc = await coll.find_one({"processId": "d"})
    assert doc["hasSavedState"] is False
