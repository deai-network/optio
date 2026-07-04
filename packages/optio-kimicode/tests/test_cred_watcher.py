"""Unit + session tests for the kimi credential watcher and seeded teardown.

kimi authenticates with a ROTATING SINGLE-USE refresh token stored at
``<KIMI_CODE_HOME>/credentials/kimi-code.json``. The watcher saves the rotated
file back into the seed each poll, a teardown finally backstop persists a
last-window rotation, and a SEEDED session is torn down GRACEFULLY (SIGTERM +
wait) even on cancel so kimi can flush the rotated token before the backstop
reads it — a SIGKILL would strand the rotation and persist a spent token.

Adapted from optio-grok's ``test_cred_watcher`` (grok ``.grok/auth.json`` → kimi
``credentials/kimi-code.json``; save-back via ``seeds.overwrite_seed_member``).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import tarfile

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_kimicode import KimiCodeTaskConfig, create_kimicode_task, cred_watcher
from optio_kimicode.seed_manifest import (
    KIMI_SEED_MANIFEST,
    KIMI_SEED_SUFFIX,
)


_CRED_MEMBER = "credentials/kimi-code.json"


def _write_cred(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", "credentials")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "kimi-code.json"), "w") as fh:
        fh.write(text)


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


async def _ctx(mongo_db):
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def _seed_cred(mongo_db, seed_id: str) -> dict:
    """Extract ``credentials/kimi-code.json`` from a seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(_CRED_MEMBER)
        return json.loads(f.read().decode("utf-8"))


async def _plant_cred_seed(mongo_db, tmp_path, refresh: str = "ORIGINAL") -> str:
    """Capture a kimi seed carrying a credentials/kimi-code.json via a scratch
    host, returning its seed id."""
    ctx = await _ctx(mongo_db)
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{refresh}"))
    await src.setup_workdir()
    _write_cred(src.workdir, {
        "access_token": f"access-{refresh}",
        "refresh_token": refresh,
        "expires_at": 9999999999,
        "scope": "offline",
        "token_type": "Bearer",
        "expires_in": 3600,
    })
    return await seeds.capture_seed(
        ctx, src, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )


# --- save-back gate (cred_fingerprint) ---------------------------------

async def test_fingerprint_none_when_missing(host):
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_unparseable(host):
    _write_cred(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_empty(host):
    _write_cred(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_changes_with_content(host):
    _write_cred(host.workdir, {"refresh_token": "T1"})
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_cred(host.workdir, {"refresh_token": "T2"})
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


async def test_capture_gate_requires_valid_cred(host):
    assert not await cred_watcher.capture_gate_ok(host)
    _write_cred(host.workdir, "not json")
    assert not await cred_watcher.capture_gate_ok(host)
    # Non-empty JSON but NO usable refresh_token (a login-less / half-written /
    # logged-out file) must gate OUT — else a dead seed (nothing to refresh with)
    # is captured. This is the claudecode-parity fix.
    _write_cred(host.workdir, {"access_token": "A", "scope": "kimi-code"})
    assert not await cred_watcher.capture_gate_ok(host)
    _write_cred(host.workdir, {"refresh_token": ""})
    assert not await cred_watcher.capture_gate_ok(host)
    assert await cred_watcher.cred_fingerprint(host) is None
    _write_cred(host.workdir, {"refresh_token": "T"})
    assert await cred_watcher.capture_gate_ok(host)


# --- (a) rotation is saved back -----------------------------------------

async def test_save_back_only_on_change(mongo_db, host, tmp_path):
    ctx = await _ctx(mongo_db)
    _write_cred(host.workdir, {"refresh_token": "T1"})
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    # Unchanged: returns baseline, no write.
    fp = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp == baseline

    # Rotated: writes the new file back via overwrite_seed_member; new fp.
    _write_cred(host.workdir, {"refresh_token": "T2"})
    fp2 = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp2 is not None and fp2 != baseline

    # The seed now carries the rotated token.
    assert (await _seed_cred(mongo_db, seed_id))["refresh_token"] == "T2"


async def test_save_back_threads_encrypt_decrypt(mongo_db, host, tmp_path):
    """The seed blob is (de/en)crypted with the supplied callables (Task 3.0):
    a reversible cipher round-trips through overwrite_seed_member."""
    def cipher(b: bytes) -> bytes:
        return bytes(x ^ 0x5A for x in b)

    ctx = await _ctx(mongo_db)
    _write_cred(host.workdir, {"refresh_token": "T1"})
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=cipher,
    )
    baseline = await cred_watcher.cred_fingerprint(host)
    _write_cred(host.workdir, {"refresh_token": "T2"})
    fp2 = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline,
        encrypt=cipher, decrypt=cipher,
    )
    assert fp2 is not None and fp2 != baseline
    # Decrypt the blob to read the rotated member back.
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(cipher(buf.getvalue())), mode="r:gz") as tar:
        member = json.loads(tar.extractfile(_CRED_MEMBER).read().decode("utf-8"))
    assert member["refresh_token"] == "T2"


