"""Pooled-lease + save-back lifecycle test for optio-grok (Stage 4, Task 2).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake grok rotates its refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — opencode's deliberate ordering).

Asserts the seed's stored auth.json carries the rotated token and the lease is
free again afterwards.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import tarfile

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_grok import GrokTaskConfig
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
        grok_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
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


async def _plant_seed(mongo_db, tmp_path) -> str:
    """Capture a seed carrying a grok auth.json + config.toml via a scratch host."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "seedsrc"})
    ctx = ProcessContext(
        process_oid=oid, process_id="seedsrc", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
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
