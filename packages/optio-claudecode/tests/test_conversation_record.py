"""The conversation record: the listener's replay buffer is stored at capture
as its own GridFS blob (``conversation``, through ``session_blob_encrypt``)
referenced by the snapshot's ``conversationBlobId``, never as a workdir file
(the workdir blob is plaintext). A resume primes the listener from that
record (decrypted with ``session_blob_decrypt``), and the crash-rescue and
Resurrect captures carry it forward. Spec:
docs/2026-10-11-conversation-buffer-encryption-design.md, sections 1-4."""

from __future__ import annotations

import asyncio
import io
import json
import logging
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
from optio_claudecode import resurrect as R
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


# --- resume: the listener is primed from the record ---------------------------------


def _decrypt(b: bytes) -> bytes:
    assert b.startswith(b"E")
    return b[1:]


async def _snapshot_with_record(mongo_db, ctx, payload: bytes | None) -> ObjectId | None:
    """A snapshot an hour old whose conversation blob holds ``payload`` as
    stored; returns its id. Blobs stored by the test, before or after it, are
    newer, however slowly a starved CPU runs the test."""
    from datetime import timedelta
    conv_id = None
    if payload is not None:
        async with ctx.store_blob("conversation") as w:
            await w.write(payload)
        conv_id = w.file_id
    doc = await insert_snapshot(
        mongo_db, prefix="test", process_id=ctx.process_id, end_state="cancelled",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(), deliverables_emitted=[],
        conversation_blob_id=conv_id,
    )
    await mongo_db["test_claudecode_session_snapshots"].update_one(
        {"_id": doc["_id"]}, {"$set": {"capturedAt": doc["capturedAt"] - timedelta(hours=1)}},
    )
    return conv_id


_ENTRIES = [
    [1, {"type": "user", "message": {"content": "hi"}}],
    [2, {"type": "result", "result": "hello"}],
    [3, {"type": "x-optio-closed"}],
]


