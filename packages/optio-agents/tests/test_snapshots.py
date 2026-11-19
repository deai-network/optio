"""optio_agents.snapshots: the snapshot-history helper behind the list_snapshots and
discard_snapshots_after hooks of every engine wrapper.

Spec: docs/2026-11-19-resume-from-snapshot-design.md
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson.codec_options import CodecOptions
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core import CapturePending, SnapshotNotFound
from optio_agents.snapshots import (
    SnapshotLayout,
    discard_snapshots_after,
    list_snapshots,
    snapshot_hooks,
)


COLL = "t_x_session_snapshots"
REQ = ("sessionBlobId", "workdirBlobId")
OPT = ("conversationBlobId",)
TS = "2026-11-19T13:05:12.345Z"


def _at(hour, minute=0, second=0, microsecond=0):
    return datetime(2026, 11, 19, hour, minute, second, microsecond, tzinfo=UTC)


async def _snap(db, coll, pid, at, *, end="done", fields=REQ, missing=(), extra=None):
    """Insert one snapshot document with one real GridFS blob per field, then delete the
    blobs named in `missing`. `extra` adds fields as given. Returns the document."""
    bucket = AsyncIOMotorGridFSBucket(db)
    doc = {"processId": pid, "capturedAt": at, "endState": end}
    for field in fields:
        doc[field] = await bucket.upload_from_stream(field, field.encode())
    if extra:
        doc.update(extra)
    await db[coll].insert_one(doc)
    for field in missing:
        await bucket.delete(doc[field])
    return doc


async def _blob_exists(db, blob_id):
    return await db["fs.files"].count_documents({"_id": blob_id}) > 0


async def _chunk_count(db, blob_id):
    return await db["fs.chunks"].count_documents({"files_id": blob_id})


async def _remaining_ids(db, coll=COLL, pid="p"):
    docs = await db[coll].find({"processId": pid}, projection={"_id": 1}).to_list(None)
    return sorted(d["_id"] for d in docs)


async def test_list_valid_only_newest_first(mongo_db):
    await _snap(mongo_db, COLL, "p", _at(10), end="done")
    await _snap(mongo_db, COLL, "p", _at(11), end="failed", missing=("workdirBlobId",))
    await _snap(mongo_db, COLL, "p", _at(12), end="cancelled")

    assert await list_snapshots(mongo_db, COLL, "p", REQ) == [
        {"capturedAt": "2026-11-19T12:00:00.000Z", "endState": "cancelled"},
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]


async def test_missing_optional_blob_keeps_a_snapshot_valid(mongo_db):
    await _snap(
        mongo_db, COLL, "p", _at(10),
        fields=("sessionBlobId", "workdirBlobId", "conversationBlobId"),
        missing=("conversationBlobId",),
    )

    assert await list_snapshots(mongo_db, COLL, "p", REQ) == [
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]


async def test_list_ignores_other_processes_and_is_empty_without_snapshots(mongo_db):
    assert await list_snapshots(mongo_db, COLL, "p", REQ) == []

    await _snap(mongo_db, COLL, "q", _at(10))
    await _snap(mongo_db, "t_y_session_snapshots", "p", _at(11))
    assert await list_snapshots(mongo_db, COLL, "p", REQ) == []

    await _snap(mongo_db, COLL, "p", _at(12))
    assert await list_snapshots(mongo_db, COLL, "p", REQ) == [
        {"capturedAt": "2026-11-19T12:00:00.000Z", "endState": "done"},
    ]


async def test_captured_at_is_millisecond_utc(mongo_db):
    await _snap(mongo_db, COLL, "p", datetime(2026, 11, 19, 13, 5, 12, 345678, tzinfo=UTC))

    listed = await list_snapshots(mongo_db, COLL, "p", REQ)

    assert [s["capturedAt"] for s in listed] == [TS]


async def test_discard_deletes_newer_docs_and_their_blobs(mongo_db):
    a = await _snap(mongo_db, COLL, "p", _at(10))
    b = await _snap(mongo_db, COLL, "p", _at(11))
    c = await _snap(
        mongo_db, COLL, "p", _at(12),
        fields=("sessionBlobId", "workdirBlobId", "conversationBlobId"),
    )

    n = await discard_snapshots_after(
        mongo_db, COLL, "p", "2026-11-19T10:00:00.000Z", REQ, OPT,
    )

    assert n == 2
    assert await _remaining_ids(mongo_db) == [a["_id"]]
    gone = [b["sessionBlobId"], b["workdirBlobId"],
            c["sessionBlobId"], c["workdirBlobId"], c["conversationBlobId"]]
    for blob_id in gone:
        assert not await _blob_exists(mongo_db, blob_id)
        assert await _chunk_count(mongo_db, blob_id) == 0
    for field in REQ:
        assert await _blob_exists(mongo_db, a[field])
        assert await _chunk_count(mongo_db, a[field]) > 0


@pytest.mark.parametrize("spelling", [
    TS,
    "2026-11-19T13:05:12.345+00:00",
    "2026-11-19T14:05:12.345+01:00",
])
async def test_discard_matches_any_iso_spelling_of_the_instant(mongo_db, spelling):
    chosen = await _snap(mongo_db, COLL, "p", _at(13, 5, 12, 345000))
    await _snap(mongo_db, COLL, "p", _at(14))

    assert await discard_snapshots_after(mongo_db, COLL, "p", spelling, REQ) == 1
    assert await _remaining_ids(mongo_db) == [chosen["_id"]]


@pytest.mark.parametrize("captured_at", [
    "2026-11-19T13:05:13.345Z",      # another second
    "2026-11-19T13:05:12Z",          # another millisecond
    "2026-11-19T13:05:12.3451Z",     # a non-zero digit below the millisecond
    "2026-11-19T13:05:12.3450001Z",  # one past the sixth fraction digit
    "2026-11-19T13:05:12.345",       # no offset
    "yesterday",                     # not ISO 8601
    "2026-11-19T13:30:00.000Z",      # the instant of an invalid document
])
async def test_discard_refusals_delete_nothing(mongo_db, captured_at):
    await _snap(mongo_db, COLL, "p", _at(13, 5, 12, 345000))
    await _snap(mongo_db, COLL, "p", _at(13, 30), missing=("workdirBlobId",))
    await _snap(mongo_db, COLL, "p", _at(14))
    docs_before = await mongo_db[COLL].count_documents({})
    files_before = await mongo_db["fs.files"].count_documents({})

    with pytest.raises(SnapshotNotFound):
        await discard_snapshots_after(mongo_db, COLL, "p", captured_at, REQ, OPT)

    assert await mongo_db[COLL].count_documents({}) == docs_before
    assert await mongo_db["fs.files"].count_documents({}) == files_before


async def test_discard_keeps_a_blob_a_kept_snapshot_names(mongo_db):
    a = await _snap(mongo_db, COLL, "p", _at(10))
    c = await _snap(
        mongo_db, COLL, "p", _at(12), extra={"conversationBlobId": a["workdirBlobId"]},
    )

    n = await discard_snapshots_after(
        mongo_db, COLL, "p", "2026-11-19T10:00:00.000Z", REQ, OPT,
    )

    assert n == 1
    assert await _remaining_ids(mongo_db) == [a["_id"]]
    assert await _blob_exists(mongo_db, a["workdirBlobId"])
    assert await _blob_exists(mongo_db, a["sessionBlobId"])
    assert not await _blob_exists(mongo_db, c["sessionBlobId"])
    assert not await _blob_exists(mongo_db, c["workdirBlobId"])


async def test_discard_deletes_invalid_newer_documents_too(mongo_db):
    a = await _snap(mongo_db, COLL, "p", _at(10))
    b = await _snap(mongo_db, COLL, "p", _at(11), missing=("workdirBlobId",))

    n = await discard_snapshots_after(mongo_db, COLL, "p", "2026-11-19T10:00:00.000Z", REQ)

    assert n == 1
    assert await _remaining_ids(mongo_db) == [a["_id"]]
    assert not await _blob_exists(mongo_db, b["sessionBlobId"])


async def test_discard_of_the_newest_deletes_nothing(mongo_db):
    a = await _snap(mongo_db, COLL, "p", _at(10))
    b = await _snap(mongo_db, COLL, "p", _at(11))

    n = await discard_snapshots_after(mongo_db, COLL, "p", "2026-11-19T11:00:00.000Z", REQ)

    assert n == 0
    assert await _remaining_ids(mongo_db) == sorted([a["_id"], b["_id"]])
    for doc in (a, b):
        for field in REQ:
            assert await _blob_exists(mongo_db, doc[field])


async def test_tz_aware_client(mongo_db):
    aware = mongo_db.client.get_database(
        mongo_db.name, codec_options=CodecOptions(tz_aware=True),
    )
    kept = {}
    for pid in ("p", "q"):
        kept[pid] = await _snap(mongo_db, COLL, pid, _at(10))
        await _snap(mongo_db, COLL, pid, _at(11), missing=("workdirBlobId",))
        await _snap(mongo_db, COLL, pid, _at(12))

    naive_list = await list_snapshots(mongo_db, COLL, "p", REQ)
    assert await list_snapshots(aware, COLL, "p", REQ) == naive_list
    assert [s["capturedAt"] for s in naive_list] == [
        "2026-11-19T12:00:00.000Z", "2026-11-19T10:00:00.000Z",
    ]

    naive_n = await discard_snapshots_after(
        mongo_db, COLL, "p", "2026-11-19T10:00:00.000Z", REQ,
    )
    aware_n = await discard_snapshots_after(
        aware, COLL, "q", "2026-11-19T10:00:00.000Z", REQ,
    )
    assert naive_n == aware_n == 2
    assert await _remaining_ids(mongo_db, pid="p") == [kept["p"]["_id"]]
    assert await _remaining_ids(mongo_db, pid="q") == [kept["q"]["_id"]]


async def test_hooks_follow_the_layout_and_check_the_pending_capture_first(mongo_db):
    layout = SnapshotLayout("_x_session_snapshots", ("workdirBlobId",))
    assert layout.collection("t") == COLL
    ctx = SimpleNamespace(process_id="p", _db=mongo_db, _prefix="t")
    chosen = await _snap(mongo_db, COLL, "p", _at(13, 5, 12, 345000), fields=("workdirBlobId",))
    await _snap(mongo_db, COLL, "p", _at(14), end="failed", fields=("workdirBlobId",))

    pending = AsyncMock(return_value=True)
    hooks = snapshot_hooks(layout, capture_pending=pending)
    assert hooks._fields == ("list_snapshots", "discard_snapshots_after")

    assert await hooks.list_snapshots(ctx) == [
        {"capturedAt": "2026-11-19T14:00:00.000Z", "endState": "failed"},
        {"capturedAt": TS, "endState": "done"},
    ]

    docs_before = await mongo_db[COLL].count_documents({})
    files_before = await mongo_db["fs.files"].count_documents({})
    with pytest.raises(CapturePending):
        await hooks.discard_snapshots_after(ctx, TS)
    pending.assert_awaited_once_with(ctx)
    assert await mongo_db[COLL].count_documents({}) == docs_before
    assert await mongo_db["fs.files"].count_documents({}) == files_before

    assert await snapshot_hooks(layout).discard_snapshots_after(ctx, TS) == 1
    assert await _remaining_ids(mongo_db) == [chosen["_id"]]
