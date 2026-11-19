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


# --- Snapshot history: the list_snapshots / discard_snapshots_after hooks ---

TS = "2026-11-19T13:05:12.345Z"


def _task(supports_resume=True):
    from optio_opencode import OpencodeTaskConfig, create_opencode_task
    return create_opencode_task(
        process_id="p", name="P",
        config=OpencodeTaskConfig(
            consumer_instructions="hi", fs_isolation=False,
            supports_resume=supports_resume,
        ),
    )


async def _blob(db, name):
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    return await AsyncIOMotorGridFSBucket(db).upload_from_stream(name, b"payload")


async def _blob_exists(db, blob_id):
    return await db["fs.files"].find_one({"_id": blob_id}) is not None


async def _two_snapshots(db):
    """Two snapshots of process "p" under prefix "t", captured at 10:00 and at
    TS, each with real session and workdir blobs. Returns (older, newer) as
    (document id, [session, workdir blob ids])."""
    from datetime import datetime, timezone
    out = []
    for captured_at in (
        datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 19, 13, 5, 12, 345000, tzinfo=timezone.utc),
    ):
        blobs = [await _blob(db, "session"), await _blob(db, "workdir")]
        doc = await insert_snapshot(
            db, prefix="t", process_id="p", end_state="done", session_id="s",
            session_blob_id=blobs[0], workdir_blob_id=blobs[1],
            deliverables_emitted=[],
        )
        await db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].update_one(
            {"_id": doc["_id"]}, {"$set": {"capturedAt": captured_at}},
        )
        out.append((doc["_id"], blobs))
    return out[0], out[1]


def _ctx(db):
    from types import SimpleNamespace
    return SimpleNamespace(process_id="p", _db=db, _prefix="t")


async def test_snapshot_layout():
    from optio_agents.snapshots import SnapshotLayout
    from optio_opencode.snapshots import SNAPSHOT_LAYOUT
    assert SNAPSHOT_LAYOUT == SnapshotLayout(
        "_opencode_session_snapshots",
        required_blob_fields=("sessionBlobId", "workdirBlobId"),
    )
    assert SNAPSHOT_LAYOUT.optional_blob_fields == ()
    assert SNAPSHOT_LAYOUT.suffix == SESSION_SNAPSHOT_COLLECTION_SUFFIX


async def test_task_wires_snapshot_hooks():
    resumable = _task(supports_resume=True)
    assert resumable.list_snapshots is not None
    assert resumable.discard_snapshots_after is not None
    plain = _task(supports_resume=False)
    assert plain.list_snapshots is None
    assert plain.discard_snapshots_after is None


async def test_hooks_list_and_discard(mongo_db):
    (older_id, older_blobs), (newer_id, newer_blobs) = await _two_snapshots(mongo_db)
    task = _task()
    ctx = _ctx(mongo_db)

    assert await task.list_snapshots(ctx) == [
        {"capturedAt": TS, "endState": "done"},
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]
    assert await task.discard_snapshots_after(ctx, "2026-11-19T10:00:00.000Z") == 1

    coll = mongo_db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.find_one({"_id": newer_id}) is None
    assert await coll.find_one({"_id": older_id}) is not None
    for blob_id in newer_blobs:
        assert not await _blob_exists(mongo_db, blob_id)
    for blob_id in older_blobs:
        assert await _blob_exists(mongo_db, blob_id)
    assert await task.list_snapshots(ctx) == [
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]
