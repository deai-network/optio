"""Pooled-lease lifecycle test for optio-antigravity (Stage 4, Task 4.3).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake ``agy`` rotates its OAuth refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated token store back into the seed AND releases the
  lease (release AFTER save-back — opencode's deliberate ordering).

The concurrent case is the point of the task: two sessions sharing ONE pool must
each lease a distinct seed and, on completion, release it — so neither strands
the other and a later acquirer can re-lease both (a still-held 60s TTL lease
would return None).

Mirrors optio-grok's ``test_session_lease`` (grok ← agy renames; the token store
is ``.gemini/antigravity-cli/antigravity-oauth-token``, the fake scenario env var ``FAKE_AGY_SCENARIO``).
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
from optio_antigravity import AntigravityTaskConfig
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
)
from optio_antigravity.session import run_antigravity_session


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


def _cfg(shim_install_dir: pathlib.Path, **kw) -> AntigravityTaskConfig:
    return AntigravityTaskConfig(
        consumer_instructions="do the thing",
        agy_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        # The fake agy can't run under a real claustrum here.
        fs_isolation=False,
        **kw,
    )


async def _seed_token(mongo_db, seed_id: str) -> dict:
    """Extract the agy token store from the seed blob for assertions (the real
    nested store at ``.gemini/antigravity-cli/antigravity-oauth-token``)."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".gemini/antigravity-cli/antigravity-oauth-token")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(mongo_db, tmp_path, name: str) -> str:
    """Capture a seed carrying an agy token store + settings via a scratch host."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": name})
    ctx = ProcessContext(
        process_oid=oid, process_id=name, root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / name))
    await src.setup_workdir()
    gem = os.path.join(src.workdir, "home", ".gemini")
    os.makedirs(os.path.join(gem, "antigravity-cli"), exist_ok=True)
    # agy's real nested token store at antigravity-cli/antigravity-oauth-token.
    with open(os.path.join(gem, "antigravity-cli", "antigravity-oauth-token"), "w") as fh:
        fh.write(json.dumps({
            "auth_method": "consumer",
            "token": {
                "access_token": "fake-access",
                "token_type": "Bearer",
                "refresh_token": "ORIGINAL",
                "expiry": "2099-01-01T00:00:00Z",
            },
        }))
    with open(os.path.join(gem, "antigravity-cli", "settings.json"), "w") as fh:
        fh.write(json.dumps({"model": "gemini-fake"}))
    return await seeds.capture_seed(
        ctx, src, manifest=ANTIGRAVITY_SEED_MANIFEST,
        suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=None,
    )


async def test_seeded_session_saves_back_and_releases_lease(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """Single seeded session: save-back fires, then the lease is released."""
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "seed_rotate")

    seed_id = await _plant_seed(mongo_db, tmp_path, "seedsrc")
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    holders: list[str] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        holders.append(holder)
        return got

    ctx = await _make_ctx(mongo_db, "agy_lease")
    await run_antigravity_session(ctx, _cfg(shim_install_dir, seed_id=provider))

    # The provider was invoked with the task's process_id as the lease holder.
    assert holders == ["agy_lease"], holders

    # Save-back fired: the seed's stored token store carries the rotated token.
    tok = await _seed_token(mongo_db, seed_id)
    assert tok["token"]["refresh_token"] == "ROTATED-INSESSION", tok

    # Lease released: a fresh holder can immediately re-acquire the same seed
    # (a still-held 60s TTL lease would return None).
    regot = await seeds.acquire(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id


async def test_two_concurrent_sessions_share_pool_without_stranding(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """Two sessions on ONE pool each lease a distinct seed and release it, so
    neither strands the other and both are re-acquirable afterwards."""
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "seed_rotate")

    seed_a = await _plant_seed(mongo_db, tmp_path, "seedA")
    seed_b = await _plant_seed(mongo_db, tmp_path, "seedB")
    for sid in (seed_a, seed_b):
        await seeds.assign_to_pool(
            mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
            seed_id=sid, poolKey="pool1",
        )

    leased: dict[str, str] = {}

    def make_provider(tag: str):
        async def provider(holder: str) -> str:
            got = await seeds.acquire(
                mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
                poolKey="pool1", holder=holder,
            )
            assert got is not None, f"{tag}: no free seed in the pool"
            leased[tag] = got
            return got
        return provider

    async def run(process_id: str, tag: str) -> None:
        ctx = await _make_ctx(mongo_db, process_id)
        await run_antigravity_session(
            ctx, _cfg(shim_install_dir, seed_id=make_provider(tag)),
        )

    # Both sessions run at once against the shared pool.
    await asyncio.gather(run("agy_lease_a", "a"), run("agy_lease_b", "b"))

    # Each leased a DISTINCT seed — neither was starved.
    assert set(leased.values()) == {seed_a, seed_b}, leased

    # Each seed's token store carries the in-session rotation (both saved back).
    for sid in (seed_a, seed_b):
        tok = await _seed_token(mongo_db, sid)
        assert tok["token"]["refresh_token"] == "ROTATED-INSESSION", (sid, tok)

    # Both leases were released: a fresh acquirer can re-lease BOTH seeds. A
    # stranded (still-held) lease would return None on the second acquire.
    re1 = await seeds.acquire(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        poolKey="pool1", holder="reacq",
    )
    re2 = await seeds.acquire(
        mongo_db, prefix="test", suffix=ANTIGRAVITY_SEED_SUFFIX,
        poolKey="pool1", holder="reacq",
    )
    assert {re1, re2} == {seed_a, seed_b}, (re1, re2)
