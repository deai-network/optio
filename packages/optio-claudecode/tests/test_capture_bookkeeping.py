"""Capture bookkeeping for Resurrect: pending-capture record, unsaved-work
flag, credentials guard, keeping the taskdir after a failed capture."""

import os

import pytest
from bson import ObjectId

import optio_claudecode.session as S
from optio_claudecode import pending_captures as PC
from optio_claudecode.snapshots import load_latest_snapshot
from optio_host.host import LocalHost


def _local_host(tmp_path):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)
    with open(os.path.join(host.workdir, "CLAUDE.md"), "w") as f:
        f.write("work\n")
    return host


async def _flags(mongo_db, ctx, **fields):
    await mongo_db["test_processes"].update_one({"_id": ctx._process_oid}, {"$set": fields})


@pytest.mark.asyncio
async def test_pending_capture_record_roundtrip(mongo_db):
    s, w = ObjectId(), ObjectId()
    await PC.record_pending_capture(mongo_db, "test", process_id="p", session_blob_id=s, workdir_blob_id=w)
    rec = await PC.load_pending_capture(mongo_db, "test", "p")
    assert rec["sessionBlobId"] == s and rec["workdirBlobId"] == w
    await PC.delete_pending_capture(mongo_db, "test", "p")
    assert await PC.load_pending_capture(mongo_db, "test", "p") is None


@pytest.mark.asyncio
async def test_store_workdir_snapshot_records_then_clears(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)
    seen = {}
    real_stream = S._stream_archive_to_blob

    async def _spy(ctx_, host_, exclude, wwriter, **kw):
        seen["pending"] = await PC.load_pending_capture(mongo_db, "test", ctx.process_id)
        return await real_stream(ctx_, host_, exclude, wwriter, **kw)
    monkeypatch.setattr(S, "_stream_archive_to_blob", _spy)

    session_id = ObjectId()
    await S._store_workdir_snapshot(ctx, host, end_state="cancelled", workdir_exclude=[".env"], session_blob_id=session_id)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == session_id
    assert seen["pending"]["workdirBlobId"] == snap["workdirBlobId"]
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True
    assert doc["hasUnsavedWork"] is False


@pytest.mark.asyncio
async def test_interrupted_workdir_store_leaves_pending_record(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)

    async def _boom(*a, **kw):
        raise RuntimeError("cut off")
    monkeypatch.setattr(S, "_stream_archive_to_blob", _boom)
    with pytest.raises(RuntimeError):
        await S._store_workdir_snapshot(ctx, host, end_state="cancelled", workdir_exclude=None, session_blob_id=ObjectId())
    rec = await PC.load_pending_capture(mongo_db, "test", ctx.process_id)
    assert rec is not None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasUnsavedWork"] is True


@pytest.mark.asyncio
async def test_credentials_guard_clears_unsaved_work(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)  # no home/.claude/.credentials.json
    await S._capture_snapshot(ctx, host, end_state="done", workdir_exclude=None)
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasUnsavedWork"] is False


class _Cfg:
    def __init__(self, supports_resume):
        self.supports_resume = supports_resume


@pytest.mark.asyncio
async def test_on_agent_live_marks_only_resumable_tasks(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResurrect=True)
    await S._on_agent_live(ctx, _Cfg(False))
    assert "hasUnsavedWork" not in await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    await S._on_agent_live(ctx, _Cfg(True))
    assert (await mongo_db["test_processes"].find_one({"_id": ctx._process_oid}))["hasUnsavedWork"] is True


class _CleanupHost:
    def __init__(self):
        self.calls = []
        self.taskdir = "/t"

    async def cleanup_taskdir(self, aggressive):
        self.calls.append(aggressive)


@pytest.mark.asyncio
async def test_cleanup_after_capture_keeps_taskdir_when_capture_failed():
    h = _CleanupHost()
    await S._cleanup_after_capture(h, capture_failed=True, cancelled=False)
    assert h.calls == []
    await S._cleanup_after_capture(h, capture_failed=False, cancelled=True)
    assert h.calls == [True]
