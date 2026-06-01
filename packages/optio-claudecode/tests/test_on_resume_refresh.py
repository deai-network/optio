"""on_resume_refresh fires only on resume and rewrites CLAUDE.md."""

import asyncio
import io
import os
import tarfile

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import load_latest_snapshot


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_refresh_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, pid, *, resume):
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


def _bump_instructions(cfg: ClaudeCodeTaskConfig) -> ClaudeCodeTaskConfig:
    import dataclasses
    return dataclasses.replace(
        cfg, consumer_instructions=cfg.consumer_instructions + " [REFRESHED]",
    )


async def test_resume_refresh_tags_resume_log(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    pid = "cc_refresh_1"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "idempotent_done")

    base = dict(
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
    )

    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    await run_claudecode_session(
        ctx1, ClaudeCodeTaskConfig(consumer_instructions="orig", **base),
    )

    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    await run_claudecode_session(
        ctx2,
        ClaudeCodeTaskConfig(
            consumer_instructions="orig", on_resume_refresh=_bump_instructions, **base,
        ),
    )

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = tar.getmember("resume.log")
        contents = tar.extractfile(member).read().decode("utf-8")
    lines = [l for l in contents.splitlines() if l]
    assert len(lines) == 2
    assert "REFRESHED:CLAUDE.md" in lines[1]