async def test_watcher_saves_back_on_change(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_cred(host.workdir, {"refresh_token": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_cred(host.workdir, {"refresh_token": "T2"})
    try:
        for _ in range(40):
            await asyncio.sleep(0.05)
            if (await _seed_cred(mongo_db, seed_id))["refresh_token"] == "T2":
                break
        else:
            raise AssertionError("watcher did not save back the rotated token")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- (c) leases: lease loss + concurrent sessions don't strand ----------

async def test_watcher_cancels_session_on_lease_loss(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_cred(host.workdir, {"refresh_token": "T1"})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
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
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
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
    await task  # lease-loss exit is a normal return, not a raise


async def test_two_concurrent_leases_do_not_strand(mongo_db, host, tmp_path, monkeypatch):
    """Two sessions sharing one pool each lease a DISTINCT seed and both keep
    their leases renewed (neither strands the other); after both release, the
    whole pool is free again."""
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_cred(host.workdir, {"refresh_token": "T1"})
    ctx = await _ctx(mongo_db)
    seed_a = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )
    seed_b = await seeds.capture_seed(
        ctx, host, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )
    for sid in (seed_a, seed_b):
        await seeds.assign_to_pool(
            mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
            seed_id=sid, poolKey="shared",
        )

    got_a = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        poolKey="shared", holder="A",
    )
    got_b = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        poolKey="shared", holder="B",
    )
    assert {got_a, got_b} == {seed_a, seed_b}  # distinct seeds, no collision
    assert got_a != got_b
    baseline = await cred_watcher.cred_fingerprint(host)

    task_a = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=got_a, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="A",
    ))
    task_b = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=got_b, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="B",
    ))
    try:
        # Let both watchers renew across several ticks; neither must lose its
        # lease (which would flag cancellation) — no mutual stranding.
        await asyncio.sleep(0.4)
        assert not ctx.cancellation_flag.is_set()
        assert not task_a.done() and not task_b.done()
    finally:
        for t in (task_a, task_b):
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t

    # Both leases released → the whole pool is acquirable again.
    await seeds.release(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=got_a, holder="A",
    )
    await seeds.release(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=got_b, holder="B",
    )
    reacq1 = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        poolKey="shared", holder="C",
    )
    reacq2 = await seeds.acquire(
        mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX,
        poolKey="shared", holder="C",
    )
    assert {reacq1, reacq2} == {seed_a, seed_b}


# --- teardown-aggressiveness gate (FINDING 1) ---------------------------

def test_teardown_aggressive_gate():
    from optio_kimicode.session import _teardown_aggressive
    # Seeded is torn down GRACEFULLY even on cancel (let kimi flush the token).
    assert _teardown_aggressive(cancelled=True, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    # Non-seeded keeps the fast aggressive kill on cancel.
    assert _teardown_aggressive(cancelled=True, seeded=False) is True
    assert _teardown_aggressive(cancelled=False, seeded=False) is False


# --- session-level: seeded save-back lifecycle --------------------------

def _seed_cfg(shim_install_dir: pathlib.Path, seed_id: str, **kw) -> KimiCodeTaskConfig:
    base = dict(
        consumer_instructions="do the thing",
        kimi_install_dir=str(shim_install_dir),
        fs_isolation=False,
        supports_resume=False,
        seed_id=seed_id,
    )
    base.update(kw)
    return KimiCodeTaskConfig(**base)


async def test_backstop_saves_rotation_on_early_exit(
    mongo_db, task_root, shim_install_dir, tmp_path, ctx_and_captures, monkeypatch,
):
    """(b) The teardown finally backstop persists a rotation the watcher never
    polled: the fake rotates the token eagerly then reaches DONE within the
    (long) watch interval, so ONLY the backstop can have saved it back."""
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "seed_rotate")
    # Watcher interval left at its default (10s) — the session finishes in well
    # under a second, so no watcher poll fires; the backstop is the only path.
    ctx, _cap, _flag = ctx_and_captures
    seed_id = await _plant_cred_seed(mongo_db, tmp_path)

    task = create_kimicode_task(
        process_id="p", name="b", config=_seed_cfg(shim_install_dir, seed_id),
    )
    await task.execute(ctx)

    assert (await _seed_cred(mongo_db, seed_id))["refresh_token"] == "ROTATED-INSESSION"


async def test_cancelled_seeded_session_flushes_before_backstop(
    mongo_db, task_root, shim_install_dir, tmp_path, ctx_and_captures, monkeypatch,
):
    """(d) A CANCELLED seeded session is torn down GRACEFULLY (SIGTERM + wait),
    letting kimi flush the rotated single-use token before the backstop reads
    it. The fake writes the rotation ONLY in its SIGTERM handler, so the seed
    ends up with the rotated token iff the graceful (not SIGKILL) path ran."""
    monkeypatch.setenv("FAKE_KIMI_SCENARIO", "seed_rotate_on_term")
    ctx, cap, flag = ctx_and_captures
    seed_id = await _plant_cred_seed(mongo_db, tmp_path)

    task = create_kimicode_task(
        process_id="p", name="d",
        config=_seed_cfg(shim_install_dir, seed_id, auto_start=False),
    )
    session = asyncio.create_task(task.execute(ctx))

    # Wait until the widget is live (kimi launched, handle assigned) before
    # cancelling, so teardown exercises the graceful terminate path.
    for _ in range(200):
        if cap.widget_data:
            break
        await asyncio.sleep(0.05)
    else:
        session.cancel()
        raise AssertionError("kimi widget never went live")

    flag.set()  # cancel the session
    await session

    # The rotated token — flushed only by the fake's SIGTERM handler — reached
    # the seed, proving the graceful SIGTERM teardown (a SIGKILL would strand it).
    assert (await _seed_cred(mongo_db, seed_id))["refresh_token"] == "ROTATED-ON-TERM"
