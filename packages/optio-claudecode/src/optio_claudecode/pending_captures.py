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

A record must not outlive the next launch, or a later Resurrect would take
it for that run's capture: `settle_pending_capture` runs at every launch
(and in the Resurrect hook).

The record also names the capture's conversation blob (`conversationBlobId`,
only when the capture stored one; `pending_conversation_blob_id` reads it).
Like the session blob it is stored before the record, and a capture that did
not commit leaves it referenced by no snapshot: the launch settle and the next
capture's stale-record discard delete it then (`discard_pending_conversation_blob`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_claudecode.snapshots import _collection as _snapshots

if TYPE_CHECKING:
    from optio_core.context import ProcessContext

PENDING_CAPTURE_COLLECTION_SUFFIX = "_claudecode_pending_captures"


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{PENDING_CAPTURE_COLLECTION_SUFFIX}"]


async def record_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, *,
    process_id: str, session_blob_id: ObjectId, workdir_blob_id: ObjectId,
    conversation_blob_id: ObjectId | None = None,
) -> None:
    record = {
        "processId": process_id,
        "sessionBlobId": session_blob_id,
        "workdirBlobId": workdir_blob_id,
        "startedAt": datetime.now(timezone.utc),
    }
    if conversation_blob_id is not None:
        record["conversationBlobId"] = conversation_blob_id
    await _collection(db, prefix).replace_one(
        {"processId": process_id}, record, upsert=True,
    )


def pending_conversation_blob_id(record: dict | None) -> ObjectId | None:
    """The conversation blob a pending record names, or None (no record, or
    a capture without a conversation)."""
    if record is None:
        return None
    return record.get("conversationBlobId")


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
) -> bool:
    """Delete the workdir blob a pending record names, unless a snapshot of
    this process references it (the capture was cut off after inserting the
    snapshot record). Leaves the pending record itself alone. Returns True
    when a snapshot references the blob, i.e. that capture had committed."""
    blob_id = record["workdirBlobId"]
    referenced = await _snapshots(db, prefix).find_one(
        {"processId": process_id, "workdirBlobId": blob_id}, projection={"_id": 1},
    )
    if referenced is None:
        await delete_blob(blob_id)
        return False
    return True


async def discard_pending_conversation_blob(
    db: AsyncIOMotorDatabase, prefix: str, *,
    process_id: str, record: dict,
    delete_blob: Callable[[ObjectId], Awaitable[None]],
    keep: ObjectId | None = None,
) -> None:
    """Delete the conversation blob a pending record names, unless a snapshot
    of this process references it as `conversationBlobId` (that capture had
    committed) or it is ``keep``. No-op when the record names none. Leaves
    the pending record itself alone."""
    blob_id = pending_conversation_blob_id(record)
    if blob_id is None or blob_id == keep:
        return
    referenced = await _snapshots(db, prefix).find_one(
        {"processId": process_id, "conversationBlobId": blob_id}, projection={"_id": 1},
    )
    if referenced is None:
        await delete_blob(blob_id)


async def settle_pending_capture(
    ctx: "ProcessContext", *, discard_session_blob: bool = False,
) -> bool:
    """Clear what a cut-off capture of this process recorded: discard its
    workdir blob (guarded as above) and delete the record. True when a
    snapshot references the blob (the capture was cut off after inserting
    its snapshot record); then `mark_has_saved_state()` is called, which
    that capture did not get to.

    ``discard_session_blob`` (the launch path only): for a capture that did
    not commit, also delete the record's session blob unless a snapshot
    references it as ``sessionBlobId``, and its conversation blob unless a
    snapshot references it as ``conversationBlobId``. After a launch they
    are garbage, and left in GridFS a later session-blob fallback could
    adopt them. The Resurrect hook settles without it: its fallback needs
    those blobs."""
    record = await load_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
    if record is None:
        return False
    committed = await discard_pending_workdir_blob(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id, record=record, delete_blob=ctx.delete_blob,
    )
    if committed:
        await ctx.mark_has_saved_state()
    elif discard_session_blob:
        session_blob_id = record["sessionBlobId"]
        referenced = await _snapshots(ctx._db, ctx._prefix).find_one(
            {"processId": ctx.process_id, "sessionBlobId": session_blob_id},
            projection={"_id": 1},
        )
        if referenced is None:
            await ctx.delete_blob(session_blob_id)
        await discard_pending_conversation_blob(
            ctx._db, ctx._prefix,
            process_id=ctx.process_id, record=record, delete_blob=ctx.delete_blob,
        )
    await delete_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
    return committed
