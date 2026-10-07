"""MongoDB `{prefix}_claudecode_pending_captures`: the blobs a capture is
writing, recorded before it streams the workdir and deleted once the
snapshot record is inserted. A capture that is cut off (force-cancel past
the grace) leaves its record behind, so Resurrect can delete exactly that
partial workdir blob. One document per processId.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

PENDING_CAPTURE_COLLECTION_SUFFIX = "_claudecode_pending_captures"


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{PENDING_CAPTURE_COLLECTION_SUFFIX}"]


async def record_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, *,
    process_id: str, session_blob_id: ObjectId, workdir_blob_id: ObjectId,
) -> None:
    await _collection(db, prefix).replace_one(
        {"processId": process_id},
        {
            "processId": process_id,
            "sessionBlobId": session_blob_id,
            "workdirBlobId": workdir_blob_id,
            "startedAt": datetime.now(timezone.utc),
        },
        upsert=True,
    )


async def load_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one({"processId": process_id})


async def delete_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> None:
    await _collection(db, prefix).delete_many({"processId": process_id})