@pytest.mark.asyncio
async def test_resume_primes_from_encrypted_blob(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _snapshot_with_record(mongo_db, ctx, _encrypt(json.dumps(_ENTRIES).encode()))

    out = await S._load_conversation_buffer(ctx, decrypt=_decrypt)

    # The close marker is dropped: replayed, it would close the resumed session.
    assert out == [(1, _ENTRIES[0][1]), (2, _ENTRIES[1][1])]


@pytest.mark.asyncio
async def test_resume_primes_from_plaintext_blob_without_cipher(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _snapshot_with_record(mongo_db, ctx, json.dumps(_ENTRIES[:2]).encode())

    assert await S._load_conversation_buffer(ctx, decrypt=None) == [
        (1, _ENTRIES[0][1]), (2, _ENTRIES[1][1]),
    ]


@pytest.mark.asyncio
async def test_resume_without_record_is_empty(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    # No snapshot at all.
    assert await S._load_conversation_buffer(ctx, decrypt=_decrypt) is None
    # A snapshot without a conversation record (iframe mode, or pre-fix).
    await _snapshot_with_record(mongo_db, ctx, None)
    assert await S._load_conversation_buffer(ctx, decrypt=_decrypt) is None


@pytest.mark.asyncio
async def test_resume_with_undecryptable_or_corrupt_blob_is_empty(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures

    def _raises(_b: bytes) -> bytes:
        raise ValueError("bad key")

    cases = [
        (_encrypt(json.dumps(_ENTRIES).encode()), _raises),  # undecryptable
        (_encrypt(b"not json"), _decrypt),                    # not JSON
        (_encrypt(b"\xff\xfe\x00"), _decrypt),                # not UTF-8
        (_encrypt(b'{"a": 1}'), _decrypt),                    # not a list
        (_encrypt(b'[["x", {}], [1, "y"], [2]]'), _decrypt),  # no valid entry
    ]
    for payload, decrypt in cases:
        await mongo_db["test_claudecode_session_snapshots"].delete_many({})
        await _snapshot_with_record(mongo_db, ctx, payload)
        assert await S._load_conversation_buffer(ctx, decrypt=decrypt) is None, payload

    # A record naming a blob that is gone.
    await mongo_db["test_claudecode_session_snapshots"].delete_many({})
    conv_id = await _snapshot_with_record(mongo_db, ctx, b"E[]")
    await ctx.delete_blob(conv_id)
    assert await S._load_conversation_buffer(ctx, decrypt=_decrypt) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [
    b'{"a": "secret-content"}',                          # not a list
    b'[["x", {}], [1, "secret-content"], [2]]',          # no valid entry
], ids=["not-a-list", "no-valid-entry"])
async def test_malformed_record_logs_one_warning(mongo_db, ctx_and_captures, caplog, record):
    """Valid JSON that is not a buffer: one warning naming the process, never
    the record's content."""
    ctx, _cap, _flag = ctx_and_captures
    await _snapshot_with_record(mongo_db, ctx, _encrypt(record))

    with caplog.at_level(logging.WARNING, logger=S.__name__):
        assert await S._load_conversation_buffer(ctx, decrypt=_decrypt) is None

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert warnings[0].name == S.__name__
    message = warnings[0].getMessage()
    assert ctx.process_id in message
    assert "secret-content" not in message and not warnings[0].exc_info


@pytest.mark.asyncio
async def test_empty_record_is_empty_without_a_warning(mongo_db, ctx_and_captures, caplog):
    """`[]` (or only the close marker) is a buffer with nothing in it, not a
    malformed one."""
    ctx, _cap, _flag = ctx_and_captures
    for record in (b"[]", b'[[1, {"type": "x-optio-closed"}]]'):
        await mongo_db["test_claudecode_session_snapshots"].delete_many({})
        await _snapshot_with_record(mongo_db, ctx, _encrypt(record))
        with caplog.at_level(logging.WARNING, logger=S.__name__):
            assert await S._load_conversation_buffer(ctx, decrypt=_decrypt) is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.asyncio
async def test_latest_conversation_bytes_are_the_stored_bytes(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    assert await S._latest_conversation_bytes(ctx) is None
    await _snapshot_with_record(mongo_db, ctx, None)
    assert await S._latest_conversation_bytes(ctx) is None
    await mongo_db["test_claudecode_session_snapshots"].delete_many({})
    await _snapshot_with_record(mongo_db, ctx, b"E" + _BUFFER)
    # Still encrypted: no decrypt here.
    assert await S._latest_conversation_bytes(ctx) == b"E" + _BUFFER


@pytest.mark.asyncio
async def test_restore_deletes_stale_buffer_file(mongo_db, tmp_path, ctx_and_captures):
    """A workdir blob of a snapshot taken before the fix still holds the file;
    the restore step deletes it (the record is the conversation blob now)."""
    ctx, _cap, _flag = ctx_and_captures
    host = _local_host(tmp_path, credentials=False)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in ((S._CONV_BUFFER_FILE, b"[[1, {}]]"), ("restored.txt", b"work\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    async with ctx.store_blob("workdir") as w:
        await w.write(buf.getvalue())

    await S._restore_workdir_snapshot(ctx, host, w.file_id)

    assert os.path.exists(os.path.join(host.workdir, "restored.txt"))
    assert not os.path.exists(os.path.join(host.workdir, S._CONV_BUFFER_FILE))


# --- crash rescue and Resurrect carry the record forward ------------------------------


_OLD_RECORD = b'E[[1,{"type":"user","message":"older"}]]'


def _cc_config(**kw) -> ClaudeCodeTaskConfig:
    return ClaudeCodeTaskConfig(
        consumer_instructions="(conversation record test)", fs_isolation=False,
        supports_resume=True, session_blob_encrypt=_encrypt, session_blob_decrypt=_decrypt,
        **kw,
    )


def _no_tmux(monkeypatch) -> None:
    """No tmux on the worker (conversation mode): no session tree to stop."""
    async def _none(host):
        return None
    monkeypatch.setattr(S.host_actions, "find_tmux", _none)


async def _rescue(ctx, host, monkeypatch) -> None:
    """A Resume finding a leftover rescue marker: the crash-orphan rescue."""
    _no_tmux(monkeypatch)
    ctx.resume = True
    with open(os.path.join(host.workdir, S._RESCUE_MARKER), "w") as f:
        f.write("")
    await S._rescue_orphan_if_present(ctx, host, _cc_config())


@pytest.mark.asyncio
async def test_rescue_capture_carries_record_forward(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    prev_conv = await _snapshot_with_record(mongo_db, ctx, _OLD_RECORD)
    host = _local_host(tmp_path)

    await _rescue(ctx, host, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "rescued"
    assert snap["conversationBlobId"] != prev_conv
    # The stored bytes as they are: not decrypted, not encrypted again.
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == _OLD_RECORD
    # The previous snapshot keeps its own blob (pruning deletes per snapshot).
    assert await _read_blob(mongo_db, prev_conv) == _OLD_RECORD


@pytest.mark.asyncio
async def test_rescue_capture_without_a_previous_record_has_none(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    await _snapshot_with_record(mongo_db, ctx, None)

    await _rescue(ctx, _local_host(tmp_path), monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "rescued"
    assert "conversationBlobId" not in snap
    assert await _conversation_files(mongo_db) == []


@pytest.mark.asyncio
async def test_refused_rescue_capture_leaves_no_copy(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """The credentials guard refuses the rescue capture: nothing is stored,
    the carried-forward copy included."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    prev_conv = await _snapshot_with_record(mongo_db, ctx, _OLD_RECORD)

    await _rescue(ctx, _local_host(tmp_path, credentials=False), monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["conversationBlobId"] == prev_conv
    assert [f["_id"] for f in await _conversation_files(mongo_db)] == [prev_conv]


async def _failed_run_workdir(ctx, host, path: str) -> None:
    """What a failed run left on the host, by Resurrect branch: credentials
    (crash while running), or none and a session blob its cut-off capture
    stored (the session-blob fallback)."""
    if path == "credentials":
        _plant_credentials(host)
    else:
        async with ctx.store_blob("session") as w:
            await w.write(b"session")


async def _resurrect(ctx, host, monkeypatch) -> None:
    monkeypatch.setattr(S, "_build_host", lambda config, process_id: host)
    _no_tmux(monkeypatch)
    await R.resurrect_claudecode_session(ctx, _cc_config())


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["credentials", "fallback"])
async def test_resurrect_carries_record_forward(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch, path):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    prev_conv = await _snapshot_with_record(mongo_db, ctx, _OLD_RECORD)
    host = _local_host(tmp_path, credentials=False)
    await _failed_run_workdir(ctx, host, path)

    await _resurrect(ctx, host, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert snap["conversationBlobId"] != prev_conv
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == _OLD_RECORD
    assert await _read_blob(mongo_db, prev_conv) == _OLD_RECORD


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["credentials", "fallback"])
async def test_resurrect_capture_excludes_stale_buffer_file(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch, path):
    """The failed run's workdir was restored from a pre-fix snapshot and still
    holds the file: the resurrected workdir blob leaves it out."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path, credentials=False)
    with open(os.path.join(host.workdir, S._CONV_BUFFER_FILE), "w") as f:
        f.write('[[1, {"type": "user", "secret": "s"}]]')
    await _failed_run_workdir(ctx, host, path)

    await _resurrect(ctx, host, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    names = await _tar_names(mongo_db, snap["workdirBlobId"])
    assert "keep.txt" in names
    assert S._CONV_BUFFER_FILE not in names


@pytest.mark.asyncio
async def test_resurrect_adopts_the_cut_off_captures_conversation_blob(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """The failed run's own capture was cut off while streaming the workdir:
    its conversation blob holds the newest history, so the resurrected
    snapshot references that blob (not a copy of the older record), and no
    settle deletes it."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    pending_conv = await _cut_off_capture(ctx, host, monkeypatch)
    prev_conv = await _snapshot_with_record(mongo_db, ctx, _OLD_RECORD)

    await _resurrect(ctx, host, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert snap["conversationBlobId"] == pending_conv
    assert await _read_blob(mongo_db, pending_conv) == b"E" + _BUFFER
    # No copy was made; the older record stays with its snapshot.
    assert {f["_id"] for f in await _conversation_files(mongo_db)} == {pending_conv, prev_conv}
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None

    # The next launch settles nothing away.
    await PC.settle_pending_capture(ctx, discard_session_blob=True)
    assert await _blob_exists(mongo_db, pending_conv)


@pytest.mark.asyncio
async def test_resurrect_after_a_committed_capture_copies_its_record(
        mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    """The pending record is of a capture that committed (its snapshot owns
    the conversation blob) and the workdir holds newer work: the new snapshot
    gets a copy, never a second reference (pruning one would empty the other)."""
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx)
    host = _local_host(tmp_path)
    await S._capture_snapshot(
        ctx, host, end_state="cancelled", workdir_exclude=None,
        session_blob_encrypt=_encrypt, conversation=_BUFFER,
    )
    committed = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    await PC.record_pending_capture(
        mongo_db, "test", process_id=ctx.process_id,
        session_blob_id=committed["sessionBlobId"], workdir_blob_id=committed["workdirBlobId"],
        conversation_blob_id=committed["conversationBlobId"],
    )
    await asyncio.sleep(0.002)  # distinct capturedAt (millisecond resolution)
    _plant_credentials(host)

    await _resurrect(ctx, host, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert snap["conversationBlobId"] != committed["conversationBlobId"]
    assert await _read_blob(mongo_db, snap["conversationBlobId"]) == b"E" + _BUFFER
    assert await _blob_exists(mongo_db, committed["conversationBlobId"])


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


def _xor(b: bytes) -> bytes:
    return bytes(x ^ 0x5A for x in b)


async def _wait_for(pred, what: str, timeout: float = 60.0) -> None:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what} did not happen in {timeout}s")


@pytest.mark.serial
@pytest.mark.asyncio
async def test_resume_primes_the_listener_from_the_encrypted_record(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    monkeypatch,
):
    """A conversation is suspended (its record stored encrypted) and resumed:
    the resumed run's listener starts with the suspended run's history,
    read from the record through ``session_blob_decrypt``."""
    from optio_core.lifecycle import Optio

    primed: list = []
    real_listener = S.ConversationListener

    def _spy(*a, **kw):
        primed.append(kw.get("initial_events"))
        return real_listener(*a, **kw)
    monkeypatch.setattr(S, "ConversationListener", _spy)

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="ccprime")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-prime", name="Conversation priming",
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
                session_blob_encrypt=_xor,
                session_blob_decrypt=_xor,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-prime", session_id=None, timeout=60)
        await _wait_widget_data(optio, "cc-conv-prime")
        result_seen = asyncio.Event()
        conv.on_event(lambda e: result_seen.set() if e.get("type") == "result" else None)
        await conv.send("ping")
        await asyncio.wait_for(result_seen.wait(), 60)
        await conv.close()
        await _wait_terminal(optio, "cc-conv-prime")

        snap = await load_latest_snapshot(mongo_db, prefix="ccprime", process_id="cc-conv-prime")
        stored = await _read_blob(mongo_db, snap["conversationBlobId"])
        exported = json.loads(_xor(stored))
        assert any(e[1].get("type") == "result" for e in exported)

        conv2 = await optio.launch_and_await_result(
            "cc-conv-prime", resume=True, session_id=None, timeout=60,
        )
        # The listener is built after the result is published.
        await _wait_for(lambda: len(primed) == 2, "the resumed run's listener")
        await conv2.close()
        await _wait_terminal(optio, "cc-conv-prime")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert primed[0] is None  # the fresh launch
    assert primed[1] == [(seq, event) for seq, event in exported]


@pytest.mark.serial
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["export-raises", "listener-never-started"])
async def test_resumed_run_without_an_export_keeps_the_record(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    monkeypatch,
    failure,
):
    """A resumed run whose capture has no listener export (the export raised,
    or the run failed after claude came up but before the listener started)
    carries the latest record forward, the stored bytes as they are, instead
    of leaving the new snapshot without a conversation record."""
    from optio_core.lifecycle import Optio

    prefix, pid = "cckeep", f"cc-conv-keep-{failure}"
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    try:
        task = create_claudecode_task(
            process_id=pid, name="Conversation record kept",
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
                session_blob_encrypt=_xor,
                session_blob_decrypt=_xor,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        result_seen = asyncio.Event()
        conv.on_event(lambda e: result_seen.set() if e.get("type") == "result" else None)
        await conv.send("ping")
        await asyncio.wait_for(result_seen.wait(), 60)
        await conv.close()
        await _wait_terminal(optio, pid)
        first = await load_latest_snapshot(mongo_db, prefix=prefix, process_id=pid)
        stored = await _read_blob(mongo_db, first["conversationBlobId"])

        if failure == "export-raises":
            def _export_raises(self):
                raise RuntimeError("export broke")
            monkeypatch.setattr(S.ConversationListener, "export_buffer", _export_raises)
            conv2 = await optio.launch_and_await_result(
                pid, resume=True, session_id=None, timeout=60,
            )
            await conv2.close()
        else:
            async def _fails(ctx, config):
                raise RuntimeError("failed after claude came up")
            monkeypatch.setattr(S, "_on_agent_live", _fails)
            await asyncio.wait_for(
                optio.launch_and_wait(pid, resume=True, session_id=None), 120,
            )
            proc = await optio.get_process(pid)
            assert proc["status"]["state"] == "failed"
            assert "failed after claude came up" in proc["status"]["error"]
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    snaps = await mongo_db[f"{prefix}_claudecode_session_snapshots"].find(
        {"processId": pid},
    ).sort("capturedAt", -1).to_list(None)
    assert len(snaps) == 2
    latest = snaps[0]
    assert latest["_id"] != first["_id"]
    # A copy (pruning deletes per snapshot), of the stored bytes as they are.
    assert latest["conversationBlobId"] != first["conversationBlobId"]
    assert await _read_blob(mongo_db, latest["conversationBlobId"]) == stored
    assert await _read_blob(mongo_db, first["conversationBlobId"]) == stored
