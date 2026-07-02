"""Unit tests for the cursor credential watcher (LocalHost + real Mongo).

Mirrors optio-grok's test_cred_watcher (cursor ← grok renames; cursor's
`accessToken`/`refreshToken` pair is treated as potentially rotating — the
same single-use-token failure mode the opencode/grok watchers were built
for). The credential lives at ``<workdir>/home/.config/cursor/auth.json``.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_cursor import cred_watcher
from optio_cursor.seed_manifest import CURSOR_CRED_MANIFEST, CURSOR_SEED_SUFFIX


def _write_auth(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", ".config", "cursor")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(text)


@pytest_asyncio.fixture
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
    _write_auth(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_empty_object(host):
    _write_auth(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_changes_with_content(host):
    _write_auth(host.workdir, {"accessToken": "A1", "refreshToken": "T1"})
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_auth(host.workdir, {"accessToken": "A2", "refreshToken": "T2"})
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


# --- capture gate -------------------------------------------------------

async def test_capture_gate_requires_valid_auth(host):
    # no auth
    assert not await cred_watcher.capture_gate_ok(host)
    # unparseable auth
    _write_auth(host.workdir, "not json")
    assert not await cred_watcher.capture_gate_ok(host)
    # valid auth -> ok (cursor has no separate model requirement)
    _write_auth(host.workdir, {"accessToken": "A", "refreshToken": "T"})
    assert await cred_watcher.capture_gate_ok(host)


# --- save_back_if_changed ------------------------------------------------

async def test_save_back_only_on_change(mongo_db, host, tmp_path):
    ctx = await _ctx(mongo_db)
    _write_auth(host.workdir, {"accessToken": "A1", "refreshToken": "T1"})
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CURSOR_CRED_MANIFEST, suffix=CURSOR_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    # Unchanged: returns baseline, no write.
    fp = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp == baseline

    # Changed: writes, returns a new fingerprint.
    _write_auth(host.workdir, {"accessToken": "A2", "refreshToken": "T2"})
    fp2 = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp2 is not None and fp2 != baseline

    # The seed now carries the rotated token.
    dst = LocalHost(taskdir=str(tmp_path / "chk"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=CURSOR_CRED_MANIFEST,
        suffix=CURSOR_SEED_SUFFIX, decrypt=None,
    )
    p = os.path.join(dst.workdir, "home", ".config", "cursor", "auth.json")
    with open(p) as fh:
        assert "T2" in fh.read()


# --- watcher integration (real Mongo) ------------------------------------

async def test_watcher_saves_back_on_change(mongo_db, host, tmp_path, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"accessToken": "A1", "refreshToken": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CURSOR_CRED_MANIFEST, suffix=CURSOR_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_auth(host.workdir, {"accessToken": "A2", "refreshToken": "T2"})
    try:
        for i in range(40):
            await asyncio.sleep(0.05)
            dst = LocalHost(taskdir=str(tmp_path / f"chk{i}"))
            await dst.setup_workdir()
            await seeds.merge_seed(
                ctx, dst, seed_id=seed_id, manifest=CURSOR_CRED_MANIFEST,
                suffix=CURSOR_SEED_SUFFIX, decrypt=None,
            )
            p = os.path.join(dst.workdir, "home", ".config", "cursor", "auth.json")
            with open(p) as fh:
                if "T2" in fh.read():
                    break
        else:
            raise AssertionError("watcher did not save back the rotated auth.json")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watcher_cancels_session_on_lease_loss(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"accessToken": "A1", "refreshToken": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CURSOR_CRED_MANIFEST, suffix=CURSOR_SEED_SUFFIX,
        encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
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
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
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
