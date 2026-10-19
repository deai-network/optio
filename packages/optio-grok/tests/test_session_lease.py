"""Pooled-lease + save-back lifecycle test for optio-grok (Stage 4, Task 2).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake grok rotates its refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — opencode's deliberate ordering).

Asserts the seed's stored auth.json carries the rotated token and the lease is
free again afterwards.

The conversation-mode test runs the same seeded, leased session headless and
asserts the credential watcher keeps renewing the lease while the session is
live, and that teardown stops the watcher and releases the lease.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import pathlib
import tarfile
import time as _time

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_grok import GrokTaskConfig, create_grok_task, cred_watcher
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
from optio_grok.session import run_grok_session


async def _make_ctx(mongo_db, process_id: str) -> ProcessContext:
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
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
        resume=False,
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> GrokTaskConfig:
    return GrokTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        delivery_type="audit",
        **kw,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    """Extract ``.grok/auth.json`` from the seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".grok/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(mongo_db, tmp_path, *, prefix: str = "test") -> str:
    """Capture a seed carrying a grok auth.json + config.toml via a scratch host.

    ``prefix`` names the seed collection; the scratch process doc stays in
    ``test_processes``, outside the collection of an engine run under another
    prefix."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "seedsrc"})
    ctx = ProcessContext(
        process_oid=oid, process_id="seedsrc", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix=prefix,
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".grok")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "https://auth.x.ai::00000000-0000-0000-0000-000000000000": {
                "key": "fake-key",
                "refresh_token": "ORIGINAL",
                "expires_at": 9999999999,
            },
        }))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "grok-fake"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=GROK_SEED_MANIFEST, suffix=GROK_SEED_SUFFIX,
        encrypt=None,
    )


async def test_seeded_session_saves_back_and_releases_lease(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "seed_rotate")

    seed_id = await _plant_seed(mongo_db, tmp_path)
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    holders: list[str] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        holders.append(holder)
        return got

    ctx = await _make_ctx(mongo_db, "grok_lease")
    await run_grok_session(ctx, _cfg(shim_install_dir, seed_id=provider))

    # The provider was invoked with the task's process_id as the lease holder.
    assert holders == ["grok_lease"], holders

    # Save-back fired: the seed's stored auth.json carries the rotated token.
    auth = await _seed_auth(mongo_db, seed_id)
    (acct,) = auth.values()
    assert acct["refresh_token"] == "ROTATED-INSESSION", auth

    # Lease released: a fresh holder can immediately re-acquire the same seed
    # (a still-held 60s TTL lease would return None).
    regot = await seeds.acquire(
        mongo_db, prefix="test", suffix=GROK_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id


async def _wait_for(predicate, timeout: float = 60.0) -> None:
    """Poll ``predicate`` (sync or async) until it is true. The timeout only
    bounds a hang; correctness comes from the awaited condition."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_terminal(optio: Optio, process_id: str) -> dict:
    found: dict = {}

    async def _terminal() -> bool:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in ("done", "failed", "cancelled"):
            found["proc"] = proc
            return True
        return False

    await _wait_for(_terminal)
    return found["proc"]


# Spawn-heavy (engine + fake ACP grok), like the conversation-mode suite.
@pytest.mark.serial
async def test_conversation_session_renews_lease_while_live(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """Conversation mode runs the same credential watcher as iframe mode: while
    the session is live, the leased seed's lease keeps being renewed (a lease
    that is never renewed lapses after LEASE_TTL_SECONDS and the seed reads
    free, so another session could lease the same account). Teardown stops the
    watcher, then releases the lease."""
    # Short watcher period so the first renewal comes quickly; the test waits
    # for the renewal itself, never for a time window.
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)

    # Spy around the REAL watcher: records that it started (and under which
    # lease holder) and that teardown cancelled it.
    real_watcher = cred_watcher.run_credential_watcher
    watcher_events: list[tuple[str, str | None]] = []

    async def _spy_watcher(ctx, host, **kw):
        watcher_events.append(("started", kw.get("lease_holder")))
        try:
            await real_watcher(ctx, host, **kw)
        except asyncio.CancelledError:
            watcher_events.append(("cancelled", None))
            raise

    monkeypatch.setattr(cred_watcher, "run_credential_watcher", _spy_watcher)

    prefix = "gkconvlease"
    process_id = "gk-conv-lease"
    seed_id = await _plant_seed(mongo_db, tmp_path, prefix=prefix)
    await seeds.assign_to_pool(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    async def _lease() -> dict | None:
        doc = await seeds.load_seed(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
        )
        return doc.get("lease")

    acquired: list[dict] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        acquired.append(await _lease())
        return got

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    try:
        task = create_grok_task(
            process_id=process_id,
            name="Conversation lease",
            config=_cfg(
                shim_install_dir,
                mode="conversation",
                auto_start=False,
                supports_resume=False,
                seed_id=provider,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            process_id, session_id=None, timeout=60,
        )
        assert len(acquired) == 1 and acquired[0]["holder"] == process_id

        # While live: the lease, held by this task, slides past the expiry the
        # acquire set.
        async def _renewed() -> bool:
            lease = await _lease()
            return (
                lease is not None
                and lease["holder"] == process_id
                and lease["expiresAt"] > acquired[0]["expiresAt"]
            )

        await _wait_for(_renewed)
        assert watcher_events == [("started", process_id)], watcher_events

        await conv.close()
        proc = await _wait_terminal(optio, process_id)
        assert proc["status"]["state"] == "done", proc["status"]
    finally:
        await optio.shutdown(grace_seconds=1.0)

    # Teardown cancelled the watcher, then released the lease: another holder
    # can lease the same seed at once.
    await _wait_for(lambda: ("cancelled", None) in watcher_events)
    assert watcher_events == [("started", process_id), ("cancelled", None)], watcher_events
    regot = await seeds.acquire(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id
