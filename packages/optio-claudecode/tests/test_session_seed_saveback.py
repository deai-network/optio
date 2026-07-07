"""End-to-end: a seeded session that rotates its credentials saves them back
to the seed (via the teardown backstop)."""

import asyncio
import os

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_agents import seeds
from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import CLAUDE_SEED_MANIFEST, CLAUDE_SEED_SUFFIX
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_saveback_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=False,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_session_saves_rotated_credentials_back_to_seed(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")

    # 1) capture an initial seed (creds token "x" from the seed scenario plant)
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cc_sb_src")
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

    # 2) a seeded session whose before_execute rotates the credentials file on
    # the host; the teardown backstop must save it back to THIS seed.
    async def _rotate(hook_ctx):
        host = hook_ctx._host
        await host.put_file_to_host(
            b'{"claudeAiOauth": {"refreshToken": "ROTATED"}}',
            f"{host.workdir}/home/.claude/.credentials.json",
        )

    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx2 = await _make_ctx(mongo_db, "cc_sb_run")
    await run_claudecode_session(ctx2, ClaudeCodeTaskConfig(
        consumer_instructions="(seeded run)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_rotate,
    ))

    # 3) the seed now carries the rotated credentials (merge into a fresh host,
    # reusing ctx2 only for blob I/O)
    from optio_host.host import LocalHost
    check = LocalHost(taskdir=os.path.join(task_root, "sb_check"))
    await check.setup_workdir()
    await seeds.merge_seed(
        ctx2, check, seed_id=seed_id, manifest=CLAUDE_SEED_MANIFEST,
        suffix=CLAUDE_SEED_SUFFIX, decrypt=None,
    )
    with open(os.path.join(check.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert "ROTATED" in fh.read()
