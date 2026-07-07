"""Pooled-lease + save-back lifecycle test for optio-codex (Stage 4).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake codex rotates its refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — the deliberate ordering ported from
  grok/opencode).

Asserts the seed's stored auth.json carries the rotated token and the lease
is free again afterwards.
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
from optio_codex import CodexTaskConfig
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.session import run_codex_session


async def _make_ctx(mongo_db, process_id: str) -> ProcessContext:
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        **kw,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    """Extract ``.codex/auth.json`` from the seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".codex/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(mongo_db, tmp_path) -> str:
    """Capture a seed carrying a codex auth.json + config.toml via a
    scratch host."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "seedsrc"})
    ctx = ProcessContext(
        process_oid=oid, process_id="seedsrc", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": "fake-id", "access_token": "fake-access",
                "refresh_token": "ORIGINAL",
            },
            "last_refresh": "2026-07-02T00:00:00Z",
        }))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )


async def test_seeded_session_saves_back_and_releases_lease(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "seed_rotate")

    seed_id = await _plant_seed(mongo_db, tmp_path)
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    holders: list[str] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        holders.append(holder)
        return got

    ctx = await _make_ctx(mongo_db, "codex_lease")
    await run_codex_session(ctx, _cfg(shim_install_dir, seed_id=provider))

    # The provider was invoked with the task's process_id as the lease holder.
    assert holders == ["codex_lease"], holders

    # Save-back fired: the seed's stored auth.json carries the rotated token.
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["refresh_token"] == "ROTATED-INSESSION", auth

    # Lease released: a fresh holder can immediately re-acquire the same
    # seed (a still-held 60s TTL lease would return None).
    regot = await seeds.acquire(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id
