"""The conversation record: the listener's replay buffer is stored at capture
as its own GridFS blob (``conversation``, through ``session_blob_encrypt``)
referenced by the snapshot's ``conversationBlobId``, never as a workdir file
(the workdir blob is plaintext). Spec:
docs/2026-10-11-conversation-buffer-encryption-design.md, sections 1, 2, 4."""

from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import tarfile
import time as _time

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

import optio_claudecode.session as S
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task
from optio_claudecode.snapshots import (
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
from optio_host.host import LocalHost


_BUFFER = b'[[1,{"type":"user"}]]'


def _encrypt(b: bytes) -> bytes:
    return b"E" + b


def _local_host(tmp_path, *, credentials: bool = True) -> LocalHost:
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)
    with open(os.path.join(host.workdir, "keep.txt"), "w") as f:
        f.write("work\n")
    if credentials:
        _plant_credentials(host)
    return host


def _plant_credentials(host: LocalHost) -> None:
    """The capture's credentials guard needs them; the capture removes
    home/.claude, so a repeated capture plants them again."""
    os.makedirs(os.path.join(host.workdir, "home/.claude"), exist_ok=True)
    with open(os.path.join(host.workdir, "home/.claude/.credentials.json"), "w") as f:
        f.write('{"t": 1}')


async def _flags(mongo_db, ctx) -> None:
    await mongo_db["test_processes"].update_one(
        {"_id": ctx._process_oid},
        {"$set": {"supportsResume": True, "supportsResurrect": True, "hasUnsavedWork": True}},
    )


async def _read_blob(mongo_db, file_id) -> bytes:
    stream = await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(file_id)
    return await stream.read()


async def _conversation_files(mongo_db) -> list[dict]:
    return await mongo_db["fs.files"].find({"filename": "conversation"}).to_list(None)


async def _tar_names(mongo_db, file_id) -> set[str]:
    data = await _read_blob(mongo_db, file_id)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        # CLI tar names members "./x"; the Python fallback names them "x".
        return {m.name[2:] if m.name.startswith("./") else m.name for m in tar.getmembers()}


# --- capture ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_stores_encrypted_conversation_blob(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)

    saved = await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None,
        session_blob_encrypt=_encrypt, conversation=_BUFFER,
    )

    assert saved is True
    files = await _conversation_files(mongo_db)
    assert len(files) == 1
    assert await _read_blob(mongo_db, files[0]["_id"]) == b"E" + _BUFFER
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["conversationBlobId"] == files[0]["_id"]


@pytest.mark.asyncio
async def test_capture_plaintext_when_no_cipher(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)

    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None, conversation=_BUFFER,
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == _BUFFER


@pytest.mark.asyncio
async def test_capture_without_conversation_has_no_field(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)

    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None, session_blob_encrypt=_encrypt,
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap is not None
    assert "conversationBlobId" not in snap
    assert await _conversation_files(mongo_db) == []


@pytest.mark.asyncio
async def test_refused_capture_stores_no_conversation(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path, credentials=False)

    saved = await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None,
        session_blob_encrypt=_encrypt, conversation=_BUFFER,
    )

    assert saved is False
    assert await _conversation_files(mongo_db) == []
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None


# --- the workdir tar never carries the buffer file ----------------------------


def test_effective_excludes_always_has_buffer_file():
    defaults_before = list(DEFAULT_WORKDIR_EXCLUDES)

    assert S._effective_workdir_excludes(None) == [*DEFAULT_WORKDIR_EXCLUDES, S._CONV_BUFFER_FILE]
    assert S._effective_workdir_excludes([]) == [S._CONV_BUFFER_FILE]
    given = ["build"]
    assert S._effective_workdir_excludes(given) == ["build", S._CONV_BUFFER_FILE]
    # Already listed: not added twice.
    assert S._effective_workdir_excludes([S._CONV_BUFFER_FILE, "x"]) == [S._CONV_BUFFER_FILE, "x"]

    # Neither the caller's list nor the shared defaults are mutated.
    assert given == ["build"]
    assert DEFAULT_WORKDIR_EXCLUDES == defaults_before


@pytest.mark.asyncio
@pytest.mark.parametrize("workdir_exclude", [None, [], ["build"]])
async def test_workdir_tar_never_contains_buffer_file(
        mongo_db, tmp_path, ctx_and_captures, workdir_exclude):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    with open(os.path.join(host.workdir, S._CONV_BUFFER_FILE), "w") as f:
        f.write('[[1, {"type": "user", "secret": "s"}]]')

    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=workdir_exclude,
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    names = await _tar_names(mongo_db, snap["workdirBlobId"])
    assert "keep.txt" in names
    assert S._CONV_BUFFER_FILE not in names


