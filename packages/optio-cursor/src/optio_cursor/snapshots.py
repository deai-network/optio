"""MongoDB ``{prefix}_cursor_session_snapshots`` collection helpers (Stage 2).

One document per terminal run per process_id. Layout:

    {
      _id:           ObjectId,
      processId:     str,
      capturedAt:    datetime,
      endState:      str,          # "done" | "cancelled" | "rescued"
      workdirBlobId: ObjectId,     # GridFS — tar.gz of the whole workdir
      sessionId:     str | None,   # cursor ACP session id at capture (conversation)
    }

Single-blob, unlike optio-claudecode: cursor persists its chat state under
``$HOME/.cursor`` (``<workdir>/home/.cursor``), which lives inside the
workdir tar. So there is no separate encrypted session blob — the workdir
blob carries everything cursor needs to resume.

The recorded ``sessionId`` is cursor's ACP session id (from the ``session/new``
response), captured for CONVERSATION mode only: on resume the wrapper replays
the prior conversation by calling ACP ``session/load(sessionId)`` DIRECTLY
against the restored on-disk session store, skipping the ``session/list`` +
most-recent heuristic (which can mispick an empty session as they accumulate).
Field name mirrors optio-grok/optio-codex for cross-engine consistency.
``sessionId`` is ``None`` for the iframe (ttyd) mode — where resume is a plain
workdir restore + ``cursor-agent --continue`` — and for pre-seam snapshot rows,
where resume falls back to the ``session/list`` heuristic.

Retention: keep the latest ``SNAPSHOT_RETENTION`` per processId. Older rows
are deleted by ``prune_snapshots``, which returns their workdir GridFS blob ids
so the caller can delete the corresponding blobs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_cursor_session_snapshots"
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
    prefix: str,
    *,
    process_id: str,
    end_state: str,
    workdir_blob_id: ObjectId,
    session_id: str | None = None,
) -> dict:
    """Insert one snapshot row and return the stored document (with ``_id``).

    ``session_id`` is cursor's ACP session id (conversation mode) so resume can
    replay history via ``session/load`` directly; ``None`` for iframe mode and
    pre-seam rows (defaults to ``None`` so the non-conversation callers stay
    unchanged, and resume then falls back to the ``session/list`` heuristic)."""
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "workdirBlobId": workdir_blob_id,
        "sessionId": session_id,
    }
    result = await _collection(db, prefix).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def load_latest_snapshot(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one(
        {"processId": process_id}, sort=[("capturedAt", -1)],
    )


async def prune_snapshots(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_id: str,
    *,
    retention: int = SNAPSHOT_RETENTION,
) -> list[ObjectId]:
    """Keep the latest ``retention`` snapshots; delete the rest.

    Returns the ``workdirBlobId`` of each deleted snapshot so the caller can
    remove the corresponding GridFS blob.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[retention:]
    if not stale:
        return []
    await coll.delete_many({"_id": {"$in": [d["_id"] for d in stale]}})
    return [d["workdirBlobId"] for d in stale]
