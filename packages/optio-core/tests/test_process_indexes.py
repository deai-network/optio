"""The process collection carries the indexes optio's queries need.

Spec: docs/2026-10-09-process-indexes-design.md
"""

import logging

from bson import ObjectId

from optio_core.store import ensure_process_indexes

EXPECTED = {
    "_id_": ([("_id", 1)], None),
    "processId_1__id_-1": ([("processId", 1), ("_id", -1)], None),
    "parentId_1_order_1": ([("parentId", 1), ("order", 1)], None),
    "rootId_1_depth_1_order_1": ([("rootId", 1), ("depth", 1), ("order", 1)], None),
    "status.state_1": ([("status.state", 1)], None),
    "originatingSessionId_1": ([("originatingSessionId", 1)], None),
    "expireAt_ttl": ([("expireAt", 1)], 0),
}


async def _indexes(coll):
    """name -> (keys as (field, direction) pairs, expireAfterSeconds or None)."""
    out = {}
    async for ix in coll.list_indexes():
        keys = [(k, int(v)) for k, v in ix["key"].items()]
        out[ix["name"]] = (keys, ix.get("expireAfterSeconds"))
    return out


def _ixscans(plan):
    """The index names of every IXSCAN stage in an explain plan."""
    found = []
    if isinstance(plan, dict):
        if plan.get("stage") == "IXSCAN":
            found.append(plan.get("indexName"))
        for v in plan.values():
            found += _ixscans(v)
    elif isinstance(plan, list):
        for v in plan:
            found += _ixscans(v)
    return found


async def test_ensure_creates_the_six_indexes_on_a_new_prefix(mongo_db):
    await ensure_process_indexes(mongo_db, "idx")

    assert await _indexes(mongo_db["idx_processes"]) == EXPECTED


async def test_ensure_twice_changes_nothing(mongo_db):
    await ensure_process_indexes(mongo_db, "idx2")
    await ensure_process_indexes(mongo_db, "idx2")

    assert await _indexes(mongo_db["idx2_processes"]) == EXPECTED


async def test_an_index_with_the_same_keys_under_another_name_is_a_warning(mongo_db, caplog):
    coll = mongo_db["idx3_processes"]
    await coll.create_index([("processId", 1), ("_id", -1)], name="my_pid")
    caplog.set_level(logging.WARNING, logger="optio_core.store")

    await ensure_process_indexes(mongo_db, "idx3")

    warnings = [r for r in caplog.records
                if r.name == "optio_core.store" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "processId_1__id_-1" in warnings[0].getMessage()
    expected = {k: v for k, v in EXPECTED.items() if k != "processId_1__id_-1"}
    expected["my_pid"] = ([("processId", 1), ("_id", -1)], None)
    assert await _indexes(coll) == expected


async def test_lookups_use_the_indexes(mongo_db):
    await ensure_process_indexes(mongo_db, "idx4")
    coll = mongo_db["idx4_processes"]
    parent = ObjectId()
    await coll.insert_many([
        {"processId": f"p{i}", "parentId": parent, "order": i} for i in range(3)
    ])

    by_pid = await coll.find({"processId": "p1"}).sort("_id", -1).limit(1).explain()
    children = await coll.find({"parentId": parent}).sort("order", 1).explain()

    assert "processId_1__id_-1" in _ixscans(by_pid["queryPlanner"]["winningPlan"])
    assert "parentId_1_order_1" in _ixscans(children["queryPlanner"]["winningPlan"])


async def test_init_ensures_the_indexes(mongo_db):
    from optio_core.lifecycle import Optio

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="idxinit")
    try:
        assert await _indexes(mongo_db["idxinit_processes"]) == EXPECTED
    finally:
        await optio.shutdown()


async def test_the_ttl_index_m004_created_is_kept_without_a_warning(mongo_db, caplog):
    """The path every existing deployment takes: m004 created expireAt_ttl on
    the collection, then every start ensures the indexes."""
    from optio_core.migrations.m004_create_expire_at_ttl_index import (
        create_expire_at_ttl_index,
    )
    coll = mongo_db["idx5_processes"]
    await coll.insert_one({"processId": "p0"})  # the collection exists when m004 runs
    await create_expire_at_ttl_index(mongo_db)
    caplog.set_level(logging.WARNING, logger="optio_core.store")

    await ensure_process_indexes(mongo_db, "idx5")

    assert [r for r in caplog.records if r.name == "optio_core.store"] == []
    assert await _indexes(coll) == EXPECTED