@pytest.mark.asyncio
async def test_store_workdir_snapshot_alone_excludes_buffer_file(mongo_db, tmp_path, ctx_and_captures):
    """Resurrect's session-blob fallback calls _store_workdir_snapshot on its
    own: the exclude is enforced there, so that tar lacks the file too."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path, credentials=False)
    with open(os.path.join(host.workdir, S._CONV_BUFFER_FILE), "w") as f:
        f.write("[]")

    await S._store_workdir_snapshot(
        ctx, host, end_state="resurrected",
        workdir_exclude=[".env", ".optio-rescue-pending"], session_blob_id=ObjectId(),
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    names = await _tar_names(mongo_db, snap["workdirBlobId"])
    assert "keep.txt" in names
    assert S._CONV_BUFFER_FILE not in names
    assert "conversationBlobId" not in snap


# --- snapshot records and pruning ----------------------------------------------


@pytest.mark.asyncio
async def test_insert_snapshot_stores_conversation_blob_id_only_when_given(mongo_db):
    conv = ObjectId()
    with_conv = await insert_snapshot(
        mongo_db, prefix="opt", process_id="pa", end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
        deliverables_emitted=[], conversation_blob_id=conv,
    )
    without = await insert_snapshot(
        mongo_db, prefix="opt", process_id="pb", end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
        deliverables_emitted=[],
    )
    assert with_conv["conversationBlobId"] == conv
    assert "conversationBlobId" not in without
    stored = await load_latest_snapshot(mongo_db, prefix="opt", process_id="pb")
    assert "conversationBlobId" not in stored


@pytest.mark.asyncio
async def test_prune_snapshots_returns_conversation_blob_id(mongo_db):
    first_conv = ObjectId()
    await insert_snapshot(
        mongo_db, prefix="opt", process_id="pc", end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
        deliverables_emitted=[], conversation_blob_id=first_conv,
    )
    for _ in range(SNAPSHOT_RETENTION):
        await asyncio.sleep(0.002)
        await insert_snapshot(
            mongo_db, prefix="opt", process_id="pc", end_state="done",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id="pc")
    assert len(pruned) == 1
    assert pruned[0]["conversationBlobId"] == first_conv


@pytest.mark.asyncio
async def test_prune_deletes_conversation_blob(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    conversation_ids = []
    for i in range(SNAPSHOT_RETENTION + 1):
        _plant_credentials(host)
        await S._capture_snapshot(
            ctx, host, end_state="done", workdir_exclude=None,
            session_blob_encrypt=_encrypt, conversation=b"[[%d,{}]]" % i,
        )
        snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
        conversation_ids.append(snap["conversationBlobId"])
        await asyncio.sleep(0.002)

    oldest, kept = conversation_ids[0], conversation_ids[1:]
    assert await mongo_db["fs.files"].count_documents({"_id": oldest}) == 0
    assert await mongo_db["fs.chunks"].count_documents({"files_id": oldest}) == 0
    for blob_id in kept:
        assert await mongo_db["fs.files"].count_documents({"_id": blob_id}) == 1


# --- the task factory ----------------------------------------------------------


def test_task_factory_sets_session_crypto():
    def enc(b: bytes) -> bytes:
        return b

    def dec(b: bytes) -> bytes:
        return b

    task = create_claudecode_task(
        "p", "P", ClaudeCodeTaskConfig(
            consumer_instructions="x", fs_isolation=False,
            session_blob_encrypt=enc, session_blob_decrypt=dec,
        ),
    )
    assert task.session_crypto.encrypt is enc
    assert task.session_crypto.decrypt is dec

    plain = create_claudecode_task(
        "q", "Q", ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False),
    )
    assert plain.session_crypto is not None
    assert plain.session_crypto.encrypt is None
    assert plain.session_crypto.decrypt is None


# --- the teardown (real engine, fake claude) -----------------------------------


_TERMINAL = {"done", "failed", "cancelled"}


async def _wait_terminal(optio, process_id: str, timeout: float = 60.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if (proc is not None and proc["status"]["state"] in _TERMINAL
                and proc["_id"] not in optio._executor._running_tasks):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_widget_data(optio, process_id: str, timeout: float = 60.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


@pytest.mark.serial
@pytest.mark.asyncio
async def test_teardown_does_not_write_buffer_to_host(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    monkeypatch,
):
    """A conversation-mode teardown keeps the listener's export in memory: no
    buffer file in the host workdir when the capture runs, and the export
    reaches the capture, which stores it as the snapshot's conversation blob."""
    from optio_core.lifecycle import Optio

    seen: dict = {}
    real_capture = S._capture_snapshot

    async def _spy(ctx, host, **kw):
        seen["file_on_host"] = os.path.exists(os.path.join(host.workdir, S._CONV_BUFFER_FILE))
        seen["conversation"] = kw.get("conversation")
        return await real_capture(ctx, host, **kw)
    monkeypatch.setattr(S, "_capture_snapshot", _spy)

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="ccrec")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-record", name="Conversation record",
            config=ClaudeCodeTaskConfig(
                consumer_instructions="Converse with the test.",
                mode="conversation",
                conversation_ui=True,
                permission_mode="bypassPermissions",
                fs_isolation=False,
                install_dir=str(claude_cache_dir),
                ttyd_install_dir=str(shim_install_dir),
                supports_resume=True,
                credentials_json={"token": "test"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-record", session_id=None, timeout=60)
        await _wait_widget_data(optio, "cc-conv-record")
        # Subscribed after the listener, so it sees the turn's result only
        # once the listener has buffered it.
        result_seen = asyncio.Event()
        conv.on_event(lambda e: result_seen.set() if e.get("type") == "result" else None)
        await conv.send("ping")
        await asyncio.wait_for(result_seen.wait(), 60)
        await conv.close()
        await _wait_terminal(optio, "cc-conv-record")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert seen["file_on_host"] is False
    exported = json.loads(seen["conversation"])
    assert isinstance(exported, list) and exported
    assert all(isinstance(e, list) and len(e) == 2 and isinstance(e[1], dict) for e in exported)
    assert any(e[1].get("type") == "result" for e in exported)
    snap = await load_latest_snapshot(mongo_db, prefix="ccrec", process_id="cc-conv-record")
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == seen["conversation"]
    assert S._CONV_BUFFER_FILE not in await _tar_names(mongo_db, snap["workdirBlobId"])
