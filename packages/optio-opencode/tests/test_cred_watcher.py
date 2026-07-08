"""Unit tests for the opencode credential watcher (LocalHost)."""

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_opencode import cred_watcher
from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST, OPENCODE_SEED_SUFFIX


def _write_auth(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", ".local", "share", "opencode")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(text)


def _write_model_config(workdir: str, model: str | None) -> None:
    d = os.path.join(workdir, "home", ".config", "opencode")
    os.makedirs(d, exist_ok=True)
    cfg = {"model": model} if model is not None else {}
    with open(os.path.join(d, "opencode.json"), "w") as fh:
        fh.write(json.dumps(cfg))


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


# --- save-back gate (cred_fingerprint) ---------------------------------

async def test_fingerprint_none_when_missing(host):
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_unparseable(host):
    _write_auth(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_no_providers(host):
    _write_auth(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_changes_with_content(host):
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T2"}})
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


# --- capture gate -------------------------------------------------------

async def test_capture_gate_requires_auth_and_model(host):
    # no auth, no model
    assert not await cred_watcher.capture_gate_ok(host)
    # auth only
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T"}})
    assert not await cred_watcher.capture_gate_ok(host)
    # auth + empty model config
    _write_model_config(host.workdir, None)
    assert not await cred_watcher.capture_gate_ok(host)
    # auth + model
    _write_model_config(host.workdir, "openai/gpt-5.4-mini")
    assert await cred_watcher.capture_gate_ok(host)


# --- watcher integration (real Mongo) ------------------------------------

@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_credwatch_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_watcher_saves_back_on_change(mongo_db, host, tmp_path, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=OPENCODE_CRED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T2"}})
    try:
        # Poll the OBSERVABLE (the rotated token saved back) until it appears,
        # bounded by a generous monotonic deadline rather than a fixed iteration
        # count — the background watcher may be starved off-CPU arbitrarily long.
        deadline = time.monotonic() + 60.0
        i = 0
        saved_back = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            dst = LocalHost(taskdir=str(tmp_path / f"chk{i}"))
            i += 1
            await dst.setup_workdir()
            await seeds.merge_seed(
                ctx, dst, seed_id=seed_id, manifest=OPENCODE_CRED_MANIFEST,
                suffix=OPENCODE_SEED_SUFFIX, decrypt=None,
            )
            p = os.path.join(
                dst.workdir, "home", ".local", "share", "opencode", "auth.json",
            )
            with open(p) as fh:
                if "T2" in fh.read():
                    saved_back = True
                    break
        if not saved_back:
            raise AssertionError("watcher did not save back the rotated auth.json")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watcher_cancels_session_on_lease_loss(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=OPENCODE_CRED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        poolKey="pool1", holder="p",
    )
    assert got == seed_id
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="p",
    ))
    # steal the lease: release as p, re-acquire as another holder
    await seeds.release(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        poolKey="pool1", holder="thief",
    )
    assert stolen == seed_id

    # watcher must notice the CAS failure and set the cancellation flag; poll the
    # observable under a generous monotonic deadline (the watcher task may be
    # starved off-CPU for an arbitrary wall-clock stretch).
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if ctx.cancellation_flag.is_set():
            break
        await asyncio.sleep(0.05)
    else:
        task.cancel()
        raise AssertionError("watcher did not flag cancellation on lease loss")
    await task  # returns (not raises): lease-loss exit is a normal return
