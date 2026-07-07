"""Full-cycle resume test for optio-cursor against fake_cursor.py (Stage 2).

Cursor persists its chat state under ``$HOME/.cursor`` (which lives inside
the workdir at ``<workdir>/home/.cursor``) and ``cursor-agent --continue``
resumes the most recent session for the cwd. So resume = restore the workdir
tar (carrying ``home/.cursor``) + pass ``--continue``. This test proves both
halves in one shot: after a resume cycle the fake-cursor argv log carries TWO
launches (the seed line survived the restore), and only the second launch
carries ``--continue``.

Adapted from optio-grok's ``test_session_resume.py`` (grok → cursor renames;
``-c`` → ``--continue``).
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

from optio_cursor import CursorTaskConfig
from optio_cursor.session import run_cursor_session
from optio_cursor.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


def test_config_resume_defaults():
    """Stage 2 flips supports_resume ON by default and adds workdir_exclude."""
    c = CursorTaskConfig(consumer_instructions="x")
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


def _cfg(shim_install_dir: pathlib.Path) -> CursorTaskConfig:
    return CursorTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_cursor_session(ctx, _cfg(shim))


async def test_terminal_flow_captures_snapshot(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cursor_terminal_1"
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    assert "workdirBlobId" in snap and "sessionBlobId" not in snap

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_resume_restores_workdir_and_passes_continue(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cursor_resume_1"
    # Seed run (fresh): captures snapshot 1 with a 1-line argv log.
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)
    # Resume run: restores the workdir (incl. home/.cursor argv log) and
    # passes --continue.
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
            if m.name.endswith("home/.cursor/fake_cursor_argv.jsonl")
        )
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()

    launches = [json.loads(line) for line in argv_lines if line]
    # Two launches ⟹ the seed run's line survived the restore (restore worked).
    assert len(launches) == 2, launches
    # Fresh launch: no --continue. Resumed launch: --continue present.
    assert "--continue" not in launches[0], launches[0]
    assert "--continue" in launches[1], launches[1]
    # PUSH resume awareness: the resumed launch carries a trailing
    # ``System: you have been resumed`` positional (delivered to the continued
    # cursor TUI as a new turn); the fresh launch does not. resume.log stays
    # the pull-based backstop; this is the push half.
    assert not any("you have been resumed" in a for a in launches[0]), launches[0]
    notice = [a for a in launches[1] if "you have been resumed" in a]
    assert len(notice) == 1 and notice[0].startswith("System: "), launches[1]


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """resume=True with no snapshot on record must still run (fresh) and, on a
    normal terminal exit, capture its own snapshot — not raise."""
    pid = "cursor_resume_no_prior"
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
