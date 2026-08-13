"""Capture a seed, then a second fresh session consumes it."""

import asyncio
import json
import os

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_con_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id, *, resume=False):
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


async def test_second_session_consumes_seed(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")

    # 1) capture
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cc_seed_src")
    await run_claudecode_session(ctx1, ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
    ))
    seed_id = captured[0]

    # 2) consume in a DIFFERENT process; probe the planted env via before_execute
    observed = {}

    async def _probe(hook_ctx):
        wd = hook_ctx._host.workdir
        observed["creds"] = os.path.exists(f"{wd}/home/.claude/.credentials.json")
        observed["plugins"] = os.path.exists(f"{wd}/home/.claude/plugins")
        observed["projects_dir"] = os.path.exists(f"{wd}/home/.claude/projects")
        cj = await hook_ctx.read_text_from_host("home/.claude/.claude.json")
        observed["projects_key"] = list(json.loads(cj)["projects"].keys())
        observed["new_cwd"] = wd

    # the second session must NOT re-run the seed scenario's planting on top;
    # use the "happy" scenario so the planted files come purely from the seed.
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx2 = await _make_ctx(mongo_db, "cc_seed_dst")
    await run_claudecode_session(ctx2, ClaudeCodeTaskConfig(
        consumer_instructions="(seeded fresh)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_probe,
    ))

    assert observed["creds"] is True
    # plugins are not seeded since manifest v2 (re-installed on launch), so the
    # consumed seed does not bring a plugins dir.
    assert observed["plugins"] is False
    # transcript dir from the seed-source session must NOT be restored
    assert observed["projects_dir"] is False
    # .claude.json projects rekeyed to the new cwd
    assert observed["projects_key"] == [observed["new_cwd"]]


# Distinct-but-identity-valued transforms: routing assertions compare object
# identity while the seed/snapshot flow runs on unmodified bytes.
def _seed_enc(b: bytes) -> bytes:
    return b


def _seed_dec(b: bytes) -> bytes:
    return b


def _sess_enc(b: bytes) -> bytes:
    return b


def _sess_dec(b: bytes) -> bytes:
    return b


async def test_seed_ops_use_seed_pair_snapshot_ops_session_pair(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """Crypto routing: with DISTINCT seed_blob vs session_blob callables, the
    seed ops (merge_seed, run_credential_watcher, save_back_if_changed,
    capture_seed) receive the seed pair (via the seed_encrypt/seed_decrypt
    accessors) and the snapshot capture receives the session pair."""
    from optio_host.host import LocalHost

    from optio_agents import seeds as seeds_mod
    from optio_claudecode import cred_watcher as cw_mod
    from optio_claudecode import session as session_mod

    # 1) plant a seed directly (no session run): a home/.claude with valid
    # credentials (cred_fingerprint requires a non-empty refreshToken).
    plant = LocalHost(taskdir=os.path.join(task_root, "route_plant"))
    await plant.setup_workdir()
    cdir = os.path.join(plant.workdir, "home", ".claude")
    os.makedirs(cdir, exist_ok=True)
    with open(os.path.join(cdir, ".credentials.json"), "w") as fh:
        json.dump({"claudeAiOauth": {"refreshToken": "seed-token"}}, fh)
    with open(os.path.join(plant.workdir, "home", ".claude.json"), "w") as fh:
        json.dump({"projects": {"/elsewhere": {}}}, fh)
    ctx0 = await _make_ctx(mongo_db, "cc_route_plant")
    from optio_claudecode.seed_manifest import CLAUDE_SEED_MANIFEST, CLAUDE_SEED_SUFFIX
    seed_id = await seeds_mod.capture_seed(
        ctx0, plant, manifest=CLAUDE_SEED_MANIFEST, suffix=CLAUDE_SEED_SUFFIX,
        encrypt=_seed_enc,
    )

    # 2) recording wrappers — capture the callable identities, delegate to the
    # real functions so the session flow stays unmodified.
    calls: dict[str, object] = {}
    real_merge = seeds_mod.merge_seed
    real_capture = seeds_mod.capture_seed
    real_watch = cw_mod.run_credential_watcher
    real_saveback = cw_mod.save_back_if_changed

    async def _rec_merge(ctx, host, **kw):
        calls["merge_decrypt"] = kw["decrypt"]
        return await real_merge(ctx, host, **kw)

    async def _rec_capture(ctx, host, **kw):
        calls["capture_encrypt"] = kw["encrypt"]
        return await real_capture(ctx, host, **kw)

    async def _rec_watch(ctx, host, **kw):
        calls["watch_encrypt"] = kw["encrypt"]
        calls["watch_decrypt"] = kw["decrypt"]
        return await real_watch(ctx, host, **kw)

    async def _rec_saveback(ctx, host, **kw):
        calls["saveback_encrypt"] = kw["encrypt"]
        calls["saveback_decrypt"] = kw["decrypt"]
        return await real_saveback(ctx, host, **kw)

    async def _rec_snapshot(ctx, host, **kw):
        # record-only stub: the snapshot's GridFS mechanics are covered by
        # test_snapshots / test_session_blob_hooks; here only routing matters.
        calls["snapshot_encrypt"] = kw["session_blob_encrypt"]

    monkeypatch.setattr(seeds_mod, "merge_seed", _rec_merge)
    monkeypatch.setattr(seeds_mod, "capture_seed", _rec_capture)
    monkeypatch.setattr(cw_mod, "run_credential_watcher", _rec_watch)
    monkeypatch.setattr(cw_mod, "save_back_if_changed", _rec_saveback)
    monkeypatch.setattr(session_mod, "_capture_snapshot", _rec_snapshot)

    # 3) one seeded fresh session with all four transforms distinct;
    # on_seed_saved arms the teardown capture_seed, supports_resume the
    # snapshot capture.
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_ctx(mongo_db, "cc_route_run")
    await run_claudecode_session(ctx, ClaudeCodeTaskConfig(
        consumer_instructions="(crypto routing)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        seed_id=seed_id,
        on_seed_saved=lambda sid, info=None: None,
        session_blob_encrypt=_sess_enc,
        session_blob_decrypt=_sess_dec,
        seed_blob_encrypt=_seed_enc,
        seed_blob_decrypt=_seed_dec,
    ))

    # seed ops → the seed pair, never the session pair
    assert calls["merge_decrypt"] is _seed_dec
    assert calls["watch_encrypt"] is _seed_enc
    assert calls["watch_decrypt"] is _seed_dec
    assert calls["saveback_encrypt"] is _seed_enc
    assert calls["saveback_decrypt"] is _seed_dec
    assert calls["capture_encrypt"] is _seed_enc
    # snapshot op → the session pair
    assert calls["snapshot_encrypt"] is _sess_enc
