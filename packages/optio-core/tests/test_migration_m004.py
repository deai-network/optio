"""Tests for m004_create_expire_at_ttl_index."""

from optio_core.migrations.m004_create_expire_at_ttl_index import (
    create_expire_at_ttl_index,
)


async def test_creates_ttl_index_on_processes_collections(mongo_db):
    await mongo_db["app1_processes"].insert_one({"processId": "x"})
    await mongo_db["app2_processes"].insert_one({"processId": "y"})
    await mongo_db["unrelated"].insert_one({"foo": "bar"})

    await create_expire_at_ttl_index(mongo_db)

    for coll_name in ("app1_processes", "app2_processes"):
        idx = await mongo_db[coll_name].index_information()
        assert "expireAt_ttl" in idx, f"missing TTL index on {coll_name}: {idx!r}"
        info = idx["expireAt_ttl"]
        assert info["expireAfterSeconds"] == 0
        assert info["key"] == [("expireAt", 1)]

    # No unrelated collection should be touched.
    assert "expireAt_ttl" not in await mongo_db["unrelated"].index_information()


async def test_idempotent_on_repeated_invocation(mongo_db):
    await mongo_db["myapp_processes"].insert_one({"processId": "z"})
    await create_expire_at_ttl_index(mongo_db)
    await create_expire_at_ttl_index(mongo_db)  # must not raise
    idx = await mongo_db["myapp_processes"].index_information()
    assert "expireAt_ttl" in idx
