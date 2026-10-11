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
  - One compare-and-set `update_one` (the document must still name the old
    blob and be unmarked) sets the new `workdirBlobId`, the
    `conversationBlobId` (when stored), the marker and
    `migratedFromWorkdirBlobId: <old>`. No match (another writer changed the
    document): the new blobs are deleted and the old one kept.
  - Then `_finish_rewrite`: pending-capture records naming the old blob are
    repointed to the new one, so a capture cut off between its snapshot
    insert and its pending-record delete still settles as committed; the old
    blob is deleted (unless a snapshot of the process still names it); the
    `migratedFromWorkdirBlobId` field is removed.
  - No buffer: the new blob is deleted and the document only marked; the old
    workdir blob stays.
* A missing workdir blob, a corrupt one (`gridfs.errors.CorruptGridFile`: a
  missing or short chunk) or one that is not an intact gzip tar of copyable
  members (`tar_strip.ArchiveError`) cannot be fixed by a retry. The document
  is marked, its blob left as it is, and a warning logged, so one broken
  snapshot does not fail every engine start.

Crash safety: new blobs first, then the document update, then the old blob's
deletion. An interruption before the update leaves only orphan new blobs and
an unmarked document, which the next start redoes. One after it (also an
update that raised after being applied, or a failed delete) leaves the
document carrying `migratedFromWorkdirBlobId`; every run starts by finishing
those documents, so the old plaintext blob does not stay behind. The
migration is recorded as applied only when the function returns, after every
document is marked and finished.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator

import gridfs.errors
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core import DeferredMigrationContext, deferred_migrations

from optio_claudecode.pending_captures import PENDING_CAPTURE_COLLECTION_SUFFIX
from optio_claudecode.snapshots import SESSION_SNAPSHOT_COLLECTION_SUFFIX
from optio_claudecode.tar_strip import ArchiveError, strip_member


_LOG = logging.getLogger(__name__)

MIGRATED_MARKER = "conversationBufferMigrated"
MIGRATED_FROM = "migratedFromWorkdirBlobId"
# The workdir file older versions wrote the buffer to (session._CONV_BUFFER_FILE).
_BUFFER_FILE = ".optio-conversation-buffer.json"
_READ_CHUNK = 1 << 20
_NAME = "claudecode_conversation_buffer_out_of_workdir"
_UNMARKED = {MIGRATED_MARKER: {"$ne": True}}
# A progress line every this many documents (and one at the end).
_PROGRESS_EVERY = 25


@deferred_migrations.register(_NAME)
async def claudecode_conversation_buffer_out_of_workdir(
    ctx: DeferredMigrationContext,
) -> None:
    run = _Run(ctx)
    await run.finish_interrupted_rewrites()
    # Ids first: a cursor held open across many blob rewrites could time out.
    ids = [
        d["_id"] async for d in run.snapshots.find(
            _UNMARKED, projection={"_id": 1}, sort=[("capturedAt", 1)],
        )
    ]
    if not ids:
        return
    _LOG.info("%s: %d snapshot(s) to check", _NAME, len(ids))
    for snapshot_id in ids:
        doc = await run.snapshots.find_one({"_id": snapshot_id, **_UNMARKED})
        if doc is None:
            continue
        await run.migrate(doc)
        if run.counts.visited % _PROGRESS_EVERY == 0:
            # str() now: the counts object keeps changing after the call.
            _LOG.info("%s: %s", _NAME, str(run.counts))
    _LOG.info("%s: done; %s", _NAME, str(run.counts))


@dataclass
class _Counts:
    visited: int = 0
    stripped: int = 0         # buffer found, workdir blob rewritten
    records_stored: int = 0   # ... and stored as the conversation blob
    dropped: int = 0          # ... and dropped (no task definition)
    marked_only: int = 0      # new-style snapshot, or no buffer
    corrupt: int = 0          # missing, corrupt or unreadable workdir blob
    changed: int = 0          # the document changed under us; left alone

    def __str__(self) -> str:
        return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in vars(self).items())


