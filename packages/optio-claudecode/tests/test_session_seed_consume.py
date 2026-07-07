"""Capture a seed, then a second fresh session consumes it."""

import asyncio
import json
import os

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_con_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id, *, resume=False):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0}, resume=resume,
    )


async def test_second_session_consumes_seed(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")

    # 1) capture
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cc_seed_src")
    await run_claudecode_session(ctx1, ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
    ))
    seed_id = captured[0]

    # 2) consume in a DIFFERENT process; probe the planted env via before_execute
    observed = {}

    async def _probe(hook_ctx):
        wd = hook_ctx._host.workdir
        observed["creds"] = os.path.exists(f"{wd}/home/.claude/.credentials.json")
        observed["plugins"] = os.path.exists(f"{wd}/home/.claude/plugins")
        observed["projects_dir"] = os.path.exists(f"{wd}/home/.claude/projects")
        cj = await hook_ctx.read_text_from_host("home/.claude/.claude.json")
        observed["projects_key"] = list(json.loads(cj)["projects"].keys())
        observed["new_cwd"] = wd

    # the second session must NOT re-run the seed scenario's planting on top;
    # use the "happy" scenario so the planted files come purely from the seed.
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx2 = await _make_ctx(mongo_db, "cc_seed_dst")
    await run_claudecode_session(ctx2, ClaudeCodeTaskConfig(
        consumer_instructions="(seeded fresh)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_probe,
    ))

    assert observed["creds"] is True
    # plugins are not seeded since manifest v2 (re-installed on launch), so the
    # consumed seed does not bring a plugins dir.
    assert observed["plugins"] is False
    # transcript dir from the seed-source session must NOT be restored
    assert observed["projects_dir"] is False
    # .claude.json projects rekeyed to the new cwd
    assert observed["projects_key"] == [observed["new_cwd"]]
