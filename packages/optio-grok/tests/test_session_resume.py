"""Full-cycle resume test for optio-grok against fake_grok.py (Stage 2).

Grok persists its session under ``<GROK_HOME>/sessions`` (which lives inside
the workdir) and ``grok --continue`` resumes the most recent session for the
cwd. So resume = restore the workdir tar (carrying ``home/.grok``) + pass
``-c``. This test proves both halves in one shot: after a resume cycle the
fake-grok argv log carries TWO launches (the seed line survived the restore),
and only the second launch carries ``-c``.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import tarfile

import pytest
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_grok import GrokTaskConfig
from optio_grok.session import run_grok_session
from optio_grok.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


def test_config_resume_defaults():
    """Stage 2 flips supports_resume ON by default and adds workdir_exclude."""
    c = GrokTaskConfig(consumer_instructions="x")
    assert c.supports_resume is True
    assert c.workdir_exclude is None


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


def _cfg(shim_install_dir: pathlib.Path) -> GrokTaskConfig:
    return GrokTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_grok_session(ctx, _cfg(shim))


async def test_terminal_flow_captures_snapshot(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "grok_terminal_1"
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    assert "workdirBlobId" in snap and "sessionBlobId" not in snap

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_resume_restores_workdir_and_passes_continue(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "grok_resume_1"
    # Seed run (fresh): captures snapshot 1 with a 1-line argv log.
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)
    # Resume run: restores the workdir (incl. home/.grok argv log) and passes -c.
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)

    count = await mongo_db[
        f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    ].count_documents({"processId": pid})
    assert count == 2

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    wstream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await wstream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers()
            if m.name.endswith("home/.grok/fake_grok_argv.jsonl")
        )
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()

    launches = [json.loads(line) for line in argv_lines if line]
    # Two launches ⟹ the seed run's line survived the restore (restore worked).
    assert len(launches) == 2, launches
    # Fresh launch: no --continue. Resumed launch: -c present.
    assert "-c" not in launches[0], launches[0]
    assert "-c" in launches[1], launches[1]
    # PUSH resume awareness: only the resumed launch carries the System: notice
    # positional so the resumed session gets a "you have been resumed" turn.
    assert not any("you have been resumed" in str(a) for a in launches[0]), launches[0]
    assert any("you have been resumed" in str(a) for a in launches[1]), launches[1]


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """resume=True with no snapshot on record must still run (fresh) and, on a
    normal terminal exit, capture its own snapshot — not raise."""
    pid = "grok_resume_no_prior"
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None


def _rev(b: bytes) -> bytes:
    """Trivial reversible session-blob transform for the encrypt round-trip."""
    return b[::-1]


async def test_session_blob_encrypt_roundtrip(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """P1: session_blob_encrypt/decrypt wrap the workdir tar (grok's session
    carrier, since home/.grok lives inside the workdir). The stored blob is
    ciphertext at rest, and the matching decrypt restores it on resume."""
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "resume")
    pid = "grok_encrypt_1"
    cfg = GrokTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        session_blob_encrypt=_rev,
        session_blob_decrypt=_rev,
    )

    # Fresh run: captures an ENCRYPTED workdir snapshot.
    ctx = await _make_ctx(mongo_db, pid, resume=False)
    await run_grok_session(ctx, cfg)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stored = await (await bucket.open_download_stream(snap["workdirBlobId"])).read()
    # At rest the blob is the reversed tar — NOT a valid gzip until un-reversed.
    with pytest.raises((tarfile.ReadError, OSError)):
        tarfile.open(fileobj=io.BytesIO(stored), mode="r:gz")
    with tarfile.open(fileobj=io.BytesIO(stored[::-1]), mode="r:gz") as tar:
        assert any(
            m.name.endswith("home/.grok/fake_grok_argv.jsonl")
            for m in tar.getmembers()
        )

    # Resume with the matching decrypt: restores the encrypted tar and relaunches.
    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    await run_grok_session(ctx2, cfg)
    snap2 = await load_latest_snapshot(mongo_db, "test", pid)
    stored2 = await (await bucket.open_download_stream(snap2["workdirBlobId"])).read()
    with tarfile.open(fileobj=io.BytesIO(stored2[::-1]), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers()
            if m.name.endswith("home/.grok/fake_grok_argv.jsonl")
        )
        launches = [
            json.loads(line)
            for line in tar.extractfile(member).read().decode("utf-8").splitlines()
            if line
        ]
    # Two launches ⟹ the fresh run's line survived the encrypted restore.
    assert len(launches) == 2, launches
    assert "-c" in launches[1], launches[1]
