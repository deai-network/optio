"""Full-cycle seed capture + consume test for optio-cursor (Stage 3, Task 2).

Proves the two halves of the seed lifecycle against fake_cursor.py:

* CAPTURE — a fresh ``seed`` session writes a fake
  ``home/.config/cursor/auth.json``; teardown captures it and fires
  ``on_seed_saved`` with a real seed id, and a seed row lands in the
  ``{prefix}_cursor_seeds`` collection.
* CONSUME — a new fresh task started with that ``seed_id`` has the stored
  identity merged into ``home/.config/cursor`` BEFORE cursor launches; the
  fake records the planted ``auth.json`` via a deliverable, so we can assert
  the seed reached the workdir before launch.

Adapted from optio-grok's ``test_session_seed.py`` (cursor ← grok renames;
cursor's cred file lives under ``.config/cursor``, not the agent home dir).
"""

from __future__ import annotations

import asyncio
import pathlib

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_cursor import CursorTaskConfig
from optio_cursor.seed_manifest import CURSOR_SEED_SUFFIX
from optio_cursor.session import run_cursor_session


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


def _cfg(shim_install_dir: pathlib.Path, **kw) -> CursorTaskConfig:
    return CursorTaskConfig(
        consumer_instructions="do the thing",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        delivery_type="audit",
        **kw,
    )


async def test_fresh_session_captures_seed(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "seed")

    saved: list[tuple[str, str | None]] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        saved.append((seed_id, info))

    ctx = await _make_ctx(mongo_db, "cursor_seed_capture", resume=False)
    await run_cursor_session(ctx, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))

    # Callback fired exactly once with a non-empty seed id (info is None in
    # Stage 3).
    assert len(saved) == 1, saved
    seed_id, info = saved[0]
    assert seed_id
    assert info is None

    # A seed row exists in the cursor seed collection, matching the callback id.
    coll = mongo_db[f"test{CURSOR_SEED_SUFFIX}"]
    assert await coll.count_documents({}) == 1
    from bson import ObjectId
    assert await coll.find_one({"_id": ObjectId(seed_id)}) is not None


async def test_seeded_fresh_session_plants_identity_before_launch(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "seed")

    # 1) Capture a seed from a fresh login session.
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cursor_seed_src", resume=False)
    await run_cursor_session(ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved))
    assert len(captured) == 1
    seed_id = captured[0]

    # 2) Consume it in a NEW fresh task. The fake cursor emits a deliverable
    #    iff home/.config/cursor/auth.json was already planted at launch
    #    (i.e. merge_seed ran before launch).
    delivered: list[str] = []

    async def on_deliverable(hook_ctx, path, text):
        delivered.append(path)

    ctx2 = await _make_ctx(mongo_db, "cursor_seed_dst", resume=False)
    await run_cursor_session(
        ctx2,
        _cfg(shim_install_dir, seed_id=seed_id, on_deliverable=on_deliverable),
    )

    assert any(p.endswith("seed_present.txt") for p in delivered), delivered
    # The workspace-trust marker must be pre-planted before launch, else real
    # cursor-agent blocks on the interactive trust gate (unattended → task dies).
    assert any(p.endswith("trust_present.txt") for p in delivered), delivered
    # CURSOR_DATA_DIR must be a short symlink into the workdir, else cursor's
    # socket/temp dir falls back to an ungranted /tmp/.cursor under claustrum.
    assert any(p.endswith("datadir_present.txt") for p in delivered), delivered


# Two DISTINCT involutions so the routing test below can assert callable
# identity: seed ops must receive the seed pair, snapshot ops the session pair.
def _rev(b: bytes) -> bytes:
    return b[::-1]


def _xor(b: bytes) -> bytes:
    return bytes(x ^ 0xFF for x in b)


