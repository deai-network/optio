"""Tests for the per-task grok session snapshot collection (Stage 2).

Single-blob layout: grok stores its session history under
``<GROK_HOME>/sessions`` which lives inside the preserved workdir tar, so a
snapshot references only ``workdirBlobId`` (no separate session blob).
"""

import asyncio

import pytest
from bson import ObjectId

from optio_grok.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


pytestmark = pytest.mark.asyncio


async def test_collection_suffix_is_grok_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_grok_session_snapshots"


async def test_insert_and_load_latest_returns_newest(mongo_db):
    pid = "proc_a"
    first = ObjectId()
    newest = ObjectId()
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done", workdir_blob_id=first,
    )
    await asyncio.sleep(0.005)
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="cancelled", workdir_blob_id=newest,
    )

    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest is not None
    assert latest["endState"] == "cancelled"
    assert latest["workdirBlobId"] == newest
    # Single-blob schema: no separate session blob field.
    assert "sessionBlobId" not in latest


async def test_insert_records_session_id_and_load_returns_it(mongo_db):
    """The ACP sessionId captured at snapshot time round-trips through load — the
    seam ``session/load`` reads on resume to replay the prior conversation."""
    pid = "proc_sid"
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done",
        workdir_blob_id=ObjectId(), session_id="fake-grok-session",
    )
    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest["sessionId"] == "fake-grok-session"


async def test_insert_allows_none_session_id(mongo_db):
    """sessionId stays optional: an iframe-mode (non-ACP) capture has no session
    id, and resume then degrades to a plain workdir + ``--continue`` restore."""
    pid = "proc_no_sid"
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done",
        workdir_blob_id=ObjectId(), session_id=None,
    )
    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest["sessionId"] is None


async def test_load_latest_none_when_empty(mongo_db):
    assert await load_latest_snapshot(mongo_db, "opt", "nope") is None


async def test_prune_keeps_five_and_returns_two_stale_ids(mongo_db):
    pid = "proc_b"
    blob_ids: list[ObjectId] = []
    for _ in range(7):
        wid = ObjectId()
        blob_ids.append(wid)
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done", workdir_blob_id=wid,
        )
        await asyncio.sleep(0.005)

    stale = await prune_snapshots(mongo_db, "opt", pid)
    # The two oldest blob ids are returned for caller-side deletion.
    assert set(stale) == set(blob_ids[:2])

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.count_documents({"processId": pid}) == SNAPSHOT_RETENTION == 5


async def test_prune_noop_within_retention(mongo_db):
    pid = "proc_c"
    for _ in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            workdir_blob_id=ObjectId(),
        )
        await asyncio.sleep(0.005)
    assert await prune_snapshots(mongo_db, "opt", pid) == []


# --- Snapshot history: the list_snapshots / discard_snapshots_after hooks ---

TS = "2026-11-19T13:05:12.345Z"


def _task(supports_resume=True):
    from optio_grok import GrokTaskConfig, create_grok_task
    return create_grok_task(
        process_id="p", name="P",
        config=GrokTaskConfig(
            consumer_instructions="x", mode="iframe", delivery_type="audit",
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
    TS, each with a real workdir blob. Returns (older, newer) as
    (document id, [workdir blob id])."""
    from datetime import datetime, timezone
    out = []
    for captured_at in (
        datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 19, 13, 5, 12, 345000, tzinfo=timezone.utc),
    ):
        blobs = [await _blob(db, "workdir")]
        doc = await insert_snapshot(
            db, "t", process_id="p", end_state="done", workdir_blob_id=blobs[0],
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
    from optio_grok.snapshots import SNAPSHOT_LAYOUT
    assert SNAPSHOT_LAYOUT == SnapshotLayout(
        "_grok_session_snapshots",
        required_blob_fields=("workdirBlobId",),
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
