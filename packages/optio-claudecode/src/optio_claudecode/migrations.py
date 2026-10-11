"""optio-claudecode's deferred migrations (`optio_core.deferred_migrations`).

Registered at import time; `optio_claudecode/__init__.py` imports this module,
so any app that builds claudecode tasks runs them at engine start, after the
initial task sync (where `ctx.task_for` can reach a task's cipher).

`claudecode_conversation_buffer_out_of_workdir`
(docs/2026-10-11-conversation-buffer-encryption-design.md, section 6). Before
the fix, a conversation-mode teardown wrote the listener's replay buffer to
`<workdir>/.optio-conversation-buffer.json`, so it went into the plaintext
workdir blob of every snapshot. This migration visits every
`{prefix}_claudecode_session_snapshots` document without
`conversationBufferMigrated: true`, oldest first:

* A document that already has `conversationBlobId` (written by the new
  capture) is only marked.
* Otherwise its workdir blob is streamed through `tar_strip.strip_member` into
  a new `workdir` blob without the top-level buffer file.
  - Buffer found and the process has a task: the bytes become the snapshot's
    `conversation` blob, through `task.session_crypto.encrypt` when set
    (plaintext otherwise, like the session blob).
  - Buffer found, no task definition: the bytes are dropped.
  - One `update_one` sets the new `workdirBlobId`, the `conversationBlobId`
    (when stored) and the marker. Then the old workdir blob is deleted.
  - No buffer: the new blob is deleted and the document only marked; the old
    workdir blob stays.
* A missing or unreadable workdir blob cannot be fixed by a retry. The
  document is marked, its blob left as it is, and a warning logged, so one
  broken snapshot does not fail every engine start.

Crash safety: new blobs first, then the document update, then the old blob's
deletion. An interruption before the update leaves only orphan new blobs and
an unmarked document, which the next start redoes. One after it leaves the
old blob orphaned. The migration is recorded as applied only when the function
returns, after every document is marked.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import gridfs.errors
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core import DeferredMigrationContext, deferred_migrations

from optio_claudecode.snapshots import SESSION_SNAPSHOT_COLLECTION_SUFFIX
from optio_claudecode.tar_strip import ArchiveError, strip_member


_LOG = logging.getLogger(__name__)

MIGRATED_MARKER = "conversationBufferMigrated"
# The workdir file older versions wrote the buffer to (session._CONV_BUFFER_FILE).
_BUFFER_FILE = ".optio-conversation-buffer.json"
_READ_CHUNK = 1 << 20


@deferred_migrations.register("claudecode_conversation_buffer_out_of_workdir")
async def claudecode_conversation_buffer_out_of_workdir(
    ctx: DeferredMigrationContext,
) -> None:
    coll = ctx.db[f"{ctx.prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    unmarked = {MIGRATED_MARKER: {"$ne": True}}
    # Ids first: a cursor held open across many blob rewrites could time out.
    ids = [
        d["_id"] async for d in coll.find(
            unmarked, projection={"_id": 1}, sort=[("capturedAt", 1)],
        )
    ]
    if not ids:
        return
    _LOG.info(
        "claudecode_conversation_buffer_out_of_workdir: %d snapshot(s) to check",
        len(ids),
    )
    bucket = AsyncIOMotorGridFSBucket(ctx.db)
    owners: dict[str, str] = {}
    moved = 0
    for snapshot_id in ids:
        doc = await coll.find_one({"_id": snapshot_id, **unmarked})
        if doc is None:
            continue
        moved += await _migrate_snapshot(ctx, coll, bucket, doc, owners)
    _LOG.info(
        "claudecode_conversation_buffer_out_of_workdir: done; %d workdir blob(s) "
        "held a conversation buffer", moved,
    )


async def _migrate_snapshot(ctx, coll, bucket, doc: dict, owners: dict[str, str]) -> int:
    """Migrate one snapshot document. Returns 1 when its workdir blob held the
    buffer (and was rewritten), else 0."""
    mark_only = {"$set": {MIGRATED_MARKER: True}}
    if doc.get("conversationBlobId") is not None:
        await coll.update_one({"_id": doc["_id"]}, mark_only)
        return 0

    process_id = doc["processId"]
    old_id = doc.get("workdirBlobId")
    metadata = {"processId": await _owner(ctx, process_id, owners), "prefix": ctx.prefix}
    try:
        if old_id is None:
            raise gridfs.errors.NoFile("no workdirBlobId")
        download = await bucket.open_download_stream(old_id)
    except gridfs.errors.NoFile:
        _LOG.warning(
            "snapshot %s (process %s): workdir blob %s not found; marked without "
            "a rewrite", doc["_id"], process_id, old_id,
        )
        await coll.update_one({"_id": doc["_id"]}, mark_only)
        return 0

    new_id = ObjectId()
    try:
        async with bucket.open_upload_stream_with_id(
            new_id, "workdir", metadata={**metadata, "name": "workdir"},
        ) as upload:
            buffer = await strip_member(
                _read_chunks(download), upload.write, name=_BUFFER_FILE,
            )
    except ArchiveError as exc:
        await _delete_blob(bucket, new_id)
        _LOG.warning(
            "snapshot %s (process %s): workdir blob %s is not a readable gzip tar "
            "(%s); marked without a rewrite", doc["_id"], process_id, old_id, exc,
        )
        await coll.update_one({"_id": doc["_id"]}, mark_only)
        return 0
    except BaseException:
        await _delete_blob(bucket, new_id)
        raise
    finally:
        download.close()

    if buffer is None:
        await _delete_blob(bucket, new_id)
        await coll.update_one({"_id": doc["_id"]}, mark_only)
        return 0

    update = {"workdirBlobId": new_id, MIGRATED_MARKER: True}
    task = ctx.task_for(process_id)
    try:
        if task is not None:
            crypto = task.session_crypto
            payload = crypto.encrypt(buffer) if crypto and crypto.encrypt else buffer
            update["conversationBlobId"] = await _store_blob(
                bucket, payload, metadata={**metadata, "name": "conversation"},
            )
        # No task definition: no cipher to store the buffer under; it is
        # dropped (design decision 4).
    except BaseException:
        # E.g. the cipher failed: the start fails and is retried; do not leave
        # a workdir copy behind each time. (Not after the update below: a
        # failed update_one may still have been applied.)
        await _delete_blob(bucket, new_id)
        raise
    await coll.update_one({"_id": doc["_id"]}, {"$set": update})
    await _delete_blob(bucket, old_id)
    _LOG.info(
        "snapshot %s (process %s): conversation buffer %s; workdir blob %s "
        "replaced by %s", doc["_id"], process_id,
        "moved to its conversation blob" if task is not None else "dropped (no task)",
        old_id, new_id,
    )
    return 1


async def _owner(ctx, process_id: str, owners: dict[str, str]) -> str:
    """The blob metadata's processId, as `ProcessContext.store_blob` writes it:
    the process document's `_id` as str, or the snapshot's processId when the
    process document is gone."""
    if process_id not in owners:
        proc = await ctx.db[f"{ctx.prefix}_processes"].find_one(
            {"processId": process_id}, projection={"_id": 1}, sort=[("_id", -1)],
        )
        owners[process_id] = str(proc["_id"]) if proc is not None else process_id
    return owners[process_id]


async def _read_chunks(download) -> AsyncIterator[bytes]:
    while chunk := await download.read(_READ_CHUNK):
        yield chunk


async def _store_blob(bucket, data: bytes, *, metadata: dict) -> ObjectId:
    async with bucket.open_upload_stream(metadata["name"], metadata=metadata) as upload:
        await upload.write(data)
    if upload.length != len(data):
        raise RuntimeError(
            f"{metadata['name']} blob short-write: expected {len(data)} bytes, "
            f"stored {upload.length}"
        )
    return upload._id


async def _delete_blob(bucket, file_id: ObjectId) -> None:
    """Delete a GridFS file and its chunks (also chunks without a files
    document, as an aborted upload leaves them)."""
    try:
        await bucket.delete(file_id)
    except gridfs.errors.NoFile:
        pass
