"""MongoDB ``{prefix}_kimicode_session_snapshots`` collection + session-tree
capture/restore (Stage 2).

One document per terminal run per process_id. Two-blob layout::

    {
      _id:           ObjectId,
      processId:     str,
      capturedAt:    datetime,
      endState:      str,          # "done" | "cancelled" | "rescued"
      sessionBlobId: ObjectId,     # GridFS — tar.gz of home/sessions, optionally
                                   #          encrypted AT REST
      workdirBlobId: ObjectId,     # GridFS — plaintext tar.gz of the workdir minus
                                   #          the session subtree
    }

Unlike optio-grok (a single plaintext workdir blob), kimi splits the sensitive
session store out so it can be encrypted at rest — the optio-claudecode model.
``KIMI_CODE_HOME`` is ``<workdir>/home``, so kimi writes its session store under
``home/sessions`` (``state.json`` + ``agents/*/wire.jsonl`` + subdirs, plus the
``session_index.jsonl`` line index that ``--continue``/list read). The per-task
home is fully isolated, so the whole ``home/sessions`` subtree — every
``<workDirKey>/<sessionId>`` dir and the matching ``session_index.jsonl`` lines —
belongs to this task; tarring it captures exactly "the session dir + the index
line".

**workDirKey pinning.** kimi buckets sessions under
``wd_<slug>_<sha256(absWorkDir)[:12]>``. Restore therefore MUST land under the
identical absolute workdir path, or the bucket key drifts and ``--continue``
misses. optio fixes the workdir (deterministic taskdir), so restore simply
extracts the subtree back to the same relative path — no rekeying.

**Fail-loud decrypt.** A present snapshot that fails to decrypt is FATAL: the
decrypt call in :func:`restore_snapshot` is intentionally outside any
``try``/``except`` so it surfaces to the caller. Never a silent fresh-start.

Retention: keep the latest ``SNAPSHOT_RETENTION`` per processId; older rows are
deleted by :func:`prune_snapshots`, which returns their ``{sessionBlobId,
workdirBlobId}`` so the caller can delete the corresponding GridFS blobs.
"""

from __future__ import annotations

import logging
import shlex
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_host.host import Host


_LOG = logging.getLogger(__name__)

SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_kimicode_session_snapshots"
SNAPSHOT_RETENTION = 5

# Sensitive kimi session subtree, RELATIVE to the workdir. KIMI_CODE_HOME =
# <workdir>/home, so kimi's session store lives at <workdir>/home/sessions.
SESSION_SUBTREE = "home/sessions"


