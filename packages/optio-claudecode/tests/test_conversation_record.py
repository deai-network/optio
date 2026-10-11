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
from optio_claudecode import pending_captures as PC
from optio_claudecode.snapshots import (
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES, yield_workdir_archive
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
    os.makedirs(os.path.join(host.workdir, "sub"))
    with open(os.path.join(host.workdir, "sub", S._CONV_BUFFER_FILE), "w") as f:
        f.write("[]")

    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=workdir_exclude,
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    names = await _tar_names(mongo_db, snap["workdirBlobId"])
    assert "keep.txt" in names
    assert "sub" in names
    assert S._CONV_BUFFER_FILE not in names
    assert f"sub/{S._CONV_BUFFER_FILE}" not in names


@pytest.mark.asyncio
async def test_fallback_archive_drops_buffer_file(tmp_path):
    """The in-process tarfile fallback (a host without tar or bash) honours
    the same exclude, at the top level and below it."""
    root = tmp_path / "wd"
    (root / "sub").mkdir(parents=True)
    (root / "keep.txt").write_text("work\n")
    (root / S._CONV_BUFFER_FILE).write_text("[]")
    (root / "sub" / S._CONV_BUFFER_FILE).write_text("[]")

    data = b"".join([
        chunk async for chunk in yield_workdir_archive(
            str(root), exclude=S._effective_workdir_excludes([]),
        )
    ])

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert "keep.txt" in names
    assert S._CONV_BUFFER_FILE not in names
    assert f"sub/{S._CONV_BUFFER_FILE}" not in names


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


# --- pending-capture tracking ----------------------------------------------------


async def _cut_off_capture(ctx, host, monkeypatch) -> ObjectId:
    """Run a capture with a conversation whose workdir stream is cut off;
    return the id of the conversation blob it stored."""
    real_stream = S._stream_archive_to_blob

    async def _cut_off(*a, **kw):
        raise RuntimeError("cut off")
    monkeypatch.setattr(S, "_stream_archive_to_blob", _cut_off)
    with pytest.raises(RuntimeError, match="cut off"):
        await S._capture_snapshot(
            ctx, host, end_state="cancelled", workdir_exclude=None,
            session_blob_encrypt=_encrypt, conversation=_BUFFER,
        )
    monkeypatch.setattr(S, "_stream_archive_to_blob", real_stream)
    files = await _conversation_files(ctx._db)
    assert len(files) == 1
    return files[0]["_id"]


async def _blob_exists(mongo_db, blob_id) -> bool:
    return await mongo_db["fs.files"].count_documents({"_id": blob_id}) == 1


@pytest.mark.asyncio
async def test_cut_off_capture_leaves_pending_record_naming_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    conv_id = await _cut_off_capture(ctx, _local_host(tmp_path), monkeypatch)

    record = await PC.load_pending_capture(mongo_db, "test", ctx.process_id)
    assert record["conversationBlobId"] == conv_id
    assert PC.pending_conversation_blob_id(record) == conv_id
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None


@pytest.mark.asyncio
async def test_pending_record_without_conversation_names_none(mongo_db):
    await PC.record_pending_capture(
        mongo_db, "test", process_id="p", session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
    )
    record = await PC.load_pending_capture(mongo_db, "test", "p")
    assert "conversationBlobId" not in record
    assert PC.pending_conversation_blob_id(record) is None
    assert PC.pending_conversation_blob_id(None) is None


@pytest.mark.asyncio
async def test_launch_settle_deletes_an_uncommitted_captures_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    conv_id = await _cut_off_capture(ctx, _local_host(tmp_path), monkeypatch)

    assert await PC.settle_pending_capture(ctx, discard_session_blob=True) is False

    assert not await _blob_exists(mongo_db, conv_id)
    assert await mongo_db["fs.chunks"].count_documents({"files_id": conv_id}) == 0
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None


@pytest.mark.asyncio
async def test_hook_settle_keeps_an_uncommitted_captures_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """The Resurrect hook settles without discard_session_blob: the blobs of
    the cut-off capture stay for its fallback, like the session blob."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    conv_id = await _cut_off_capture(ctx, _local_host(tmp_path), monkeypatch)

    assert await PC.settle_pending_capture(ctx) is False

    assert await _blob_exists(mongo_db, conv_id)


@pytest.mark.asyncio
async def test_launch_settle_keeps_a_committed_captures_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures):
    """Cut off between inserting the snapshot record and deleting the pending
    one: the conversation blob is that snapshot's and stays."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    await S._capture_snapshot(
        ctx, _local_host(tmp_path), end_state="done", workdir_exclude=None,
        session_blob_encrypt=_encrypt, conversation=_BUFFER,
    )
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    await PC.record_pending_capture(
        mongo_db, "test", process_id=ctx.process_id,
        session_blob_id=snap["sessionBlobId"], workdir_blob_id=snap["workdirBlobId"],
        conversation_blob_id=snap["conversationBlobId"],
    )

    assert await PC.settle_pending_capture(ctx, discard_session_blob=True) is True

    assert await _blob_exists(mongo_db, snap["conversationBlobId"])
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None


@pytest.mark.asyncio
async def test_next_capture_discards_a_stale_records_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """A cut-off capture followed by a Resume/Restart that did not settle
    (a stale record): the next capture deletes the unreferenced conversation
    blob the record names, and records its own."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    stale_conv = await _cut_off_capture(ctx, host, monkeypatch)

    _plant_credentials(host)
    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None,
        session_blob_encrypt=_encrypt, conversation=b"[[2,{}]]",
    )

    assert not await _blob_exists(mongo_db, stale_conv)
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["conversationBlobId"] != stale_conv
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == b"E[[2,{}]]"
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None


@pytest.mark.asyncio
async def test_stale_record_discard_keeps_a_referenced_or_current_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    await S._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None, conversation=_BUFFER,
    )
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    referenced = snap["conversationBlobId"]

    # A stale record naming the committed snapshot's conversation blob: kept.
    await PC.record_pending_capture(
        mongo_db, "test", process_id=ctx.process_id,
        session_blob_id=snap["sessionBlobId"], workdir_blob_id=snap["workdirBlobId"],
        conversation_blob_id=referenced,
    )
    await S._store_workdir_snapshot(
        ctx, host, end_state="done", workdir_exclude=None, session_blob_id=ObjectId(),
    )
    assert await _blob_exists(mongo_db, referenced)

    # A stale record naming the blob this very capture stores: kept too.
    async with ctx.store_blob("conversation") as w:
        await w.write(b"[]")
    current = w.file_id
    await PC.record_pending_capture(
        mongo_db, "test", process_id=ctx.process_id,
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(), conversation_blob_id=current,
    )
    await S._store_workdir_snapshot(
        ctx, host, end_state="done", workdir_exclude=None, session_blob_id=ObjectId(),
        conversation_blob_id=current,
    )
    assert await _blob_exists(mongo_db, current)


@pytest.mark.asyncio
async def test_conversation_blob_short_write_raises(ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures

    class _ShortWriter:
        file_id = ObjectId()
        _position = 0

        async def write(self, b):
            self._position = len(b) - 1

    class _Cm:
        async def __aenter__(self):
            return _ShortWriter()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ctx, "store_blob", lambda name, file_id=None: _Cm())
    with pytest.raises(RuntimeError, match="conversation blob short-write"):
        await S._store_conversation_blob(ctx, b"abc", encrypt=None)


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
