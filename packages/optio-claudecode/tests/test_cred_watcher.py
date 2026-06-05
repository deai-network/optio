"""Unit tests for the credential watcher helpers (LocalHost)."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_claudecode import cred_watcher
from optio_claudecode.seed_manifest import CLAUDE_CRED_MANIFEST, CLAUDE_SEED_SUFFIX


def _write_creds(host_workdir: str, refresh_token: str) -> None:
    claude = os.path.join(host_workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"claudeAiOauth": {"refreshToken": "%s"}}' % refresh_token)


async def test_cred_fingerprint_none_when_missing(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "m"))
    await host.setup_workdir()
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_cred_fingerprint_none_when_malformed_or_no_token(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "b"))
    await host.setup_workdir()
    claude = os.path.join(host.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write("not json")
    assert await cred_watcher.cred_fingerprint(host) is None
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"claudeAiOauth": {"refreshToken": ""}}')
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_cred_fingerprint_changes_with_content(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "c"))
    await host.setup_workdir()
    _write_creds(host.workdir, "T1")
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_creds(host.workdir, "T2")
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_credwatch_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _ctx(mongo_db, taskdir):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_run_credential_watcher_saves_on_change_then_cancels(
    mongo_db, tmp_workdir, monkeypatch,
):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)

    host = LocalHost(taskdir=os.path.join(tmp_workdir, "w"))
    await host.setup_workdir()
    _write_creds(host.workdir, "T1")
    ctx = await _ctx(mongo_db, host.taskdir)

    # seed the customer's seed with T1
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CLAUDE_CRED_MANIFEST, suffix=CLAUDE_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    # rotate creds; the watcher should pick it up within a few intervals
    _write_creds(host.workdir, "T2")
    for _ in range(40):
        await asyncio.sleep(0.05)
        dst = LocalHost(taskdir=os.path.join(tmp_workdir, f"chk{_}"))
        await dst.setup_workdir()
        await seeds.merge_seed(
            ctx, dst, seed_id=seed_id, manifest=CLAUDE_CRED_MANIFEST,
            suffix=CLAUDE_SEED_SUFFIX, decrypt=None,
        )
        with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
            if "T2" in fh.read():
                break
    else:
        task.cancel()
        raise AssertionError("watcher did not save back the rotated credentials")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
