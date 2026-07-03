"""Full-cycle resume test for optio-codex against fake_codex.py (Stage 2).

Codex persists per-session rollout JSONLs under ``$CODEX_HOME/sessions``
(inside the workdir) and resumes ONLY by explicit id — ``codex resume <id>``
(``resume --last`` is cwd-filtered and silently starts a new session; never
used). So resume = restore the workdir tar (carrying ``home/.codex``) +
relaunch with the sessionId recorded in the snapshot. This test proves the
whole cycle in one shot: the fake-codex argv log carries TWO launches (the
first run's line survived the restore), only the second launch leads with
``resume <recorded-id>``, the auto-start positional is suppressed on the
resumed launch, and the snapshot honors the default exclude list (sessions
kept, packages/sqlite junk dropped).
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import re
import tarfile

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_codex import CodexTaskConfig
from optio_codex.host_actions import AUTO_START_PROMPT
from optio_codex.session import run_codex_session
from optio_codex.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


async def _make_ctx(mongo_db, process_id: str, *, resume: bool) -> ProcessContext:
    """A ProcessContext whose backing process doc carries supportsResume=True
    (via upsert_process) so mark_has_saved_state is honored, not ignored."""
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


def _cfg(shim_install_dir: pathlib.Path) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        codex_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        # Task-execution (iframe) demo shape: opt into the unattended kickoff
        # (the default is now False — Gap 2). Without this the fresh launch
        # carries no AUTO_START_PROMPT positional and the resume E2E fails.
        auto_start=True,
    )


async def _run(mongo_db, pid, shim, *, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "resume")
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_codex_session(ctx, _cfg(shim))


async def _open_workdir_tar(mongo_db, snap) -> tarfile.TarFile:
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    blob = await stream.read()
    return tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")


async def test_terminal_flow_captures_snapshot_with_session_id(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "codex_terminal_1"
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
    assert "workdirBlobId" in snap and "sessionBlobId" not in snap
    # The sessionId was scanned from the fake's rollout filename.
    assert snap["sessionId"] is not None and _UUID_RE.match(snap["sessionId"])

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_resume_restores_workdir_and_relaunches_by_session_id(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "codex_resume_1"
    # Fresh run: captures snapshot 1 (1-line argv log, new rollout).
    await _run(mongo_db, pid, shim_install_dir, resume=False, monkeypatch=monkeypatch)
    snap1 = await load_latest_snapshot(mongo_db, "test", pid)
    session_id = snap1["sessionId"]
    assert session_id is not None and _UUID_RE.match(session_id)

    # Resume run: restores the workdir (incl. home/.codex) and relaunches
    # via `codex resume <session_id>`.
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)

    count = await mongo_db[
        f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    ].count_documents({"processId": pid})
    assert count == 2

    snap2 = await load_latest_snapshot(mongo_db, "test", pid)
    # The resumed launch continued the SAME session — same recorded id.
    assert snap2["sessionId"] == session_id

    with await _open_workdir_tar(mongo_db, snap2) as tar:
        names = set(tar.getnames())
        argv_member = next(
            m for m in tar.getmembers()
            if m.name.endswith("home/.codex/fake_codex_argv.jsonl")
        )
        argv_lines = (
            tar.extractfile(argv_member).read().decode("utf-8").splitlines()
        )
        resume_member = next(
            m for m in tar.getmembers() if m.name == "resume.log"
        )
        resume_lines = (
            tar.extractfile(resume_member).read().decode("utf-8").splitlines()
        )

    # Exclude-list truth: the session store IS in the tar, the junk is NOT.
    assert any(n.startswith("home/.codex/sessions/") for n in names)
    assert not any(n.startswith("home/.codex/packages") for n in names)
    assert not any(".sqlite" in n for n in names)

    launches = [json.loads(line) for line in argv_lines if line]
    # Two lines ⟹ the first run's line survived the restore (restore worked).
    assert len(launches) == 2, launches
    # Fresh launch: no resume subcommand; auto-start positional LAST.
    assert launches[0][:1] != ["resume"]
    assert launches[0][-1] == AUTO_START_PROMPT
    # Resumed launch: `resume <recorded id>` BEFORE the flags; positional
    # suppressed (re-kicking would enqueue a duplicate task).
    assert launches[1][:2] == ["resume", session_id]
    assert AUTO_START_PROMPT not in launches[1]
    # PUSH resume awareness (Gap 1): only the RESUMED launch carries the
    # System: notice positional, so the resumed session gets a "you have
    # been resumed" turn. The fresh launch never does (it got the kickoff).
    assert not any("you have been resumed" in str(a) for a in launches[0]), launches[0]
    assert any("you have been resumed" in str(a) for a in launches[1]), launches[1]
    # resume.log: one line per session start (fresh + resume).
    assert len(resume_lines) == 2


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """resume=True with no snapshot on record must still run (fresh) and, on
    a normal terminal exit, capture its own snapshot — not raise."""
    pid = "codex_resume_no_prior"
    await _run(mongo_db, pid, shim_install_dir, resume=True, monkeypatch=monkeypatch)
    snap = await load_latest_snapshot(mongo_db, "test", pid)
    assert snap is not None
