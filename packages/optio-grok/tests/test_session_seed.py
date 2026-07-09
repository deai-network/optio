"""Full-cycle seed capture + consume test for optio-grok (Stage 3, Task 2).

Proves the two halves of the seed lifecycle against fake_grok.py:

* CAPTURE — a fresh ``seed`` session writes a fake ``home/.grok/auth.json``;
  teardown captures it and fires ``on_seed_saved`` with a real seed id, and a
  seed row lands in the ``{prefix}_grok_seeds`` collection.
* CONSUME — a new fresh task started with that ``seed_id`` has the stored
  identity merged into ``home/.grok`` BEFORE grok launches; the fake records
  the planted ``auth.json`` via a deliverable, so we can assert the seed
  reached the workdir before launch.
"""

from __future__ import annotations

import asyncio
import pathlib

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_grok import GrokTaskConfig
from optio_grok.seed_manifest import GROK_SEED_SUFFIX
from optio_grok.session import run_grok_session


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
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> GrokTaskConfig:
    return GrokTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        delivery_type="audit",
        **kw,
    )


async def test_fresh_session_captures_seed(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "seed")

    # Capture-time account analysis: stub resolve_capture_account (which would
    # otherwise hit xAI/grok with the fake token) to a deterministic
    # AccountInfo, so the wiring — metadata.accounts stamp + on_seed_saved
    # summary — is asserted host- and network-free.
    from optio_agents.account import AccountInfo
    from optio_grok import session as grok_session

    info_obj = AccountInfo(
        name="Test User", email="user@example.com", plan="Grok Pro",
        account_id="00000000-0000-0000-0000-000000000001", windows=(),
    )

    captured_host = {}

    async def _fake_resolve(host):
        captured_host["workdir"] = host.workdir
        return info_obj

    monkeypatch.setattr(grok_session, "resolve_capture_account", _fake_resolve)

    saved: list[tuple[str, str | None]] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        saved.append((seed_id, info))

    ctx = await _make_ctx(mongo_db, "grok_seed_capture", resume=False)
    await run_grok_session(ctx, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))

    # Callback fired exactly once with a non-empty seed id and the resolved
    # account's human-readable summary as the 2nd arg.
    assert len(saved) == 1, saved
    seed_id, info = saved[0]
    assert seed_id
    assert info == "Plan: Grok Pro for Test User <user@example.com>"
    assert captured_host  # resolve_capture_account was called with the live host

    # A seed row exists in the grok seed collection, matching the callback id,
    # with the normalized account wrapped in the plural metadata.accounts list.
    coll = mongo_db[f"test{GROK_SEED_SUFFIX}"]
    assert await coll.count_documents({}) == 1
    from bson import ObjectId
    doc = await coll.find_one({"_id": ObjectId(seed_id)})
    assert doc is not None
    accounts = doc["metadata"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["plan"] == "Grok Pro"
    assert accounts[0]["account_id"] == "00000000-0000-0000-0000-000000000001"
    assert accounts[0]["windows"] == []  # grok: always empty


async def test_seeded_fresh_session_plants_identity_before_launch(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "seed")

    # 1) Capture a seed from a fresh login session.
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "grok_seed_src", resume=False)
    await run_grok_session(ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))
    assert len(captured) == 1
    seed_id = captured[0]

    # 2) Consume it in a NEW fresh task. The fake grok emits a deliverable iff
    #    home/.grok/auth.json was already planted at launch (i.e. merge_seed
    #    ran before launch).
    delivered: list[str] = []

    async def on_deliverable(hook_ctx, path, text):
        delivered.append(path)

    ctx2 = await _make_ctx(mongo_db, "grok_seed_dst", resume=False)
    await run_grok_session(
        ctx2,
        _cfg(shim_install_dir, seed_id=seed_id, on_deliverable=on_deliverable),
    )

    assert any(p.endswith("seed_present.txt") for p in delivered), delivered
