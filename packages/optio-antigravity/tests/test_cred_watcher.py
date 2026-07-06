"""Unit tests for the antigravity credential watcher (LocalHost + real Mongo).

Mirrors optio-grok's test_cred_watcher (grok ← antigravity renames). agy's
rotating Google OAuth refresh token is the exact save-back failure mode the
watcher exists for. The token store lives at
``<workdir>/home/.gemini/oauth_creds.json`` (design likely-outcome; TODO(S1)).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_antigravity import cred_watcher
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_CRED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
)


def _write_creds(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", ".gemini")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "oauth_creds.json"), "w") as fh:
        fh.write(text)


@pytest.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


# --- save-back gate (cred_fingerprint) ---------------------------------

async def test_fingerprint_none_when_missing(host):
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_unparseable(host):
    _write_creds(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_no_refresh_token(host):
    # A present-but-logged-out store (no refresh_token) must not be saved back.
    _write_creds(host.workdir, {"access_token": "a", "scope": "openid"})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_empty_object(host):
    _write_creds(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_changes_with_content(host):
    _write_creds(host.workdir, {"refresh_token": "T1"})
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_creds(host.workdir, {"refresh_token": "T2"})
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


# --- capture gate -------------------------------------------------------

async def test_capture_gate_requires_valid_token(host):
    assert not await cred_watcher.capture_gate_ok(host)
    _write_creds(host.workdir, "not json")
    assert not await cred_watcher.capture_gate_ok(host)
    _write_creds(host.workdir, {"access_token": "a"})  # no refresh_token
    assert not await cred_watcher.capture_gate_ok(host)
    _write_creds(host.workdir, {"refresh_token": "T"})
    assert await cred_watcher.capture_gate_ok(host)


# --- save_back_if_changed ------------------------------------------------

async def test_save_back_only_on_change(mongo_db, host, tmp_path):
    ctx = await _ctx(mongo_db)
    _write_creds(host.workdir, {"refresh_token": "T1"})
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=ANTIGRAVITY_CRED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    # Unchanged: returns baseline, no write.
    fp = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp == baseline

    # Changed: writes, returns a new fingerprint.
    _write_creds(host.workdir, {"refresh_token": "T2"})
    fp2 = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp2 is not None and fp2 != baseline

    # The seed now carries the rotated token.
    dst = LocalHost(taskdir=str(tmp_path / "chk"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=ANTIGRAVITY_CRED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".gemini", "oauth_creds.json")) as fh:
        assert "T2" in fh.read()


async def test_save_back_skips_logged_out_store(mongo_db, host):
    # A live store that drops to logged-out (no refresh_token) must NOT poison
    # the seed: save-back keeps the baseline unchanged.
    ctx = await _ctx(mongo_db)
    _write_creds(host.workdir, {"refresh_token": "T1"})
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=ANTIGRAVITY_CRED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)
    _write_creds(host.workdir, {"access_token": "a"})  # logged out
    fp = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp == baseline


# --- watcher integration (real Mongo) ------------------------------------

async def test_watcher_saves_back_on_change(mongo_db, host, tmp_path, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_creds(host.workdir, {"refresh_token": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=ANTIGRAVITY_CRED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_creds(host.workdir, {"refresh_token": "T2"})
    try:
        for i in range(40):
            await asyncio.sleep(0.05)
            dst = LocalHost(taskdir=str(tmp_path / f"chk{i}"))
            await dst.setup_workdir()
            await seeds.merge_seed(
                ctx, dst, seed_id=seed_id, manifest=ANTIGRAVITY_CRED_MANIFEST,
                suffix=ANTIGRAVITY_SEED_SUFFIX, decrypt=None,
            )
            p = os.path.join(dst.workdir, "home", ".gemini", "oauth_creds.json")
            with open(p) as fh:
                if "T2" in fh.read():
                    break
        else:
            raise AssertionError("watcher did not save back the rotated token store")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watcher_cancels_session_on_lease_loss(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_creds(host.workdir, {"refresh_token": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=ANTIGRAVITY_CRED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        poolKey="pool1", holder="p",
    )
    assert got == seed_id
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="p",
    ))
    # Steal the lease: release as p, re-acquire as another holder.
    await seeds.release(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        poolKey="pool1", holder="thief",
    )
    assert stolen == seed_id

    for _ in range(60):
        await asyncio.sleep(0.05)
        if ctx.cancellation_flag.is_set():
            break
    else:
        task.cancel()
        raise AssertionError("watcher did not flag cancellation on lease loss")
    await task  # returns (not raises): lease-loss exit is a normal return
