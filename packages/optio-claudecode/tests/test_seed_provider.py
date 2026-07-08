"""Provider resolution + lease wiring (no real claude; unit-level)."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_claudecode import cred_watcher
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX
from optio_claudecode.types import SeedProvider, SeedUnavailableError
from optio_agents import seeds


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = f"optio_cc_provider_{os.getpid()}"
    db = client[name]
    yield db
    await client.drop_database(name)
    client.close()


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "proc-1"})
    return ProcessContext(
        process_oid=oid, process_id="proc-1", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test", cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


async def _pooled_seed(mongo_db, pool):
    sid = await seeds.insert_seed(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, blob_id=ObjectId(), manifest_version=1)
    await seeds.assign_to_pool(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, seed_id=sid, poolKey=pool)
    return sid


def test_seed_provider_type_and_error_exist():
    assert SeedProvider is not None
    with pytest.raises(SeedUnavailableError):
        raise SeedUnavailableError("seed shortage")


async def test_watcher_renews_lease_and_aborts_when_lost(mongo_db, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    ctx = await _ctx(mongo_db)
    sid = await _pooled_seed(mongo_db, "pool-1")
    # proc-1 holds the lease
    got = await seeds.acquire(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, poolKey="pool-1", holder="proc-1")
    assert got == sid

    # cred save-back is irrelevant here; stub it to a no-op
    async def noop_saveback(*a, **k): return None
    monkeypatch.setattr(cred_watcher, "save_back_if_changed", noop_saveback)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host=None, seed_id=sid, baseline=None, encrypt=None, decrypt=None,
        lease_holder="proc-1",
    ))
    await asyncio.sleep(0.2)  # several renew ticks; lease stays held
    assert ctx.should_continue() is True

    # steal the lease out from under proc-1: expire it, re-acquire as someone else
    from datetime import datetime, timezone, timedelta
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    await mongo_db[f"test{CLAUDE_SEED_SUFFIX}"].update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "proc-1", "expiresAt": past}}},
    )
    assert await seeds.acquire(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, poolKey="pool-1", holder="thief") == sid

    # The next renew tick sees the loss and signals abort; poll for that
    # observable transition instead of assuming it happens within a fixed
    # wall-clock window (which flakes when the watcher task is CPU-starved).
    import time
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if ctx.cancellation_flag.is_set():
            break
        await asyncio.sleep(0.02)
    assert ctx.cancellation_flag.is_set()  # watcher signalled abort
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
