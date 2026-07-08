"""Session-level seed save-back integration test.

A seeded session whose ``auth.json`` rotates mid-run must persist the
rotated credentials back into the seed — via the in-session credential
watcher when the run outlives a poll interval, or via the teardown
backstop when it does not. With the fake binary's prompt exit (well under
the 10s default ``CRED_WATCH_INTERVAL_S``), the backstop is the path this
test proves — that is deliberate: the backstop is the load-bearing one
(opencode's own auth write-back is best-effort and the provider has
already consumed the old refresh token).

The module-local fixtures (``mongo_db``, ``task_root``, the
``_supply_scenario`` fake-opencode substitution, the ProcessContext
builder) are copied verbatim from tests/test_session_seed.py — same
rationale: self-contained module.
"""

import asyncio
import json
import os
import shlex
import sys

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_agents import seeds
from optio_opencode import OpencodeTaskConfig
from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST, OPENCODE_SEED_SUFFIX
from optio_opencode.session import run_opencode_session


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_seed_saveback_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Substitute fake_opencode.py for the real opencode binary.

    Identical to the substitution in test_session_local.py /
    test_session_resume.py — only ``--scenario <name>`` is meaningful to the
    fake; the trailing ``web --port=0 …`` from launch_opencode is harmless.
    """
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode
    holder = {"name": "happy"}

    async def _launch(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None, claustrum_wrap=None):
        del opencode_executable
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} --scenario {holder['name']}"
            ),
            hostname=hostname,
            extra_env=extra_env,
        )
    monkeypatch.setattr(host_actions, "launch_opencode", _launch)

    async def _ensure(host, **kwargs):
        return "opencode"
    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)

    async def _version(host, *, opencode_executable="opencode"):
        return None
    monkeypatch.setattr(host_actions, "opencode_version", _version)

    orig_export = host_actions.opencode_export

    async def _export(host, opencode_db_path, session_id, *, opencode_executable="opencode"):
        return await orig_export(
            host, opencode_db_path, session_id,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_export", _export)

    orig_import = host_actions.opencode_import

    async def _import(host, opencode_db_path, session_json, *, opencode_executable="opencode"):
        return await orig_import(
            host, opencode_db_path, session_json,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_import", _import)

    return holder


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


def _write_auth_hook(refresh_token: str):
    """before_execute hook factory: (re)write the isolated HOME's auth.json
    with the given refresh token (a valid multi-provider auth blob, so the
    save-back validity gate passes)."""
    auth = json.dumps({"xai": {"type": "oauth", "refresh": refresh_token}})

    async def _hook(hook_ctx) -> None:
        home = f"{hook_ctx._host.workdir.rstrip('/')}/home"
        d = f"{home}/.local/share/opencode"
        await hook_ctx._host.run_command(
            f"mkdir -p {shlex.quote(d)} && "
            f"printf '%s' {shlex.quote(auth)} > {shlex.quote(d + '/auth.json')}"
        )

    return _hook


async def _plant_seed_env(hook_ctx) -> None:
    """before_execute probe for the seed-source session: plant auth.json
    (refresh "T1") plus a model default so the capture gate (valid auth.json
    AND a model in opencode.json) passes at teardown."""
    await _write_auth_hook("T1")(hook_ctx)
    home = f"{hook_ctx._host.workdir.rstrip('/')}/home"
    d = f"{home}/.config/opencode"
    cfg = json.dumps({"model": "planted/model-0"})
    await hook_ctx._host.run_command(
        f"mkdir -p {shlex.quote(d)} && "
        f"printf '%s' {shlex.quote(cfg)} > {shlex.quote(d + '/opencode.json')}"
    )


async def test_rotation_during_session_updates_seed(
    mongo_db, task_root, _supply_scenario, tmp_path,
):
    """Seeded session; auth.json rotates mid-run; teardown backstop (or the
    watcher) persists it; assert the seed blob carries the rotated token."""
    _supply_scenario["name"] = "happy"

    # 1. Capture a seed whose auth.json has refresh "T1" and a model config.
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "oc_sb_src")
    await run_opencode_session(ctx1, OpencodeTaskConfig(
        consumer_instructions="(seed setup)", fs_isolation=False,
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
        before_execute=_plant_seed_env,
    ))
    assert len(captured) == 1
    seed_id = captured[0]

    # 2. Seeded session with a callable seed_id (exercises SeedProvider) and
    #    a before_execute that rewrites auth.json with refresh "T2".
    #    before_execute fires after merge_seed planted T1 and after the
    #    baseline was captured, so the rewrite registers as a change.
    provider_calls: list[str] = []

    async def _seed_provider(process_id: str) -> str:
        provider_calls.append(process_id)
        return seed_id

    ctx2 = await _make_ctx(mongo_db, "oc_sb_run")
    # 3. Run with the fake binary scenario that exits promptly.
    await run_opencode_session(ctx2, OpencodeTaskConfig(
        consumer_instructions="(seeded, rotating)", fs_isolation=False,
        supports_resume=False,
        seed_id=_seed_provider,
        before_execute=_write_auth_hook("T2"),
    ))
    assert provider_calls == ["oc_sb_run"]

    # 4. Merge the seed into a fresh LocalHost and assert auth.json carries
    #    the rotated token.
    dst = LocalHost(taskdir=str(tmp_path / "saveback_check"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx2, dst, seed_id=seed_id, manifest=OPENCODE_CRED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, decrypt=None,
    )
    auth_path = os.path.join(
        dst.workdir, "home", ".local", "share", "opencode", "auth.json",
    )
    with open(auth_path) as fh:
        assert "T2" in fh.read()
