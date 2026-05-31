"""A fresh session with on_seed_saved captures an env-only seed."""

import asyncio
import io
import os
import tarfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX
from optio_claudecode.session import run_claudecode_session
from optio_agents import seeds


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_cap_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


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


async def test_capture_fires_callback_and_stores_env_only_seed(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")
    captured: list[str] = []

    async def _on_seed_saved(seed_id: str) -> None:
        captured.append(seed_id)

    ctx = await _make_ctx(mongo_db, "cc_seed_cap")
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
    )
    await run_claudecode_session(ctx, cfg)

    # callback fired with a hex id
    assert len(captured) == 1
    seed_id = captured[0]

    # a seed doc + blob exist
    doc = await seeds.load_seed(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id)
    assert doc is not None

    # the seed tar contains ONLY INCLUDE paths, never the transcript
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(doc["blobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith(".credentials.json") for n in names)
    assert any(n.endswith("settings.json") for n in names)
    assert any(n == ".claude.json" for n in names)
    assert any("plugins" in n for n in names)
    assert not any("projects" in n for n in names), names
    assert not any("history.jsonl" in n for n in names), names
