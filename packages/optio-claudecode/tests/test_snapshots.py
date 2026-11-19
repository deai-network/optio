"""Tests for the per-task claudecode session snapshot collection."""

import asyncio
import os
import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_claudecode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_snapshots_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def test_collection_suffix_is_claudecode_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_claudecode_session_snapshots"


async def test_insert_and_load_latest(mongo_db):
    pid = "proc_a"
    for i in range(3):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)

    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id=pid)
    assert latest is not None
    assert latest["endState"] == "done"
    assert "sessionId" not in latest


async def test_load_latest_none_when_empty(mongo_db):
    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id="nope")
    assert latest is None


async def test_prune_keeps_last_five_and_returns_deleted_ids(mongo_db):
    pid = "proc_b"
    blob_ids_by_cap: list[dict] = []
    for i in range(6):
        snap = await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done",
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
            end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        await asyncio.sleep(0.001)
    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert pruned == []


# --- Snapshot history: the list_snapshots / discard_snapshots_after hooks ---

# The two snapshots' capturedAt in the wire form; the newer one is the plan's TS,
# so a non-zero millisecond runs through the wrapper.
OLDER = "2026-11-19T10:00:00.000Z"
TS = "2026-11-19T13:05:12.345Z"


def _config(supports_resume=True):
    from optio_claudecode import ClaudeCodeTaskConfig
    return ClaudeCodeTaskConfig(
        consumer_instructions="(snapshot history test)",
        fs_isolation=False,
        supports_resume=supports_resume,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )


def _task(supports_resume=True):
    from optio_claudecode.session import create_claudecode_task
    return create_claudecode_task(
        process_id="p", name="P", config=_config(supports_resume),
    )


async def _blob(db, name):
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    return await AsyncIOMotorGridFSBucket(db).upload_from_stream(name, b"payload")


async def _blob_exists(db, blob_id):
    return await db["fs.files"].find_one({"_id": blob_id}) is not None


async def _two_snapshots(db):
    """Two snapshots of process "p" under prefix "t", captured at OLDER and
    TS, each with real session, workdir and conversation blobs. Returns
    (older, newer) as (document id, [session, workdir, conversation blob ids])."""
    from datetime import datetime
    out = []
    for captured_at in (OLDER, TS):
        blobs = [
            await _blob(db, "session"),
            await _blob(db, "workdir"),
            await _blob(db, "conversation"),
        ]
        doc = await insert_snapshot(
            db, prefix="t", process_id="p", end_state="done",
            session_blob_id=blobs[0], workdir_blob_id=blobs[1],
            conversation_blob_id=blobs[2], deliverables_emitted=[],
        )
        await db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"capturedAt": datetime.fromisoformat(captured_at)}},
        )
        out.append((doc["_id"], blobs))
    return out[0], out[1]


def _ctx(db):
    from types import SimpleNamespace
    return SimpleNamespace(process_id="p", _db=db, _prefix="t")


def test_snapshot_layout():
    from optio_agents.snapshots import SnapshotLayout
    from optio_claudecode.snapshots import SNAPSHOT_LAYOUT
    assert SNAPSHOT_LAYOUT == SnapshotLayout(
        "_claudecode_session_snapshots",
        required_blob_fields=("sessionBlobId", "workdirBlobId"),
        optional_blob_fields=("conversationBlobId", "migratedFromWorkdirBlobId"),
    )
    assert SNAPSHOT_LAYOUT.suffix == SESSION_SNAPSHOT_COLLECTION_SUFFIX


def test_task_wires_snapshot_hooks():
    resumable = _task(supports_resume=True)
    assert resumable.list_snapshots is not None
    assert resumable.discard_snapshots_after is not None
    plain = _task(supports_resume=False)
    assert plain.list_snapshots is None
    assert plain.discard_snapshots_after is None


def test_requesty_task_wires_snapshot_hooks():
    from optio_claudecode import (
        ClaudeCodeRequestyTaskConfig,
        create_claudecode_requesty_task,
    )
    task = create_claudecode_requesty_task(
        process_id="p", name="P",
        config=ClaudeCodeRequestyTaskConfig(
            consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk",
        ),
    )
    assert task.list_snapshots is not None
    assert task.discard_snapshots_after is not None


async def test_hooks_list_and_discard_claudecode_snapshots(mongo_db):
    (older_id, older_blobs), (newer_id, newer_blobs) = await _two_snapshots(mongo_db)
    task = _task()
    ctx = _ctx(mongo_db)

    assert await task.list_snapshots(ctx) == [
        {"capturedAt": TS, "endState": "done"},
        {"capturedAt": OLDER, "endState": "done"},
    ]
    assert await task.discard_snapshots_after(ctx, OLDER) == 1

    coll = mongo_db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.find_one({"_id": newer_id}) is None
    assert await coll.find_one({"_id": older_id}) is not None
    for blob_id in newer_blobs:
        assert not await _blob_exists(mongo_db, blob_id)
    for blob_id in older_blobs:
        assert await _blob_exists(mongo_db, blob_id)
    assert await task.list_snapshots(ctx) == [
        {"capturedAt": OLDER, "endState": "done"},
    ]


async def test_capture_pending_refuses_and_deletes_nothing(mongo_db):
    from optio_core import CapturePending
    from optio_claudecode.pending_captures import (
        load_pending_capture,
        record_pending_capture,
    )
    (older_id, older_blobs), (newer_id, newer_blobs) = await _two_snapshots(mongo_db)
    await record_pending_capture(
        mongo_db, "t", process_id="p",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
    )
    task = _task()
    ctx = _ctx(mongo_db)

    with pytest.raises(CapturePending):
        await task.discard_snapshots_after(ctx, OLDER)

    coll = mongo_db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.find_one({"_id": older_id}) is not None
    assert await coll.find_one({"_id": newer_id}) is not None
    for blob_id in (*older_blobs, *newer_blobs):
        assert await _blob_exists(mongo_db, blob_id)
    # The refusal leaves the record for Resurrect or a plain Resume to settle,
    # and listing ignores it.
    assert await load_pending_capture(mongo_db, "t", "p") is not None
    assert await task.list_snapshots(ctx) == [
        {"capturedAt": TS, "endState": "done"},
        {"capturedAt": OLDER, "endState": "done"},
    ]