# --- Mongo metadata helpers (ported from grok, two-blob schema) -----------


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
    session_blob_id: ObjectId,
    workdir_blob_id: ObjectId,
) -> dict:
    """Insert one snapshot row and return the stored document (with ``_id``)."""
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "sessionBlobId": session_blob_id,
        "workdirBlobId": workdir_blob_id,
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
) -> list[dict]:
    """Keep the latest ``retention`` snapshots; delete the rest.

    Returns ``{sessionBlobId, workdirBlobId}`` for each deleted snapshot so the
    caller can remove the corresponding GridFS blobs.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"sessionBlobId": 1, "workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[retention:]
    if not stale:
        return []
    await coll.delete_many({"_id": {"$in": [d["_id"] for d in stale]}})
    return [
        {"sessionBlobId": d["sessionBlobId"], "workdirBlobId": d["workdirBlobId"]}
        for d in stale
    ]


# --- session-tree archive/extract (subtree tar over the Host) -------------


async def _archive_session_tree(host: Host) -> bytes:
    """tar.gz the sensitive ``home/sessions`` subtree and fetch it as bytes."""
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-kimicode-session.tar.gz"
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)} "
        f"{shlex.quote(SESSION_SUBTREE)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"tar {SESSION_SUBTREE} failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _extract_session_tree(host: Host, plain: bytes) -> None:
    """Extract the decrypted ``home/sessions`` tar over the workdir.

    Lands under the identical workdir path (workDirKey pinning), on top of a
    restored workdir tar.
    """
    workdir = host.workdir.rstrip("/")
    tmpfile = f"{workdir}/.optio-kimicode-restore.tar.gz"
    await host.put_file_to_host(plain, tmpfile)
    try:
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(workdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"tar -x {SESSION_SUBTREE} failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _read_blob_bytes(ctx, blob_id: ObjectId) -> bytes:
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _stream_blob(ctx, blob_id: ObjectId) -> AsyncIterator[bytes]:
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _store_session_blob(
    ctx,
    host: Host,
    *,
    encrypt: "Callable[[bytes], bytes] | None",
) -> ObjectId:
    """Tar ``home/sessions``, optionally encrypt, store as a GridFS blob."""
    session_bytes = await _archive_session_tree(host)
    enc = encrypt or (lambda b: b)
    payload = enc(session_bytes)
    expected_len = len(payload)
    async with ctx.store_blob("session") as swriter:
        await swriter.write(payload)
        session_blob_id = swriter.file_id
        written = getattr(swriter, "_position", None)
        if written is not None and written != expected_len:
            raise RuntimeError(
                f"session blob short-write: expected {expected_len} bytes, "
                f"GridIn._position is {written}"
            )
    return session_blob_id


# --- capture / restore orchestration --------------------------------------


async def capture_snapshot(
    ctx,
    host: Host,
    *,
    end_state: str,
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None,
    workdir_exclude: list[str] | None = None,
) -> bool:
    """Capture a two-blob resume snapshot of the (now static) workdir.

    Returns ``True`` when a snapshot row was written, ``False`` when capture was
    skipped because there is no session tree to resume.

    Steps: (0) refuse to snapshot when ``home/sessions`` holds no session — a
    ``--continue`` with nothing to resume drops the agent to a fresh start, so
    the degenerate snapshot must never be CREATED (the resume path ignores
    ``hasSavedState``). (1) tar + encrypt the session subtree → session blob.
    (2) defensively wipe ``home/sessions`` so the plaintext workdir tar cannot
    carry it. (3) stream the workdir tar → workdir blob. (4) insert the row.
    (5) prune to retention, deleting stale blobs. (6) mark resumable.
    """
    workdir = host.workdir.rstrip("/")

    # 0. Session-present guard.
    chk = await host.run_command(
        f"find {shlex.quote(workdir + '/' + SESSION_SUBTREE)} -type f "
        f"-print -quit 2>/dev/null || true"
    )
    if not chk.stdout.strip():
        _LOG.warning(
            "snapshot capture skipped: %s holds no session; refusing to mark "
            "resumable", SESSION_SUBTREE,
        )
        return False

    # 1. tar + encrypt the sensitive session subtree.
    session_blob_id = await _store_session_blob(
        ctx, host, encrypt=session_blob_encrypt,
    )

    # 2. defensive wipe so the plaintext workdir tar cannot carry the session.
    await host.run_command(f"rm -rf {shlex.quote(workdir)}/{SESSION_SUBTREE}")

    # 3. stream the plaintext workdir tar.
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    # 4. insert the snapshot row.
    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
    )

    # 5. prune + delete stale blobs (both members).
    for stale in await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id):
        for key in ("sessionBlobId", "workdirBlobId"):
            try:
                await ctx.delete_blob(stale[key])
            except Exception:
                _LOG.exception("delete_blob(%s) failed", key)

    # 6. surface the Resume affordance.
    await ctx.mark_has_saved_state()
    return True


async def restore_snapshot(
    ctx,
    host: Host,
    snapshot: dict,
    *,
    session_blob_decrypt: "Callable[[bytes], bytes] | None" = None,
) -> None:
    """Restore a two-blob snapshot: workdir tar, then the session subtree on top.

    The session subtree lands under the identical workdir path (workDirKey pins
    on the absolute workdir; optio fixes the workdir). The ``decrypt`` call is
    intentionally OUTSIDE any ``try``/``except``: a present snapshot that fails
    to decrypt is FATAL and must surface to the caller — never a silent
    fresh-start.
    """
    # 1. Restore the plaintext workdir (repopulates everything but the session
    #    subtree, which was wiped before archiving).
    await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))

    # 2. Decrypt + extract the session subtree on top.
    payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
    decrypt = session_blob_decrypt or (lambda b: b)
    plain = decrypt(payload)
    await _extract_session_tree(host, plain)
