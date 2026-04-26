"""Tests for the per-task session snapshot collection."""

import asyncio
import os
import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_opencode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_test_snapshots_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def test_insert_and_load_latest(mongo_db):
    pid = "proc_a"
    for i in range(3):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)

    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id=pid)
    assert latest is not None
    assert latest["sessionId"] == "s_2"


async def test_load_latest_none_when_empty(mongo_db):
    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id="nope")
    assert latest is None


async def test_prune_keeps_last_five_and_returns_deleted_ids(mongo_db):
    pid = "proc_b"
    blob_ids_by_cap: list[dict] = []
    for i in range(6):
        snap = await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)
        blob_ids_by_cap.append({
            "session": snap["sessionBlobId"],
            "workdir": snap["workdirBlobId"],
        })

    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert len(pruned) == 1
    assert pruned[0]["sessionBlobId"] == blob_ids_by_cap[0]["session"]
    assert pruned[0]["workdirBlobId"] == blob_ids_by_cap[0]["workdir"]

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    count = await coll.count_documents({"processId": pid})
    assert count == SNAPSHOT_RETENTION


async def test_prune_noop_when_within_retention(mongo_db):
    pid = "proc_c"
    for i in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)
    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert pruned == []