async def test_seed_ops_use_seed_pair_snapshot_ops_session_pair(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """Crypto routing (BlobCryptoConfigMixin): with distinct seed_blob vs
    session_blob pairs configured, the SEED ops (capture_seed / merge_seed)
    receive the seed pair via the ``seed_encrypt``/``seed_decrypt`` accessors,
    while the resume-snapshot capture keeps the session pair."""
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "seed")

    from optio_agents import seeds as _seeds
    from optio_cursor import session as session_mod

    used: dict[str, object] = {}
    real_capture, real_merge = _seeds.capture_seed, _seeds.merge_seed
    real_snapshot = session_mod._capture_snapshot

    async def spy_capture(ctx, host, *, manifest, suffix, encrypt):
        used["capture_encrypt"] = encrypt
        return await real_capture(
            ctx, host, manifest=manifest, suffix=suffix, encrypt=encrypt,
        )

    async def spy_merge(ctx, host, *, seed_id, manifest, suffix, decrypt):
        used["merge_decrypt"] = decrypt
        return await real_merge(
            ctx, host, seed_id=seed_id, manifest=manifest, suffix=suffix,
            decrypt=decrypt,
        )

    async def spy_snapshot(ctx, host, **kw):
        used["snapshot_encrypt"] = kw.get("session_blob_encrypt")
        return await real_snapshot(ctx, host, **kw)

    monkeypatch.setattr(_seeds, "capture_seed", spy_capture)
    monkeypatch.setattr(_seeds, "merge_seed", spy_merge)
    monkeypatch.setattr(session_mod, "_capture_snapshot", spy_snapshot)

    crypto = dict(
        session_blob_encrypt=_xor, session_blob_decrypt=_xor,
        seed_blob_encrypt=_rev, seed_blob_decrypt=_rev,
    )
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cursor_route_src")
    await run_cursor_session(
        ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved, **crypto),
    )
    assert used["capture_encrypt"] is _rev      # SEED op → seed pair
    assert used["snapshot_encrypt"] is _xor     # SNAPSHOT op → session pair
    assert len(captured) == 1

    ctx2 = await _make_ctx(mongo_db, "cursor_route_dst")
    await run_cursor_session(
        ctx2, _cfg(shim_install_dir, seed_id=captured[0], **crypto),
    )
    assert used["merge_decrypt"] is _rev        # SEED op → seed pair


async def test_seeded_fresh_caller_cli_config_wins_seed_keys_survive(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """A seeded FRESH start must not lose the caller's allowed/disallowed_tools:
    cli-config.json is applied AFTER merge_seed as a read-modify-write
    deep-merge, so the caller's permission keys win over the seed's own
    cli-config.json while the seed's other keys (the fake plants
    ``editor.vimMode``) survive. Guards against the old pre-seed whole-file
    write, which the overlay-overwrite seed extraction clobbered."""
    monkeypatch.setenv("FAKE_CURSOR_SCENARIO", "seed")

    # 1) Capture a seed whose cli-config.json carries a non-permission key
    #    (fake_cursor writes {"version": 1, "editor": {"vimMode": false}}).
    captured: list[str] = []

    async def on_seed_saved(seed_id: str, info: str | None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cursor_cli_src")
    await run_cursor_session(
        ctx1, _cfg(shim_install_dir, on_seed_saved=on_seed_saved),
    )
    assert len(captured) == 1

    # 2) Consume it with caller rules set. Spy the ordering (apply must run
    #    AFTER merge_seed) and read the merged document back while the workdir
    #    still exists (teardown wipes it).
    import json as _json
    import os

    from optio_agents import seeds as _seeds
    from optio_cursor import host_actions as _ha

    order: list[str] = []
    final: dict = {}
    real_merge = _seeds.merge_seed
    real_apply = _ha.apply_cli_config

    async def spy_merge(*args, **kw):
        order.append("merge_seed")
        return await real_merge(*args, **kw)

    async def spy_apply(host, cli_config):
        order.append("apply_cli_config")
        await real_apply(host, cli_config)
        with open(os.path.join(host.workdir, "home/.cursor/cli-config.json")) as fh:
            final.update(_json.load(fh))

    monkeypatch.setattr(_seeds, "merge_seed", spy_merge)
    monkeypatch.setattr(_ha, "apply_cli_config", spy_apply)

    ctx2 = await _make_ctx(mongo_db, "cursor_cli_dst")
    await run_cursor_session(
        ctx2,
        _cfg(
            shim_install_dir, seed_id=captured[0],
            allowed_tools=["Shell(ls)"], disallowed_tools=["Shell(rm)"],
        ),
    )

    assert order == ["merge_seed", "apply_cli_config"]
    # Caller wins per key…
    assert final["permissions"]["allow"] == ["Shell(ls)"]
    assert final["permissions"]["deny"] == ["Shell(rm)"]
    assert final["approvalMode"] == "allowlist"
    # …and the seed's other cli-config keys survive the merge.
    assert final["editor"] == {"vimMode": False}
