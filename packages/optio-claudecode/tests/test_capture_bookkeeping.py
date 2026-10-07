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
async def test_store_workdir_snapshot_discards_stale_pending_blob(mongo_db, tmp_path, ctx_and_captures):
    """A cut-off capture followed by Resume/Restart instead of Resurrect
    leaves a stale pending record; the next capture deletes its partial
    workdir blob (referenced by no snapshot) before recording its own."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=ObjectId(), workdir_blob_id=partial)

    session_id = ObjectId()
    await S._store_workdir_snapshot(ctx, host, end_state="done", workdir_exclude=None, session_blob_id=session_id)

    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 0
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == session_id
    assert snap["workdirBlobId"] != partial
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None


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


class _StopAfterSettle(Exception):
    pass


async def _start_session_until_rescue(ctx, host, monkeypatch):
    """Run run_claudecode_session up to crash-orphan rescue (which still sees
    the workdir) and stop there."""
    async def _stop(*a, **kw):
        raise _StopAfterSettle()
    monkeypatch.setattr(S, "_build_host", lambda config, process_id: host)
    monkeypatch.setattr(S, "_rescue_orphan_if_present", _stop)
    from optio_claudecode import ClaudeCodeTaskConfig
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, supports_resume=True)
    with pytest.raises(_StopAfterSettle):
        await S.run_claudecode_session(ctx, cfg)


async def _blob(ctx, name, payload):
    async with ctx.store_blob(name) as w:
        await w.write(payload)
    return w.file_id


@pytest.mark.asyncio
async def test_launch_settles_a_committed_pending_capture(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """A capture cut off between insert_snapshot and delete_pending_capture,
    then a Resume/Restart: the record must not outlive the launch (a later
    Resurrect would trust it); its blob is the snapshot's and stays."""
    from optio_claudecode.snapshots import insert_snapshot
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True)
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state="cancelled",
                          session_blob_id=s, workdir_blob_id=w, deliverables_emitted=[])
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=s, workdir_blob_id=w)

    await _start_session_until_rescue(ctx, _local_host(tmp_path), monkeypatch)

    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    assert await mongo_db["fs.files"].count_documents({"_id": w}) == 1
    assert await mongo_db["fs.chunks"].count_documents({"files_id": w}) == 1
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True


@pytest.mark.asyncio
async def test_launch_settles_an_uncommitted_pending_capture(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True)
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=ObjectId(), workdir_blob_id=partial)

    await _start_session_until_rescue(ctx, _local_host(tmp_path), monkeypatch)

    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 0
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert "hasSavedState" not in doc


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
