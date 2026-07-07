"""Session-blob encryption round-trip for optio-cursor (harmonization P1).

Cursor persists its whole session (including ``home/.cursor`` chat state) in a
single workdir tar. When ``session_blob_encrypt``/``session_blob_decrypt`` are
set, that tar is encrypted at rest on the GridFS write and decrypted on the
resume read. This test proves both halves in one full cycle:

  * the stored workdir blob is NOT a readable gzip tar (it was encrypted), and
  * a resume with the matching decrypt still restores + passes ``--continue``
    (two launches survive, so the encrypted tar round-tripped).

Mirrors ``test_session_resume.py`` (adds the encrypt/decrypt pair).
"""

from __future__ import annotations

import asyncio
import io
import pathlib
import tarfile

import pytest
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_cursor import CursorTaskConfig
from optio_cursor.session import run_cursor_session
from optio_cursor.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


# A trivially reversible "cipher": byte-reverse. Enough to prove the write is
# transformed (a reversed gzip tar is unreadable) and the read undoes it.
def _reverse(b: bytes) -> bytes:
    return b[::-1]


async def _make_ctx(mongo_db, process_id: str, *, resume: bool) -> ProcessContext:
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )


def _cfg(shim_install_dir: pathlib.Path) -> CursorTaskConfig:
    return CursorTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        session_blob_encrypt=_reverse,
        session_blob_decrypt=_reverse,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_cursor_session(ctx, _cfg(shim))


async def test_encrypted_blob_is_opaque_and_round_trips(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cursor_encrypt_1"
    # Fresh run captures an ENCRYPTED workdir tar.
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    raw = await (await bucket.open_download_stream(snap["workdirBlobId"])).read()
    # Opaque at rest: the reversed gzip tar cannot be opened directly.
    with pytest.raises(Exception):
        tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz").getmembers()
    # But undoing the transform yields a valid gzip tar (decrypt is correct).
    with tarfile.open(fileobj=io.BytesIO(_reverse(raw)), mode="r:gz") as tar:
        assert tar.getmembers()

    # Resume run: decrypts the stored tar, restores, passes --continue.
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    count = await mongo_db[
        f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    ].count_documents({"processId": pid})
    assert count == 2, "resume did not capture a second snapshot (round-trip failed)"
