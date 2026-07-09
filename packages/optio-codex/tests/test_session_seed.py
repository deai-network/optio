"""Full-cycle seed capture + consume test for optio-codex (Stage 3).

Proves the two halves of the seed lifecycle against fake_codex.py:

* CAPTURE — a fresh ``seed`` session writes a fake ``home/.codex/auth.json``;
  teardown captures it and fires ``on_seed_saved`` with a real seed id, and a
  seed row lands in the ``{prefix}_codex_seeds`` collection.
* CONSUME — a new fresh task started with that ``seed_id`` has the stored
  identity merged into ``home/.codex`` BEFORE codex launches; the fake
  records the planted ``auth.json`` via a deliverable, and FAKE_CODEX_RECORD
  proves the config.toml carried the workdir pre-trust entry at launch time.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_codex import CodexTaskConfig
from optio_codex.seed_manifest import CODEX_SEED_SUFFIX
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
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        delivery_type="audit",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        **kw,
    )


async def test_fresh_session_captures_seed(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "seed")

    saved: list[tuple[str, str | None]] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        saved.append((seed_id, info))

    ctx = await _make_ctx(mongo_db, "codex_seed_capture")
    await run_codex_session(ctx, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))

    # Callback fired exactly once with a non-empty seed id (info is None in
    # Stage 3).
    assert len(saved) == 1, saved
    seed_id, info = saved[0]
    assert seed_id
    assert info is None

    # A seed row exists in the codex seed collection, matching the callback id.
    coll = mongo_db[f"test{CODEX_SEED_SUFFIX}"]
    assert await coll.count_documents({}) == 1
    from bson import ObjectId
    doc = await coll.find_one({"_id": ObjectId(seed_id)})
    assert doc is not None
    # The capture path stamps the plural metadata.accounts (a list), never the
    # legacy singular metadata.account.
    assert isinstance(doc["metadata"]["accounts"], list)
    assert "account" not in doc["metadata"]


async def test_capture_skipped_without_valid_auth(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """The happy scenario never writes auth.json → capture_gate_ok is False
    → no capture, no callback, no seed row (a login-less identity must
    never become a seed)."""
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "happy")

    saved: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        saved.append(seed_id)

    ctx = await _make_ctx(mongo_db, "codex_seed_gate")
    await run_codex_session(ctx, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))

    assert saved == []
    coll = mongo_db[f"test{CODEX_SEED_SUFFIX}"]
    assert await coll.count_documents({}) == 0


async def test_seeded_fresh_session_plants_identity_and_trust_before_launch(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "seed")

    # 1) Capture a seed from a fresh login session.
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "codex_seed_src")
    await run_codex_session(ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))
    assert len(captured) == 1
    seed_id = captured[0]

    # 2) Consume it in a NEW fresh task. The fake codex emits a deliverable
    #    iff home/.codex/auth.json was already planted at launch (i.e.
    #    merge_seed ran before launch), and the durable record proves the
    #    pre-trust entry was in config.toml at launch time.
    record = tmp_path / "record.jsonl"          # OUTSIDE the workdir
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))

    delivered: list[str] = []

    async def on_deliverable(hook_ctx, path, text):
        delivered.append(path)

    ctx2 = await _make_ctx(mongo_db, "codex_seed_dst")
    await run_codex_session(
        ctx2,
        _cfg(shim_install_dir, seed_id=seed_id, on_deliverable=on_deliverable),
    )

    assert any(p.endswith("seed_present.txt") for p in delivered), delivered

    # Pre-trust proof: the record's config_toml (read by the fake at launch)
    # carries the [projects."<workdir>"] trust entry for THIS task's workdir.
    lines = [json.loads(l) for l in record.read_text().splitlines() if l.strip()]
    assert lines, "FAKE_CODEX_RECORD is empty — fake did not record the launch"
    config_toml = lines[-1]["config_toml"]
    assert config_toml is not None
    assert 'trust_level = "trusted"' in config_toml
    assert "[projects." in config_toml
    assert "codex_seed_dst" in config_toml     # the CONSUMER's workdir, not the source's
    # And the seed's own content survived the append-if-absent edit.
    assert 'model = "gpt-5.5"' in config_toml
