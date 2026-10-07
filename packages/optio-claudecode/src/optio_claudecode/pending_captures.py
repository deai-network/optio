"""MongoDB `{prefix}_claudecode_pending_captures`: the blobs a capture is
writing, recorded before it streams the workdir and deleted once the
snapshot record is inserted. One document per processId.

A capture that is cut off (force-cancel past the grace) leaves its record
behind. Cut off while streaming, the recorded workdir blob is partial
(chunks, maybe no `fs.files` document) and Resurrect, or the next capture,
deletes it. Cut off between inserting the snapshot record and deleting this
one, the recorded workdir blob is that snapshot's complete blob and must
stay: `discard_pending_workdir_blob` deletes it only when no snapshot of the
process references it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_claudecode.snapshots import _collection as _snapshots

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


async def discard_pending_workdir_blob(
    db: AsyncIOMotorDatabase, prefix: str, *,
    process_id: str, record: dict,
    delete_blob: Callable[[ObjectId], Awaitable[None]],
) -> None:
    """Delete the workdir blob a pending record names, unless a snapshot of
    this process references it (the capture was cut off after inserting the
    snapshot record). Leaves the pending record itself alone."""
    blob_id = record["workdirBlobId"]
    referenced = await _snapshots(db, prefix).find_one(
        {"processId": process_id, "workdirBlobId": blob_id}, projection={"_id": 1},
    )
    if referenced is None:
        await delete_blob(blob_id)
