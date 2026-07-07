"""Full-cycle seed capture + consume test for optio-antigravity (Stage 3).

Proves the two halves of the seed lifecycle against fake_agy.py:

* CAPTURE — a fresh ``seed`` session writes a fake agy token store; teardown
  captures it and fires ``on_seed_saved`` with a real seed id, and a seed row
  lands in the ``{prefix}_antigravity_seeds`` collection.
* CONSUME — a new fresh task started with that ``seed_id`` has the stored
  identity merged into ``home/.gemini`` BEFORE agy launches; the fake records
  the planted token store via a deliverable, so we can assert the seed reached
  the workdir before launch.

Mirrors optio-grok's test_session_seed.py — the test whose ABSENCE let the
missing seed-capture-on-teardown ship green (the demo captured 0 seeds).
"""

from __future__ import annotations

import asyncio
import pathlib

from bson import ObjectId
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_antigravity import AntigravityTaskConfig
from optio_antigravity.seed_manifest import ANTIGRAVITY_SEED_SUFFIX
from optio_antigravity.session import run_antigravity_session


async def _make_ctx(mongo_db, process_id: str, *, resume: bool = False) -> ProcessContext:
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
        cancellation_flag=asyncio.Event(), child_counter={"next": 0}, resume=resume,
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> AntigravityTaskConfig:
    return AntigravityTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        # The fake agy can't run under a real claustrum here.
        fs_isolation=False,
        **kw,
    )


async def test_fresh_session_captures_seed(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "seed")

    saved: list[tuple[str, str | None]] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        saved.append((seed_id, info))

    ctx = await _make_ctx(mongo_db, "agy_seed_capture", resume=False)
    await run_antigravity_session(ctx, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))

    # Callback fired exactly once with a non-empty seed id (info None in Stage 3).
    assert len(saved) == 1, saved
    seed_id, info = saved[0]
    assert seed_id
    assert info is None

    # A seed row exists in the antigravity seed collection, matching the id.
    coll = mongo_db[f"test{ANTIGRAVITY_SEED_SUFFIX}"]
    assert await coll.count_documents({}) == 1
    assert await coll.find_one({"_id": ObjectId(seed_id)}) is not None


async def test_seeded_fresh_session_plants_identity_before_launch(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_AGY_SCENARIO", "seed")

    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "agy_seed_src", resume=False)
    await run_antigravity_session(ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))
    assert len(captured) == 1
    seed_id = captured[0]

    # Consume it in a NEW fresh task. The fake agy emits a deliverable iff the
    # token store was already planted at launch (i.e. merge_seed ran before launch).
    delivered: list[str] = []

    async def on_deliverable(hook_ctx, path, text):
        delivered.append(path)

    ctx2 = await _make_ctx(mongo_db, "agy_seed_dst", resume=False)
    await run_antigravity_session(
        ctx2,
        _cfg(shim_install_dir, seed_id=seed_id, on_deliverable=on_deliverable),
    )

    assert any(p.endswith("seed_present.txt") for p in delivered), delivered
