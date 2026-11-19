"""Tests for the per-task codex session snapshot collection (Stage 2).

Single-blob layout: codex stores its rollout JSONLs under
``$CODEX_HOME/sessions`` which lives inside the preserved workdir tar, so a
snapshot references only ``workdirBlobId`` — plus the recorded ``sessionId``
(codex resumes ONLY by explicit id; ``resume --last`` is cwd-filtered and
silently starts a new session on a miss).
"""

import asyncio
import io
import tarfile

import pytest
from bson import ObjectId

from optio_codex.snapshots import (
    CODEX_WORKDIR_EXCLUDE_DEFAULT,
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    effective_workdir_exclude,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


pytestmark = pytest.mark.asyncio


async def test_collection_suffix_is_codex_specific():
    assert SESSION_SNAPSHOT_COLLECTION_SUFFIX == "_codex_session_snapshots"


async def test_insert_and_load_latest_returns_newest(mongo_db):
    pid = "proc_a"
    first = ObjectId()
    newest = ObjectId()
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="done",
        workdir_blob_id=first,
        session_id="11111111-1111-1111-1111-111111111111",
    )
    await asyncio.sleep(0.005)
    await insert_snapshot(
        mongo_db, "opt", process_id=pid, end_state="cancelled",
        workdir_blob_id=newest,
        session_id="22222222-2222-2222-2222-222222222222",
    )

    latest = await load_latest_snapshot(mongo_db, "opt", pid)
    assert latest is not None
    assert latest["endState"] == "cancelled"
    assert latest["workdirBlobId"] == newest
    assert latest["sessionId"] == "22222222-2222-2222-2222-222222222222"
    # Single-blob schema: no separate session blob field.
    assert "sessionBlobId" not in latest


async def test_insert_allows_none_session_id(mongo_db):
    """The sessionId seam stays optional: a capture that found no rollout
    (codex died pre-persist) and — later, Plan D — the conversation-mode
    caller both pass None/their own id through the same parameter."""
    await insert_snapshot(
        mongo_db, "opt", process_id="proc_none", end_state="done",
        workdir_blob_id=ObjectId(), session_id=None,
    )
    latest = await load_latest_snapshot(mongo_db, "opt", "proc_none")
    assert latest is not None
    assert latest["sessionId"] is None


async def test_load_latest_none_when_empty(mongo_db):
    assert await load_latest_snapshot(mongo_db, "opt", "nope") is None


async def test_prune_keeps_five_and_returns_two_stale_ids(mongo_db):
    pid = "proc_b"
    blob_ids: list[ObjectId] = []
    for _ in range(7):
        wid = ObjectId()
        blob_ids.append(wid)
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            workdir_blob_id=wid, session_id=None,
        )
        await asyncio.sleep(0.005)

    stale = await prune_snapshots(mongo_db, "opt", pid)
    # The two oldest blob ids are returned for caller-side deletion.
    assert set(stale) == set(blob_ids[:2])

    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    assert await coll.count_documents({"processId": pid}) == SNAPSHOT_RETENTION == 5


async def test_prune_noop_within_retention(mongo_db):
    pid = "proc_c"
    for _ in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, "opt", process_id=pid, end_state="done",
            workdir_blob_id=ObjectId(), session_id=None,
        )
        await asyncio.sleep(0.005)
    assert await prune_snapshots(mongo_db, "opt", pid) == []


async def test_effective_workdir_exclude_resolution():
    assert effective_workdir_exclude(None) == CODEX_WORKDIR_EXCLUDE_DEFAULT
    assert effective_workdir_exclude(["x"]) == ["x"]
    assert effective_workdir_exclude([]) == []


async def test_default_excludes_never_touch_the_session_store():
    """MUST NOT exclude home/.codex/sessions — it is the resume source."""
    assert not any("sessions" in p for p in CODEX_WORKDIR_EXCLUDE_DEFAULT)


async def test_default_excludes_drop_codex_junk_keep_sessions(tmp_path):
    """End-to-end against the real archive builder: the design-doc junk is
    dropped, the rollout store and the working files survive."""
    from optio_host.archive import yield_workdir_archive

    wd = tmp_path / "workdir"
    keep = [
        "home/.codex/sessions/2026/07/02/"
        "rollout-2026-07-02T10-00-00-01234567-89ab-cdef-0123-456789abcdef.jsonl",
        "home/.codex/auth.json",
        "home/.codex/config.toml",
        "deliverables/out.txt",
        "AGENTS.md",
        "resume.log",
    ]
    drop = [
        "home/.codex/packages/blob.bin",
        "home/.codex/state.sqlite3",
        "home/.codex/cache/models.json",
        "home/.codex/tmp/scratch",
        "home/.codex/.tmp/scratch2",
        "home/.codex/shell_snapshots/snap1",
        "home/.codex/version.json",
        "home/.codex/installation_id",
        "home/.codex/log/codex.log",
        "home/.cache/junk",
        ".git/HEAD",
    ]
    for rel in (*keep, *drop):
        p = wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")

    chunks = []
    async for chunk in yield_workdir_archive(str(wd), CODEX_WORKDIR_EXCLUDE_DEFAULT):
        chunks.append(chunk)
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:gz") as tar:
        names = set(tar.getnames())

    for rel in keep:
        assert rel in names, f"expected {rel} to be preserved"
    for rel in drop:
        assert rel not in names, f"expected {rel} to be excluded"


# --- Snapshot history: the list_snapshots / discard_snapshots_after hooks ---

TS = "2026-11-19T13:05:12.345Z"


def _task(supports_resume=True):
    from optio_codex import CodexTaskConfig, create_codex_task
    return create_codex_task(
        process_id="p", name="P",
        config=CodexTaskConfig(
            consumer_instructions="x", delivery_type="audit", mode="iframe",
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
    TS, each with a real workdir blob. Returns (older, newer) as
    (document id, [workdir blob id])."""
    from datetime import datetime, timezone
    out = []
    for captured_at in (
        datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 19, 13, 5, 12, 345000, tzinfo=timezone.utc),
    ):
        blobs = [await _blob(db, "workdir")]
        doc = await insert_snapshot(
            db, "t", process_id="p", end_state="done",
            workdir_blob_id=blobs[0], session_id=None,
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
    from optio_codex.snapshots import SNAPSHOT_LAYOUT
    assert SNAPSHOT_LAYOUT == SnapshotLayout(
        "_codex_session_snapshots",
        required_blob_fields=("workdirBlobId",),
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
