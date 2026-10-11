"""The deferred migration `claudecode_conversation_buffer_out_of_workdir`: it
moves the conversation buffer that pre-fix captures left inside the plaintext
workdir blob into the snapshot's (encrypted) conversation blob, and rewrites
the workdir blob without it. Spec:
docs/2026-10-11-conversation-buffer-encryption-design.md, section 6.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorGridFSBucket

from optio_core.deferred_migrations import (
    DeferredMigrationContext,
    DeferredMigrationRegistry,
)
from optio_core.models import SessionCrypto, TaskInstance

import optio_claudecode.migrations as M
from optio_claudecode.snapshots import insert_snapshot


_PREFIX = "test"
_NAME = ".optio-conversation-buffer.json"
_BUFFER = b'[[1,{"type":"user","message":"hi"}],[2,{"type":"assistant"}]]'
_MIGRATION = "claudecode_conversation_buffer_out_of_workdir"
_SNAPSHOTS = f"{_PREFIX}_claudecode_session_snapshots"


async def _noop(ctx):
    pass


def _task(pid: str, crypto: SessionCrypto | None) -> TaskInstance:
    return TaskInstance(execute=_noop, process_id=pid, name=pid, session_crypto=crypto)


def _cipher() -> SessionCrypto:
    return SessionCrypto(encrypt=lambda b: b"E" + b, decrypt=lambda b: b[1:])


def _ctx(mongo_db, tasks: dict[str, TaskInstance]) -> DeferredMigrationContext:
    return DeferredMigrationContext(db=mongo_db, prefix=_PREFIX, task_for=tasks.get)


def _workdir_tar(*, buffer: bool) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        entries = [("./keep.txt", b"work\n")]
        if buffer:
            entries.append((f"./{_NAME}", _BUFFER))
        entries.append(("./src/main.py", b"print('x')\n"))
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1_600_000_000
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _put_blob(mongo_db, name: str, data: bytes, owner: str) -> ObjectId:
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    async with bucket.open_upload_stream(
        name, metadata={"processId": owner, "prefix": _PREFIX, "name": name},
    ) as up:
        await up.write(data)
        return up._id


async def _read_blob(mongo_db, file_id) -> bytes:
    stream = await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(file_id)
    return await stream.read()


async def _file_doc(mongo_db, file_id) -> dict | None:
    return await mongo_db["fs.files"].find_one({"_id": file_id})


async def _blob_gone(mongo_db, file_id) -> bool:
    return (
        await mongo_db["fs.files"].count_documents({"_id": file_id}) == 0
        and await mongo_db["fs.chunks"].count_documents({"files_id": file_id}) == 0
    )


def _tar_names(data: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return set(tar.getnames())


async def _process(mongo_db, pid: str) -> ObjectId:
    oid = ObjectId()
    await mongo_db[f"{_PREFIX}_processes"].insert_one({"_id": oid, "processId": pid})
    return oid


async def _snapshot(
    mongo_db, pid: str, *, buffer: bool, owner: str,
    conversation_blob_id: ObjectId | None = None,
) -> dict:
    workdir_id = await _put_blob(mongo_db, "workdir", _workdir_tar(buffer=buffer), owner)
    return await insert_snapshot(
        mongo_db, prefix=_PREFIX, process_id=pid, end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=workdir_id,
        deliverables_emitted=[], conversation_blob_id=conversation_blob_id,
    )


async def _reload(mongo_db, doc) -> dict:
    return await mongo_db[_SNAPSHOTS].find_one({"_id": doc["_id"]})


async def test_migrates_with_task_cipher(mongo_db):
    oid = await _process(mongo_db, "p1")
    snap = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid))

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert await _read_blob(mongo_db, doc["conversationBlobId"]) == b"E" + _BUFFER
    assert doc["workdirBlobId"] != snap["workdirBlobId"]
    names = _tar_names(await _read_blob(mongo_db, doc["workdirBlobId"]))
    assert names == {"./keep.txt", "./src/main.py"}
    assert await _blob_gone(mongo_db, snap["workdirBlobId"])
    for blob_id, name in (
        (doc["conversationBlobId"], "conversation"), (doc["workdirBlobId"], "workdir"),
    ):
        f = await _file_doc(mongo_db, blob_id)
        assert f["filename"] == name
        assert f["metadata"] == {"processId": str(oid), "prefix": _PREFIX, "name": name}


@pytest.mark.parametrize("crypto", [None, SessionCrypto()], ids=["no-crypto", "empty-crypto"])
async def test_plaintext_when_task_has_no_cipher(mongo_db, crypto):
    oid = await _process(mongo_db, "p1")
    snap = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid))

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", crypto)}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert await _read_blob(mongo_db, doc["conversationBlobId"]) == _BUFFER
    assert _NAME not in {
        n.removeprefix("./") for n in _tar_names(await _read_blob(mongo_db, doc["workdirBlobId"]))
    }
    assert await _blob_gone(mongo_db, snap["workdirBlobId"])


async def test_no_task_strips_and_drops(mongo_db):
    # No process document either: the new blob's metadata falls back to the
    # snapshot's processId.
    snap = await _snapshot(mongo_db, "gone", buffer=True, owner="whatever")

    await M.claudecode_conversation_buffer_out_of_workdir(_ctx(mongo_db, {}))

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert "conversationBlobId" not in doc
    assert await mongo_db["fs.files"].count_documents({"filename": "conversation"}) == 0
    names = _tar_names(await _read_blob(mongo_db, doc["workdirBlobId"]))
    assert names == {"./keep.txt", "./src/main.py"}
    assert await _blob_gone(mongo_db, snap["workdirBlobId"])
    f = await _file_doc(mongo_db, doc["workdirBlobId"])
    assert f["metadata"] == {"processId": "gone", "prefix": _PREFIX, "name": "workdir"}


async def test_absent_buffer_marks_only(mongo_db):
    oid = await _process(mongo_db, "p1")
    snap = await _snapshot(mongo_db, "p1", buffer=False, owner=str(oid))
    original = await _read_blob(mongo_db, snap["workdirBlobId"])

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert doc["workdirBlobId"] == snap["workdirBlobId"]
    assert "conversationBlobId" not in doc
    assert await _read_blob(mongo_db, doc["workdirBlobId"]) == original
    files = await mongo_db["fs.files"].find({}).to_list(None)
    assert [f["_id"] for f in files] == [snap["workdirBlobId"]]
    assert await mongo_db["fs.chunks"].count_documents(
        {"files_id": {"$ne": snap["workdirBlobId"]}},
    ) == 0


async def test_new_style_snapshot_marked_only(mongo_db):
    oid = await _process(mongo_db, "p1")
    conv_id = await _put_blob(mongo_db, "conversation", b"E" + _BUFFER, str(oid))
    # Even a workdir blob that (oddly) holds the file is left alone: the
    # snapshot's conversation record is authoritative.
    snap = await _snapshot(
        mongo_db, "p1", buffer=True, owner=str(oid), conversation_blob_id=conv_id,
    )
    workdir_before = await _read_blob(mongo_db, snap["workdirBlobId"])

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert doc["conversationBlobId"] == conv_id
    assert doc["workdirBlobId"] == snap["workdirBlobId"]
    assert await _read_blob(mongo_db, conv_id) == b"E" + _BUFFER
    assert await _read_blob(mongo_db, snap["workdirBlobId"]) == workdir_before
    assert await mongo_db["fs.files"].count_documents({}) == 2


async def test_already_marked_snapshot_is_skipped(mongo_db):
    oid = await _process(mongo_db, "p1")
    snap = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid))
    await mongo_db[_SNAPSHOTS].update_one(
        {"_id": snap["_id"]}, {"$set": {"conversationBufferMigrated": True}},
    )

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["workdirBlobId"] == snap["workdirBlobId"]
    assert "conversationBlobId" not in doc
    assert await mongo_db["fs.files"].count_documents({}) == 1


async def test_missing_workdir_blob_marks_only(mongo_db):
    oid = await _process(mongo_db, "p1")
    snap = await insert_snapshot(
        mongo_db, prefix=_PREFIX, process_id="p1", end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
        deliverables_emitted=[],
    )
    good = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid))

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert doc["workdirBlobId"] == snap["workdirBlobId"]
    assert "conversationBlobId" not in doc
    # A broken snapshot does not stop the others.
    assert (await _reload(mongo_db, good)).get("conversationBlobId") is not None


async def test_corrupt_workdir_blob_marks_only(mongo_db):
    oid = await _process(mongo_db, "p1")
    bad_id = await _put_blob(mongo_db, "workdir", b"not a gzip tar" * 1000, str(oid))
    snap = await insert_snapshot(
        mongo_db, prefix=_PREFIX, process_id="p1", end_state="done",
        session_blob_id=ObjectId(), workdir_blob_id=bad_id,
        deliverables_emitted=[],
    )

    await M.claudecode_conversation_buffer_out_of_workdir(
        _ctx(mongo_db, {"p1": _task("p1", _cipher())}),
    )

    doc = await _reload(mongo_db, snap)
    assert doc["conversationBufferMigrated"] is True
    assert doc["workdirBlobId"] == bad_id
    assert await _read_blob(mongo_db, bad_id) == b"not a gzip tar" * 1000
    files = await mongo_db["fs.files"].find({}).to_list(None)
    assert [f["_id"] for f in files] == [bad_id]
    assert await mongo_db["fs.chunks"].count_documents({"files_id": {"$ne": bad_id}}) == 0


async def test_cipher_failure_fails_and_leaves_no_copy(mongo_db):
    oid = await _process(mongo_db, "p1")
    snap = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid))

    def broken(b: bytes) -> bytes:
        raise RuntimeError("vault unavailable")

    with pytest.raises(RuntimeError, match="vault unavailable"):
        await M.claudecode_conversation_buffer_out_of_workdir(
            _ctx(mongo_db, {"p1": _task("p1", SessionCrypto(encrypt=broken, decrypt=broken))}),
        )

    doc = await _reload(mongo_db, snap)
    assert "conversationBufferMigrated" not in doc
    assert doc["workdirBlobId"] == snap["workdirBlobId"]
    files = await mongo_db["fs.files"].find({}).to_list(None)
    assert [f["_id"] for f in files] == [snap["workdirBlobId"]]
    assert await mongo_db["fs.chunks"].count_documents(
        {"files_id": {"$ne": snap["workdirBlobId"]}},
    ) == 0


async def test_interrupted_run_completes(mongo_db, monkeypatch):
    oid1 = await _process(mongo_db, "p1")
    oid2 = await _process(mongo_db, "p2")
    snap1 = await _snapshot(mongo_db, "p1", buffer=True, owner=str(oid1))
    snap2 = await _snapshot(mongo_db, "p2", buffer=True, owner=str(oid2))
    tasks = {"p1": _task("p1", _cipher()), "p2": _task("p2", _cipher())}

    registry = DeferredMigrationRegistry()
    registry.register(_MIGRATION)(M.claudecode_conversation_buffer_out_of_workdir)

    # Crash after the first doc's new blobs are stored, before its update.
    original_update_one = AsyncIOMotorCollection.update_one
    state = {"armed": True}

    async def flaky_update_one(self, filter, update, *args, **kwargs):
        if state["armed"] and "workdirBlobId" in update.get("$set", {}):
            state["armed"] = False
            raise RuntimeError("engine died mid-migration")
        return await original_update_one(self, filter, update, *args, **kwargs)

    monkeypatch.setattr(AsyncIOMotorCollection, "update_one", flaky_update_one)

    with pytest.raises(RuntimeError, match="engine died"):
        await registry.run(mongo_db, _PREFIX, tasks.get)

    first = await _reload(mongo_db, snap1)
    assert "conversationBufferMigrated" not in first
    assert first["workdirBlobId"] == snap1["workdirBlobId"]
    assert await _read_blob(mongo_db, snap1["workdirBlobId"])  # old blob intact
    assert await mongo_db[f"{_PREFIX}_fw_deferred_migrations"].count_documents({}) == 0

    await registry.run(mongo_db, _PREFIX, tasks.get)

    for snap in (snap1, snap2):
        doc = await _reload(mongo_db, snap)
        assert doc["conversationBufferMigrated"] is True
        assert await _read_blob(mongo_db, doc["conversationBlobId"]) == b"E" + _BUFFER
        names = _tar_names(await _read_blob(mongo_db, doc["workdirBlobId"]))
        assert names == {"./keep.txt", "./src/main.py"}
        assert await _blob_gone(mongo_db, snap["workdirBlobId"])
    applied = await mongo_db[f"{_PREFIX}_fw_deferred_migrations"].find({}).to_list(None)
    assert [d["name"] for d in applied] == [_MIGRATION]
    # Only the failed attempt's two orphans (workdir + conversation) remain
    # beyond what the snapshots reference.
    referenced = set()
    for snap in (snap1, snap2):
        doc = await _reload(mongo_db, snap)
        referenced |= {doc["workdirBlobId"], doc["conversationBlobId"]}
    orphans = await mongo_db["fs.files"].find({"_id": {"$nin": list(referenced)}}).to_list(None)
    assert sorted(f["filename"] for f in orphans) == ["conversation", "workdir"]


def test_registered_on_import():
    # A fresh interpreter: here `optio_claudecode.migrations` is imported
    # already, so only a bare `import optio_claudecode` shows the package
    # registers it by itself.
    probe = (
        "import optio_claudecode\n"
        "from optio_core import deferred_migrations\n"
        f"assert {_MIGRATION!r} in deferred_migrations._fns, deferred_migrations._fns\n"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)

    from optio_core import deferred_migrations

    assert (
        deferred_migrations._fns[_MIGRATION]
        is M.claudecode_conversation_buffer_out_of_workdir
    )
