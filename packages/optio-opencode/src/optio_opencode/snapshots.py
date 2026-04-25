"""MongoDB `{prefix}_opencode_session_snapshots` collection helpers.

One document per terminal run per process_id. Layout:

    {
      _id:             ObjectId,
      processId:       str,
      capturedAt:      datetime,
      endState:        str,          # "done" | "failed" | "cancelled"
      sessionId:       str,          # opencode session id (preserved across export→import)
      sessionBlobId:   ObjectId,     # GridFS file id for the session JSON
      workdirBlobId:   ObjectId,     # GridFS file id for the workdir tar.gz
      deliverablesEmitted: list,     # audit metadata only; not replayed
    }

Retention: keep the latest `SNAPSHOT_RETENTION` per processId. Older rows
are deleted by `prune_snapshots` and their GridFS blobs are expected to be
deleted by the caller using the ids returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_opencode_session_snapshots"
SNAPSHOT_RETENTION = 5


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]


async def ensure_indexes(db: AsyncIOMotorDatabase, prefix: str) -> None:
    """Idempotent index creation — called lazily by insert_snapshot."""
    await _collection(db, prefix).create_index(
        [("processId", 1), ("capturedAt", -1)],
        name="by_processId_capturedAt_desc",
    )


async def insert_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    process_id: str,
    end_state: str,
    session_id: str,
    session_blob_id: ObjectId,
    workdir_blob_id: ObjectId,
    deliverables_emitted: list,
) -> dict:
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "sessionId": session_id,
        "sessionBlobId": session_blob_id,
        "workdirBlobId": workdir_blob_id,
        "deliverablesEmitted": deliverables_emitted,
    }
    result = await _collection(db, prefix).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def load_latest_snapshot(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one(
        {"processId": process_id}, sort=[("capturedAt", -1)],
    )


async def prune_snapshots(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> list[dict]:
    """Keep the latest SNAPSHOT_RETENTION; delete the rest.

    Returns a list of `{sessionBlobId, workdirBlobId}` dicts for the
    deleted snapshots so the caller can remove the corresponding GridFS
    blobs.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"sessionBlobId": 1, "workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[SNAPSHOT_RETENTION:]
    if not stale:
        return []
    stale_ids = [d["_id"] for d in stale]
    await coll.delete_many({"_id": {"$in": stale_ids}})
    return [
        {"sessionBlobId": d["sessionBlobId"], "workdirBlobId": d["workdirBlobId"]}
        for d in stale
    ]
