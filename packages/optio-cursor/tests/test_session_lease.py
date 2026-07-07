"""Pooled-lease + save-back lifecycle test for optio-cursor (Stage 4, Task 2).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake cursor rotates its refreshToken mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — opencode's deliberate ordering, via grok).

Asserts the seed's stored auth.json carries the rotated token and the lease is
free again afterwards.

Adapted from optio-grok's ``test_session_lease.py`` (cursor ← grok renames;
cursor's cred file is ``home/.config/cursor/auth.json`` with a flat
``accessToken``/``refreshToken`` object, not grok's per-account map).
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
from optio_cursor import CursorTaskConfig
from optio_cursor.seed_manifest import CURSOR_SEED_MANIFEST, CURSOR_SEED_SUFFIX
from optio_cursor.session import run_cursor_session


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


def _cfg(shim_install_dir: pathlib.Path, **kw) -> CursorTaskConfig:
    return CursorTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        **kw,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    """Extract ``.config/cursor/auth.json`` from the seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".config/cursor/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(mongo_db, tmp_path) -> str:
    """Capture a seed carrying a cursor auth.json + cli-config.json via a
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
    cred_dir = os.path.join(src.workdir, "home", ".config", "cursor")
    os.makedirs(cred_dir, exist_ok=True)
    with open(os.path.join(cred_dir, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "accessToken": "fake-access-token",
            "refreshToken": "ORIGINAL",
        }))
    cfg_dir = os.path.join(src.workdir, "home", ".cursor")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "cli-config.json"), "w") as fh:
        fh.write(json.dumps({"version": 1, "editor": {"vimMode": False}}))
    return await seeds.capture_seed(
        ctx, src, manifest=CURSOR_SEED_MANIFEST, suffix=CURSOR_SEED_SUFFIX,
        encrypt=None,
    )


async def test_seeded_session_saves_back_and_releases_lease(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "seed_rotate")

    seed_id = await _plant_seed(mongo_db, tmp_path)
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    holders: list[str] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        holders.append(holder)
        return got

    ctx = await _make_ctx(mongo_db, "cursor_lease")
    await run_cursor_session(ctx, _cfg(shim_install_dir, seed_id=provider))

    # The provider was invoked with the task's process_id as the lease holder.
    assert holders == ["cursor_lease"], holders

    # Save-back fired: the seed's stored auth.json carries the rotated token.
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["refreshToken"] == "ROTATED-INSESSION", auth

    # Lease released: a fresh holder can immediately re-acquire the same seed
    # (a still-held 60s TTL lease would return None).
    regot = await seeds.acquire(
        mongo_db, prefix="test", suffix=CURSOR_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id
