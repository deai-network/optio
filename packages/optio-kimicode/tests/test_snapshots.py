"""Tests for the per-task kimi session snapshot module (Stage 2).

Two-blob layout (claudecode model, not grok's single blob): the sensitive kimi
session subtree (``home/sessions`` — ``state.json`` + ``agents/*/wire.jsonl`` +
``session_index.jsonl``, all under the per-task ``KIMI_CODE_HOME``) is tarred,
optionally encrypted AT REST, and stored as ``sessionBlobId``; the rest of the
workdir (with that subtree wiped) is stored plaintext as ``workdirBlobId``.

``capture -> wipe -> restore`` must round-trip the session dir + the
``session_index.jsonl`` line under the identical workdir path (workDirKey hashes
the absolute workdir; optio fixes the workdir, so the bucket key matches). A
present snapshot that fails to decrypt is FATAL — restore must raise, never a
silent fresh-start.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib

import pytest
from bson import ObjectId

from optio_host.host import LocalHost

from optio_kimicode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SESSION_SUBTREE,
    SNAPSHOT_RETENTION,
    capture_snapshot,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
    restore_snapshot,
)


pytestmark = pytest.mark.asyncio


# --- reversible at-rest cipher (proves decrypt is REQUIRED) ---------------
# Reversing the gzip bytes yields an invalid tar, so extraction fails unless
# the matching decrypt runs — exactly the property a real cipher has.
def _reverse(b: bytes) -> bytes:
    return b[::-1]


def _boom(_b: bytes) -> bytes:
    raise ValueError("decrypt failed (wrong key)")


# --- Mongo metadata helpers (ported from grok, two-blob schema) -----------


async def test_collection_suffix_is_kimicode_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_kimicode_session_snapshots"


async def test_insert_and_load_latest_returns_newest(mongo_db):
    pid = "proc_a"
    first_s, first_w = ObjectId(), ObjectId()
    new_s, new_w = ObjectId(), ObjectId()
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done",
        session_blob_id=first_s, workdir_blob_id=first_w,
    )
    await asyncio.sleep(0.005)
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="cancelled",
        session_blob_id=new_s, workdir_blob_id=new_w,
    )

    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest is not None
    assert latest["endState"] == "cancelled"
    assert latest["sessionBlobId"] == new_s
    assert latest["workdirBlobId"] == new_w


async def test_load_latest_none_when_empty(mongo_db):
    assert await load_latest_snapshot(mongo_db, "opt", "nope") is None


async def test_prune_keeps_five_and_returns_stale_pairs(mongo_db):
    pid = "proc_b"
    pairs: list[tuple[ObjectId, ObjectId]] = []
    for _ in range(7):
        s, w = ObjectId(), ObjectId()
        pairs.append((s, w))
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            session_blob_id=s, workdir_blob_id=w,
        )
        await asyncio.sleep(0.005)

    stale = await prune_snapshots(mongo_db, "opt", pid)
    stale_sessions = {d["sessionBlobId"] for d in stale}
    stale_workdirs = {d["workdirBlobId"] for d in stale}
    assert stale_sessions == {pairs[0][0], pairs[1][0]}
    assert stale_workdirs == {pairs[0][1], pairs[1][1]}

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.count_documents({"processId": pid}) == SNAPSHOT_RETENTION == 5


async def test_prune_noop_within_retention(mongo_db):
    pid = "proc_c"
    for _ in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
        )
        await asyncio.sleep(0.005)
    assert await prune_snapshots(mongo_db, "opt", pid) == []


# --- capture / restore round-trip -----------------------------------------


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def _seed_session_tree(host: LocalHost) -> dict:
    """Populate a realistic kimi session tree + a workdir code file.

    Returns the on-disk contents so the caller can assert an exact round-trip.
    """
    wd = pathlib.Path(host.workdir)
    work_dir_key = "wd_myproj_0123456789ab"
    session_id = "sess-abc123"
    sess_dir = wd / SESSION_SUBTREE / work_dir_key / session_id

    state = json.dumps({"sessionId": session_id, "cwd": host.workdir})
    wire = '{"role":"user","content":"hi"}\n{"role":"assistant","content":"yo"}\n'
    index_line = json.dumps(
        {"sessionId": session_id,
         "sessionDir": f"{work_dir_key}/{session_id}",
         "workDir": host.workdir}
    )
    code = "print('user edit that must survive resume')\n"

    _write(sess_dir / "state.json", state)
    _write(sess_dir / "agents" / "main" / "wire.jsonl", wire)
    _write(wd / SESSION_SUBTREE / "session_index.jsonl", index_line + "\n")
    _write(wd / "main.py", code)

    return {
        "state_rel": f"{SESSION_SUBTREE}/{work_dir_key}/{session_id}/state.json",
        "state": state,
        "wire_rel": f"{SESSION_SUBTREE}/{work_dir_key}/{session_id}/agents/main/wire.jsonl",
        "wire": wire,
        "index_rel": f"{SESSION_SUBTREE}/session_index.jsonl",
        "index": index_line + "\n",
        "code_rel": "main.py",
        "code": code,
    }


async def _make_host(tmp_path: pathlib.Path) -> LocalHost:
    host = LocalHost(str(tmp_path / "task"))
    await host.setup_workdir()
    return host


async def test_capture_wipe_restore_roundtrips_session_tree_and_index(
    ctx_and_captures, tmp_path,
):
    ctx, _cap, _flag = ctx_and_captures
    host = await _make_host(tmp_path)
    expected = await _seed_session_tree(host)

    captured = await capture_snapshot(
        ctx, host, end_state="done", session_blob_encrypt=_reverse,
    )
    assert captured is True

    snapshot = await load_latest_snapshot(ctx._db, ctx._prefix, ctx.process_id)
    assert snapshot is not None

    # Wipe the entire workdir — the resume-from-scratch condition.
    await host.setup_workdir()
    wd = pathlib.Path(host.workdir)
    assert not (wd / SESSION_SUBTREE).exists()
    assert not (wd / "main.py").exists()

    await restore_snapshot(ctx, host, snapshot, session_blob_decrypt=_reverse)

    # Session dir + index line round-tripped byte-for-byte, under the SAME path.
    for key in ("state", "wire", "index", "code"):
        rel = expected[f"{key}_rel"]
        assert (wd / rel).read_text(encoding="utf-8") == expected[key], rel


async def test_capture_skips_when_no_session_tree(ctx_and_captures, tmp_path):
    ctx, _cap, _flag = ctx_and_captures
    host = await _make_host(tmp_path)
    # A workdir with code but NO home/sessions: --continue would have nothing to
    # resume, so a resumable snapshot must NOT be created.
    (pathlib.Path(host.workdir) / "main.py").write_text("x = 1\n", encoding="utf-8")

    captured = await capture_snapshot(ctx, host, end_state="done")
    assert captured is False
    assert await load_latest_snapshot(ctx._db, ctx._prefix, ctx.process_id) is None


async def test_restore_raises_on_decrypt_failure(ctx_and_captures, tmp_path):
    ctx, _cap, _flag = ctx_and_captures
    host = await _make_host(tmp_path)
    await _seed_session_tree(host)

    assert await capture_snapshot(
        ctx, host, end_state="done", session_blob_encrypt=_reverse,
    ) is True
    snapshot = await load_latest_snapshot(ctx._db, ctx._prefix, ctx.process_id)

    await host.setup_workdir()  # wipe, then attempt restore with a broken key
    with pytest.raises(ValueError):
        await restore_snapshot(ctx, host, snapshot, session_blob_decrypt=_boom)


# --- Snapshot history: the list_snapshots / discard_snapshots_after hooks ---

TS = "2026-11-19T13:05:12.345Z"


def _task(supports_resume=True):
    from optio_kimicode import KimiCodeTaskConfig, create_kimicode_task
    return create_kimicode_task(
        process_id="p", name="P",
        config=KimiCodeTaskConfig(
            consumer_instructions="do the thing", fs_isolation=False,
            supports_resume=supports_resume,
        ),
    )


async def _blob(db, name):
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    return await AsyncIOMotorGridFSBucket(db).upload_from_stream(name, b"payload")


async def _blob_exists(db, blob_id):
    return await db["fs.files"].find_one({"_id": blob_id}) is not None


async def _two_snapshots(db):
    """Two snapshots of process "p" under prefix "t", captured at 10:00 and at
    TS, each with real session and workdir blobs. Returns (older, newer) as
    (document id, [session, workdir blob ids])."""
    from datetime import datetime, timezone
    out = []
    for captured_at in (
        datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 19, 13, 5, 12, 345000, tzinfo=timezone.utc),
    ):
        blobs = [await _blob(db, "session"), await _blob(db, "workdir")]
        doc = await insert_snapshot(
            db, "t", process_id="p", end_state="done",
            session_blob_id=blobs[0], workdir_blob_id=blobs[1],
        )
        await db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].update_one(
            {"_id": doc["_id"]}, {"$set": {"capturedAt": captured_at}},
        )
        out.append((doc["_id"], blobs))
    return out[0], out[1]


def _ctx(db):
    from types import SimpleNamespace
    return SimpleNamespace(process_id="p", _db=db, _prefix="t")


async def test_snapshot_layout():
    from optio_agents.snapshots import SnapshotLayout
    from optio_kimicode.snapshots import SNAPSHOT_LAYOUT
    assert SNAPSHOT_LAYOUT == SnapshotLayout(
        "_kimicode_session_snapshots",
        required_blob_fields=("sessionBlobId", "workdirBlobId"),
    )
    assert SNAPSHOT_LAYOUT.optional_blob_fields == ()
    assert SNAPSHOT_LAYOUT.suffix == SESSION_SNAPSHOT_COLLECTION_SUFFIX


async def test_task_wires_snapshot_hooks():
    resumable = _task(supports_resume=True)
    assert resumable.list_snapshots is not None
    assert resumable.discard_snapshots_after is not None
    plain = _task(supports_resume=False)
    assert plain.list_snapshots is None
    assert plain.discard_snapshots_after is None


async def test_hooks_list_and_discard(mongo_db):
    (older_id, older_blobs), (newer_id, newer_blobs) = await _two_snapshots(mongo_db)
    task = _task()
    ctx = _ctx(mongo_db)

    assert await task.list_snapshots(ctx) == [
        {"capturedAt": TS, "endState": "done"},
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]
    assert await task.discard_snapshots_after(ctx, "2026-11-19T10:00:00.000Z") == 1

    coll = mongo_db[f"t{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.find_one({"_id": newer_id}) is None
    assert await coll.find_one({"_id": older_id}) is not None
    for blob_id in newer_blobs:
        assert not await _blob_exists(mongo_db, blob_id)
    for blob_id in older_blobs:
        assert await _blob_exists(mongo_db, blob_id)
    assert await task.list_snapshots(ctx) == [
        {"capturedAt": "2026-11-19T10:00:00.000Z", "endState": "done"},
    ]
