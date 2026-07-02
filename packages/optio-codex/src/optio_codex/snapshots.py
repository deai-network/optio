"""MongoDB ``{prefix}_codex_session_snapshots`` collection helpers (Stage 2).

One document per terminal run per process_id. Layout:

    {
      _id:           ObjectId,
      processId:     str,
      capturedAt:    datetime,
      endState:      str,          # "done" | "cancelled" | "rescued"
      workdirBlobId: ObjectId,     # GridFS — tar.gz of the whole workdir
      sessionId:     str | None,   # codex session/rollout UUID at capture
    }

Single-blob, like optio-grok: codex persists per-session rollout JSONLs
under ``$CODEX_HOME/sessions`` (= ``<workdir>/home/.codex/sessions``), which
rides the workdir tar. The layout is path-portable (design-doc probe: a
sessions/ tree copied to a different CODEX_HOME resumes fine; the sqlite
index is derived and rebuilt — hence excluded below). Unlike grok there IS
a recorded ``sessionId``: codex must be resumed by explicit id
(``codex resume <id>``) — ``resume --last`` is cwd-filtered and silently
starts a NEW session on a miss, so optio records the id at snapshot time
and replays it on relaunch. ``sessionId`` may be ``None`` when no rollout
existed at capture time (resume then degrades to a fresh launch in the
restored workdir, loudly logged). Conversation mode (Plan D) passes its
``thread/started`` id through the same field.

Retention: keep the latest ``SNAPSHOT_RETENTION`` per processId. Older rows
are deleted by ``prune_snapshots``, which returns their workdir GridFS blob
ids so the caller can delete the corresponding blobs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_codex_session_snapshots"
SNAPSHOT_RETENTION = 5

# Default snapshot exclude list (used when ``CodexTaskConfig.workdir_exclude``
# is None): the framework defaults plus the CODEX_HOME junk pinned in the
# design doc. MUST NOT exclude ``home/.codex/sessions`` — the rollout JSONLs
# there are the resume source. Pattern semantics (optio_host.archive):
# fnmatch against the full workdir-relative path AND against every single
# path segment — so ``*.sqlite*`` matches anywhere, while the multi-segment
# ``home/.codex/...`` entries prune exactly one subtree.
CODEX_WORKDIR_EXCLUDE_DEFAULT: list[str] = [
    *DEFAULT_WORKDIR_EXCLUDES,
    "home/.codex/packages",         # ~286 MB binary cache; re-seeded, never snapshotted
    "*.sqlite*",                    # derived rollout index; absolute-path poison
    "home/.codex/cache",
    "home/.codex/tmp",
    "home/.codex/.tmp",
    "home/.codex/shell_snapshots",
    "home/.codex/models_cache.json",
    "home/.codex/version.json",
    "home/.codex/installation_id",
    "home/.codex/log",
    "home/.cache",                  # per-task XDG cache — junk, can be large
]


def effective_workdir_exclude(workdir_exclude: list[str] | None) -> list[str]:
    """The exclude list a snapshot will actually honor.

    ``None`` (the config default) means the codex defaults above — NOT the
    bare framework defaults. Single source of truth for the archive call
    (``session._capture_snapshot``) and the AGENTS.md resume-section
    rendering (``prompt``), so the prompt's preservation claims can never
    drift from what is actually snapshotted.
    """
    if workdir_exclude is None:
        return CODEX_WORKDIR_EXCLUDE_DEFAULT
    return workdir_exclude


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
    session_id: str | None,
) -> dict:
    """Insert one snapshot row and return the stored document (with ``_id``)."""
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