class _Run:
    def __init__(self, ctx: DeferredMigrationContext) -> None:
        self.ctx = ctx
        self.snapshots = ctx.db[f"{ctx.prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
        self.pending = ctx.db[f"{ctx.prefix}{PENDING_CAPTURE_COLLECTION_SUFFIX}"]
        self.bucket = AsyncIOMotorGridFSBucket(ctx.db)
        self.owners: dict[str, str] = {}
        self.counts = _Counts()

    async def finish_interrupted_rewrites(self) -> None:
        """Finish documents a previous run updated but did not finish."""
        docs = await self.snapshots.find(
            {MIGRATED_FROM: {"$exists": True}},
            projection={"processId": 1, "workdirBlobId": 1, MIGRATED_FROM: 1},
        ).to_list(None)
        for doc in docs:
            _LOG.info(
                "%s: finishing snapshot %s (old workdir blob %s)",
                _NAME, doc["_id"], doc[MIGRATED_FROM],
            )
            await self._finish_rewrite(
                doc["_id"], doc["processId"], doc[MIGRATED_FROM], doc["workdirBlobId"],
            )

    async def migrate(self, doc: dict) -> None:
        self.counts.visited += 1
        if doc.get("conversationBlobId") is not None:
            await self._mark(doc)
            self.counts.marked_only += 1
            return

        process_id = doc["processId"]
        old_id = doc.get("workdirBlobId")
        metadata = {"processId": await self._owner(process_id), "prefix": self.ctx.prefix}
        try:
            if old_id is None:
                raise gridfs.errors.NoFile("no workdirBlobId")
            download = await self.bucket.open_download_stream(old_id)
        except gridfs.errors.NoFile:
            await self._mark_corrupt(doc, f"workdir blob {old_id} not found")
            return

        new_id = ObjectId()
        try:
            async with self.bucket.open_upload_stream_with_id(
                new_id, "workdir", metadata={**metadata, "name": "workdir"},
            ) as upload:
                buffer = await strip_member(
                    _read_chunks(download), upload.write, name=_BUFFER_FILE,
                )
        except (ArchiveError, gridfs.errors.CorruptGridFile) as exc:
            await _delete_blob(self.bucket, new_id)
            await self._mark_corrupt(doc, f"workdir blob {old_id}: {exc}")
            return
        except BaseException:
            await _delete_blob(self.bucket, new_id)
            raise
        finally:
            download.close()

        if buffer is None:
            await _delete_blob(self.bucket, new_id)
            await self._mark(doc)
            self.counts.marked_only += 1
            return

        task = self.ctx.task_for(process_id)
        conversation_id = None
        try:
            if task is not None:
                crypto = task.session_crypto
                payload = crypto.encrypt(buffer) if crypto and crypto.encrypt else buffer
                conversation_id = await _store_blob(
                    self.bucket, payload, metadata={**metadata, "name": "conversation"},
                )
            # No task definition: no cipher to store the buffer under; it is
            # dropped (design decision 4).
        except BaseException:
            # E.g. the cipher failed: the start fails and is retried; do not
            # leave a workdir copy behind each time. (Not after the update
            # below: a failed update_one may still have been applied.)
            await _delete_blob(self.bucket, new_id)
            raise

        update = {"workdirBlobId": new_id, MIGRATED_MARKER: True, MIGRATED_FROM: old_id}
        if conversation_id is not None:
            update["conversationBlobId"] = conversation_id
        result = await self.snapshots.update_one(
            {"_id": doc["_id"], "workdirBlobId": old_id, **_UNMARKED}, {"$set": update},
        )
        if result.matched_count == 0:
            await _delete_blob(self.bucket, new_id)
            if conversation_id is not None:
                await _delete_blob(self.bucket, conversation_id)
            _LOG.warning(
                "%s: snapshot %s (process %s) changed while it was rewritten; "
                "left alone", _NAME, doc["_id"], process_id,
            )
            self.counts.changed += 1
            return
        self.counts.stripped += 1
        if conversation_id is not None:
            self.counts.records_stored += 1
        else:
            self.counts.dropped += 1
        await self._finish_rewrite(doc["_id"], process_id, old_id, new_id)
        _LOG.debug(
            "%s: snapshot %s (process %s): buffer %s; workdir blob %s replaced by %s",
            _NAME, doc["_id"], process_id,
            "stored" if conversation_id is not None else "dropped (no task)",
            old_id, new_id,
        )

    async def _finish_rewrite(
        self, doc_id: ObjectId, process_id: str, old_id: ObjectId, new_id: ObjectId,
    ) -> None:
        """After the document names the new workdir blob: repoint pending-capture
        records, delete the old blob, drop the document's
        `migratedFromWorkdirBlobId`. Idempotent; a failure leaves the field
        set, and the next run's `finish_interrupted_rewrites` redoes it."""
        await self.pending.update_many(
            {"processId": process_id, "workdirBlobId": old_id},
            {"$set": {"workdirBlobId": new_id}},
        )
        if await self.snapshots.count_documents(
            {"processId": process_id, "workdirBlobId": old_id}, limit=1,
        ):
            _LOG.warning(
                "%s: workdir blob %s is still named by a snapshot of process %s; "
                "kept", _NAME, old_id, process_id,
            )
        else:
            await _delete_blob(self.bucket, old_id)
        await self.snapshots.update_one(
            {"_id": doc_id, MIGRATED_FROM: old_id}, {"$unset": {MIGRATED_FROM: ""}},
        )

    async def _mark(self, doc: dict) -> None:
        await self.snapshots.update_one(
            {"_id": doc["_id"]}, {"$set": {MIGRATED_MARKER: True}},
        )

    async def _mark_corrupt(self, doc: dict, problem: str) -> None:
        _LOG.warning(
            "%s: snapshot %s (process %s): %s; marked without a rewrite, blob kept",
            _NAME, doc["_id"], doc["processId"], problem,
        )
        await self._mark(doc)
        self.counts.corrupt += 1

    async def _owner(self, process_id: str) -> str:
        """The blob metadata's processId, as `ProcessContext.store_blob` writes
        it: the process document's `_id` as str, or the snapshot's processId
        when the process document is gone."""
        if process_id not in self.owners:
            proc = await self.ctx.db[f"{self.ctx.prefix}_processes"].find_one(
                {"processId": process_id}, projection={"_id": 1}, sort=[("_id", -1)],
            )
            self.owners[process_id] = str(proc["_id"]) if proc is not None else process_id
        return self.owners[process_id]


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
