"""A bogus seed_id fails loudly — no silent vanilla fallback."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_unk_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id):
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
        cancellation_flag=asyncio.Event(), child_counter={"next": 0}, resume=False,
    )


async def test_unknown_seed_id_raises(mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_ctx(mongo_db, "cc_seed_unknown")
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(bad seed)",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=str(ObjectId()),  # well-formed but absent
    )
    with pytest.raises(Exception):  # KeyError surfaces through the session
        await run_claudecode_session(ctx, cfg)
