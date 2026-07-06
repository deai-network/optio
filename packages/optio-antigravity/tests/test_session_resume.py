"""Full-cycle resume test for optio-antigravity against fake_agy.py (Stage 2).

``agy`` persists its conversation state under ``home/.gemini/antigravity``
(which lives inside the workdir) and ``agy --continue`` resumes the most recent
conversation for the workspace. So resume = restore the workdir tar (carrying
``home/.gemini/antigravity``) + pass ``--continue``. This test proves both
halves in one shot: after a resume cycle the fake-agy argv log carries TWO
launches (the seed line survived the restore), and only the second launch
carries ``--continue`` + the PUSH ``System: you have been resumed`` positional.

Mirrors optio-grok's ``test_session_resume.py`` (grok ← agy renames; grok's
``-c`` flag ← agy's ``--continue``).
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import tarfile

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_antigravity import AntigravityTaskConfig
from optio_antigravity.session import run_antigravity_session
from optio_antigravity.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


def test_config_resume_defaults():
    """Stage 2: supports_resume is ON by default and workdir_exclude defaults None."""
    c = AntigravityTaskConfig(consumer_instructions="x")
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


def _cfg(shim_install_dir: pathlib.Path) -> AntigravityTaskConfig:
    return AntigravityTaskConfig(
        consumer_instructions="do the thing",
        agy_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        # Resume mechanics only — fs-isolation has its own suite, and the fake
        # agy can't run under a real claustrum here.
        fs_isolation=False,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_antigravity_session(ctx, _cfg(shim))


async def test_terminal_flow_captures_snapshot(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "agy_terminal_1"
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    assert "workdirBlobId" in snap and "sessionBlobId" not in snap

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_resume_restores_workdir_and_passes_continue(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "agy_resume_1"
    # Seed run (fresh): captures snapshot 1 with a 1-line argv log.
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)
    # Resume run: restores the workdir (incl. the agy argv log) and passes --continue.
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
            if m.name.endswith("home/.gemini/antigravity/fake_agy_argv.jsonl")
        )
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()

    launches = [json.loads(line) for line in argv_lines if line]
    # Two launches ⟹ the seed run's line survived the restore (restore worked).
    assert len(launches) == 2, launches
    # Fresh launch: no --continue. Resumed launch: --continue present.
    assert "--continue" not in launches[0], launches[0]
    assert "--continue" in launches[1], launches[1]
    # PUSH resume awareness: only the resumed launch carries the System: notice
    # positional so the resumed session gets a "you have been resumed" turn.
    assert not any("you have been resumed" in str(a) for a in launches[0]), launches[0]
    assert any("you have been resumed" in str(a) for a in launches[1]), launches[1]


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """resume=True with no snapshot on record must still run (fresh) and, on a
    normal terminal exit, capture its own snapshot — not raise."""
    pid = "agy_resume_no_prior"
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
