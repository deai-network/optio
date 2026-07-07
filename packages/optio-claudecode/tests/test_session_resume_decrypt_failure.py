"""Resume with a corrupted session blob must fail loud, not fresh-start."""

import asyncio
import os

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import load_latest_snapshot


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_decrypt_{os.getpid()}"
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


def _raise_on_decrypt(_b: bytes) -> bytes:
    raise ValueError("session blob decrypt failed: bad key or tampering")


async def test_decrypt_failure_propagates_and_no_fresh_start(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    pid = "cc_decrypt_fail"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    # First cycle: capture a (plaintext) snapshot.
    ctx1 = await _make_ctx(mongo_db, pid, resume=False)
    cfg1 = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        # Configured (logged-in) session: creds on disk so the snapshot
        # passes the credentials-present capture guard.
        credentials_json={"token": "test"},
    )
    await run_claudecode_session(ctx1, cfg1)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None

    # Resume with a decrypt hook that raises. The session must raise and
    # must NOT silently fall through to a fresh start.
    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    cfg2 = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=_raise_on_decrypt,
    )
    with pytest.raises(Exception) as exc:
        await run_claudecode_session(ctx2, cfg2)
    assert "decrypt" in repr(exc.value).lower()
