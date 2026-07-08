"""on_resume_refresh fires only on resume and rewrites AGENTS.md (P2).

Mirrors optio-claudecode's ``test_on_resume_refresh.py`` (CLAUDE.md → AGENTS.md).
A resume whose ``on_resume_refresh`` hook mutates the config recomposes AGENTS.md
and tags the second ``resume.log`` line with ``REFRESHED:AGENTS.md`` so the
resumed agent re-reads it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import pathlib
import tarfile

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_cursor import CursorTaskConfig
from optio_cursor.session import run_cursor_session
from optio_cursor.snapshots import load_latest_snapshot


async def _make_ctx(mongo_db, pid: str, *, resume: bool) -> ProcessContext:
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=pid, name=pid, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=pid, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
        resume=resume,
    )


def _bump_instructions(cfg: CursorTaskConfig) -> CursorTaskConfig:
    return dataclasses.replace(
        cfg, consumer_instructions=cfg.consumer_instructions + " [REFRESHED]",
    )


def _cfg(shim: pathlib.Path, **kw) -> CursorTaskConfig:
    base = dict(
        consumer_instructions="orig",
        install_dir=str(shim),
        ttyd_install_dir=str(shim),
        supports_resume=True,
        delivery_type="audit",
    )
    base.update(kw)
    return CursorTaskConfig(**base)


async def test_resume_refresh_tags_resume_log(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    pid = "cursor_refresh_1"
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "resume")

    # Fresh run: captures snapshot 1, resume.log has a single (unrefreshed) line.
    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    await run_cursor_session(ctx1, _cfg(shim_install_dir))

    # Resume run: the mutating hook recomposes AGENTS.md, tagging resume.log.
    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    await run_cursor_session(
        ctx2, _cfg(shim_install_dir, on_resume_refresh=_bump_instructions),
    )

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    workdir_bytes = await (
        await bucket.open_download_stream(snap["workdirBlobId"])
    ).read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers() if m.name.rstrip("/").endswith("resume.log")
        )
        contents = tar.extractfile(member).read().decode("utf-8")
    lines = [l for l in contents.splitlines() if l]
    assert len(lines) == 2, lines
    assert "REFRESHED:AGENTS.md" in lines[1], lines


async def test_resume_without_refresh_leaves_resume_log_untagged(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """The identity default recomposes the SAME AGENTS.md → no REFRESHED tag."""
    pid = "cursor_refresh_identity"
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "resume")

    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    await run_cursor_session(ctx1, _cfg(shim_install_dir))
    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    await run_cursor_session(ctx2, _cfg(shim_install_dir))  # identity default hook

    snap = await load_latest_snapshot(mongo_db, "test", pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    workdir_bytes = await (
        await bucket.open_download_stream(snap["workdirBlobId"])
    ).read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers() if m.name.rstrip("/").endswith("resume.log")
        )
        contents = tar.extractfile(member).read().decode("utf-8")
    lines = [l for l in contents.splitlines() if l]
    assert len(lines) == 2, lines
    assert "REFRESHED" not in lines[1], lines
