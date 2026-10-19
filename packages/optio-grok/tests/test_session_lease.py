"""Pooled-lease + save-back lifecycle test for optio-grok (Stage 4, Task 2).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake grok rotates its refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — opencode's deliberate ordering).

Asserts the seed's stored auth.json carries the rotated token and the lease is
free again afterwards.

The conversation-mode test runs the same seeded, leased session headless and
asserts the credential watcher keeps renewing the lease while the session is
live, and that teardown stops the watcher and releases the lease.

The resume tests run a seeded task twice, fresh then resumed, with a fresh
seed added in between (the owner's case: a new account plan, a new seed). The
resumed launch resolves the seed again, leased under the task's process_id,
and overlays the seed's current auth.json onto the restored snapshot, keeping
the snapshot's config.toml, as optio-claudecode does on resume.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import pathlib
import tarfile
import time as _time

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_grok import GrokTaskConfig, create_grok_task, cred_watcher
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
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
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
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


async def _seed_auth(mongo_db, seed_id: str, *, prefix: str = "test") -> dict:
    """Extract ``.grok/auth.json`` from the seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".grok/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(
    mongo_db, tmp_path, *, prefix: str = "test", src_name: str = "seedsrc",
    key: str = "fake-key", refresh_token: str = "ORIGINAL",
    model: str = "grok-fake",
) -> str:
    """Capture a seed carrying a grok auth.json + config.toml via a scratch host.

    ``prefix`` names the seed collection; the scratch process doc stays in
    ``test_processes``, outside the collection of an engine run under another
    prefix. ``src_name`` names the scratch process and its directory (distinct
    per seed when a test plants several); ``key`` / ``refresh_token`` /
    ``model`` tell one seed's files from another's."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": src_name})
    ctx = ProcessContext(
        process_oid=oid, process_id=src_name, root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix=prefix,
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / src_name))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".grok")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "https://auth.x.ai::00000000-0000-0000-0000-000000000000": {
                "key": key,
                "refresh_token": refresh_token,
                "expires_at": 9999999999,
            },
        }))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write(f'model = "{model}"\n')
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


async def _wait_for(predicate, timeout: float = 60.0) -> None:
    """Poll ``predicate`` (sync or async) until it is true. The timeout only
    bounds a hang; correctness comes from the awaited condition."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_terminal(optio: Optio, process_id: str) -> dict:
    found: dict = {}

    async def _terminal() -> bool:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in ("done", "failed", "cancelled"):
            found["proc"] = proc
            return True
        return False

    await _wait_for(_terminal)
    return found["proc"]


# Spawn-heavy (engine + fake ACP grok), like the conversation-mode suite.
@pytest.mark.serial
async def test_conversation_session_renews_lease_while_live(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """Conversation mode runs the same credential watcher as iframe mode: while
    the session is live, the leased seed's lease keeps being renewed (a lease
    that is never renewed lapses after LEASE_TTL_SECONDS and the seed reads
    free, so another session could lease the same account). Teardown stops the
    watcher, then releases the lease."""
    # Short watcher period so the first renewal comes quickly; the test waits
    # for the renewal itself, never for a time window.
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)

    # Spy around the REAL watcher: records that it started (and under which
    # lease holder) and that teardown cancelled it.
    real_watcher = cred_watcher.run_credential_watcher
    watcher_events: list[tuple[str, str | None]] = []

    async def _spy_watcher(ctx, host, **kw):
        watcher_events.append(("started", kw.get("lease_holder")))
        try:
            await real_watcher(ctx, host, **kw)
        except asyncio.CancelledError:
            watcher_events.append(("cancelled", None))
            raise

    monkeypatch.setattr(cred_watcher, "run_credential_watcher", _spy_watcher)

    prefix = "gkconvlease"
    process_id = "gk-conv-lease"
    seed_id = await _plant_seed(mongo_db, tmp_path, prefix=prefix)
    await seeds.assign_to_pool(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    async def _lease() -> dict | None:
        doc = await seeds.load_seed(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
        )
        return doc.get("lease")

    acquired: list[dict] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        acquired.append(await _lease())
        return got

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    try:
        task = create_grok_task(
            process_id=process_id,
            name="Conversation lease",
            config=_cfg(
                shim_install_dir,
                mode="conversation",
                auto_start=False,
                supports_resume=False,
                seed_id=provider,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            process_id, session_id=None, timeout=60,
        )
        assert len(acquired) == 1 and acquired[0]["holder"] == process_id

        # While live: the lease, held by this task, slides past the expiry the
        # acquire set.
        async def _renewed() -> bool:
            lease = await _lease()
            return (
                lease is not None
                and lease["holder"] == process_id
                and lease["expiresAt"] > acquired[0]["expiresAt"]
            )

        await _wait_for(_renewed)
        assert watcher_events == [("started", process_id)], watcher_events

        await conv.close()
        proc = await _wait_terminal(optio, process_id)
        assert proc["status"]["state"] == "done", proc["status"]
    finally:
        await optio.shutdown(grace_seconds=1.0)

    # Teardown cancelled the watcher, then released the lease: another holder
    # can lease the same seed at once.
    await _wait_for(lambda: ("cancelled", None) in watcher_events)
    assert watcher_events == [("started", process_id), ("cancelled", None)], watcher_events
    regot = await seeds.acquire(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id


# --- resume: the seed, not the snapshot, carries the credentials ------------


def _launches(record: pathlib.Path) -> list[dict]:
    """The fake grok's launch records: argv plus the GROK_HOME files at launch."""
    return [json.loads(line) for line in record.read_text().splitlines() if line.strip()]


def _account(auth_json: str) -> dict:
    """The single account entry of an auth.json text."""
    (acct,) = json.loads(auth_json).values()
    return acct


async def _old_and_new_seeds(mongo_db, tmp_path, *, prefix: str = "test") -> tuple[str, str]:
    """The seed the first run uses (pool "old") and a fresh seed added before
    the resume (pool "new"). They differ in every file: auth.json key and
    refresh token, config.toml model."""
    old = await _plant_seed(
        mongo_db, tmp_path, prefix=prefix, src_name="oldsrc",
        key="old-key", refresh_token="OLD", model="grok-old",
    )
    new = await _plant_seed(
        mongo_db, tmp_path, prefix=prefix, src_name="newsrc",
        key="new-key", refresh_token="NEW", model="grok-new",
    )
    for seed_id, pool in ((old, "old"), (new, "new")):
        await seeds.assign_to_pool(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
            seed_id=seed_id, poolKey=pool,
        )
    return old, new


def _pool_provider(mongo_db, *, prefix: str = "test"):
    """A SeedProvider leasing from the pool named by ``state["pool"]`` (the
    test switches it between the runs). ``state["leases"]`` records
    ``(holder, seed_id, lease)`` per call."""
    state: dict = {"pool": "old", "leases": []}

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
            poolKey=state["pool"], holder=holder,
        )
        assert got is not None, f"provider could not lease from {state['pool']!r}"
        doc = await seeds.load_seed(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX, seed_id=got,
        )
        state["leases"].append((holder, got, doc.get("lease")))
        return got

    return provider, state


def _spy_on_watcher(monkeypatch) -> list[tuple]:
    """Wrap the real credential watcher. Returns its events:
    ``("started", seed_id, lease_holder)`` and ``("cancelled",)``. On start it
    creates the ``FAKE_GROK_HOLD_UNTIL`` file when one is set, releasing a
    fake grok that holds its DONE until the watcher runs."""
    real = cred_watcher.run_credential_watcher
    events: list[tuple] = []

    async def _spy(ctx, host, **kw):
        events.append(("started", kw.get("seed_id"), kw.get("lease_holder")))
        hold = os.environ.get("FAKE_GROK_HOLD_UNTIL")
        if hold:
            pathlib.Path(hold).touch()
        try:
            await real(ctx, host, **kw)
        except asyncio.CancelledError:
            events.append(("cancelled",))
            raise

    monkeypatch.setattr(cred_watcher, "run_credential_watcher", _spy)
    return events


async def _assert_free(mongo_db, seed_id: str, pool: str, *, prefix: str = "test") -> None:
    """The seed's lease was released: another holder leases it at once."""
    regot = await seeds.acquire(
        mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX,
        poolKey=pool, holder="other",
    )
    assert regot == seed_id, (pool, regot)


async def test_resumed_session_takes_its_credentials_from_the_seed(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """iframe resume with a SeedProvider: the resumed launch resolves the seed
    again (leased under the task's process_id) and overlays the seed's current
    auth.json onto the restored snapshot before grok starts with -c; the
    snapshot's config.toml stays. The watcher runs, the token the resumed grok
    rotates is saved back into that seed, and teardown releases its lease."""
    record = tmp_path / "launches.jsonl"
    monkeypatch.setenv("FAKE_GROK_RECORD", str(record))
    old_seed, new_seed = await _old_and_new_seeds(mongo_db, tmp_path)
    provider, state = _pool_provider(mongo_db)
    watcher_events = _spy_on_watcher(monkeypatch)
    pid = "grok_resume_seed"
    cfg = _cfg(shim_install_dir, seed_id=provider)

    # Fresh run on the old seed; its snapshot carries the old auth.json and
    # the old config.toml.
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")
    await run_grok_session(await _make_ctx(mongo_db, pid), cfg)
    await _assert_free(mongo_db, old_seed, "old")
    watcher_events.clear()

    # A fresh seed replaces the old one; the resumed grok rotates its token
    # and holds its DONE until the watcher has started.
    state["pool"] = "new"
    monkeypatch.setenv("FAKE_GROK_SCENARIO", "seed_rotate")
    monkeypatch.setenv("FAKE_GROK_HOLD_UNTIL", str(tmp_path / "watcher-started"))
    await run_grok_session(await _make_ctx(mongo_db, pid, resume=True), cfg)

    # The resumed launch leased the new seed under the task's process_id.
    assert [(h, s) for h, s, _ in state["leases"]] == [
        (pid, old_seed), (pid, new_seed),
    ], state["leases"]

    # What grok found at the resumed launch (with -c): the new seed's
    # auth.json, the snapshot's config.toml.
    fresh, resumed = _launches(record)
    assert "-c" not in fresh["argv"] and "-c" in resumed["argv"], resumed["argv"]
    assert _account(fresh["auth_json"])["key"] == "old-key"
    acct = _account(resumed["auth_json"])
    assert (acct["key"], acct["refresh_token"]) == ("new-key", "NEW"), acct
    assert 'model = "grok-old"' in resumed["config_toml"], resumed["config_toml"]
    assert "grok-new" not in resumed["config_toml"], resumed["config_toml"]

    # The watcher ran on the resumed session, on the new seed and its lease;
    # teardown stopped it.
    assert watcher_events == [("started", new_seed, pid), ("cancelled",)], watcher_events

    # The rotated token went back into the new seed; the old seed is untouched.
    (new_acct,) = (await _seed_auth(mongo_db, new_seed)).values()
    assert (new_acct["key"], new_acct["refresh_token"]) == (
        "new-key", "ROTATED-INSESSION",
    ), new_acct
    (old_acct,) = (await _seed_auth(mongo_db, old_seed)).values()
    assert (old_acct["key"], old_acct["refresh_token"]) == ("old-key", "OLD"), old_acct

    await _assert_free(mongo_db, new_seed, "new")


async def test_failed_overlay_on_resume_keeps_the_snapshot_token_out_of_the_seed(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """When the credential overlay fails on resume, the launch fails, the
    restored (old) auth.json is NOT saved back into the seed just leased, and
    that seed's lease is released."""
    old_seed, new_seed = await _old_and_new_seeds(mongo_db, tmp_path)
    provider, state = _pool_provider(mongo_db)
    pid = "grok_resume_seed_fail"
    cfg = _cfg(shim_install_dir, seed_id=provider)

    monkeypatch.setenv("FAKE_GROK_SCENARIO", "happy")
    await run_grok_session(await _make_ctx(mongo_db, pid), cfg)

    from optio_grok import session as grok_session

    async def _failing_merge(ctx, host, **kw):
        raise RuntimeError("overlay failed")

    monkeypatch.setattr(grok_session._seeds, "merge_seed", _failing_merge)
    state["pool"] = "new"
    with pytest.raises(RuntimeError, match="overlay failed"):
        await run_grok_session(await _make_ctx(mongo_db, pid, resume=True), cfg)

    assert [(h, s) for h, s, _ in state["leases"]][-1] == (pid, new_seed)
    (new_acct,) = (await _seed_auth(mongo_db, new_seed)).values()
    assert (new_acct["key"], new_acct["refresh_token"]) == ("new-key", "NEW"), new_acct
    await _assert_free(mongo_db, new_seed, "new")


async def _wait_result_retired(optio: Optio, process_id: str) -> None:
    """Before relaunching a process id, wait until the finished run's
    published result is cleared, so its teardown cannot clobber the next
    run's result future (see test_session_conversation)."""
    await _wait_for(lambda: optio.get_published_result(process_id) is None)


# Spawn-heavy (engine + fake ACP grok), like the conversation-mode suite.
@pytest.mark.serial
async def test_resumed_conversation_session_takes_its_credentials_from_the_seed(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    """Conversation mode, resumed with a SeedProvider: the resumed launch
    leases the new seed under the task's process_id, grok starts on the new
    seed's auth.json and the snapshot's config.toml, session/load replays the
    stored ACP session, the watcher renews the new lease while the session is
    live, and teardown stops it and releases the lease."""
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    record = tmp_path / "launches.jsonl"
    acp_record = tmp_path / "acp.jsonl"
    monkeypatch.setenv("FAKE_GROK_RECORD", str(record))
    monkeypatch.setenv("FAKE_GROK_ACP_RECORD", str(acp_record))

    prefix = "gkconvresseed"
    process_id = "gk-conv-resume-seed"
    old_seed, new_seed = await _old_and_new_seeds(mongo_db, tmp_path, prefix=prefix)
    provider, state = _pool_provider(mongo_db, prefix=prefix)
    watcher_events = _spy_on_watcher(monkeypatch)

    async def _lease(seed_id: str) -> dict | None:
        doc = await seeds.load_seed(
            mongo_db, prefix=prefix, suffix=GROK_SEED_SUFFIX, seed_id=seed_id,
        )
        return doc.get("lease")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    try:
        task = create_grok_task(
            process_id=process_id,
            name="Conversation resume seed",
            config=_cfg(
                shim_install_dir,
                mode="conversation",
                conversation_ui=True,
                auto_start=False,
                supports_resume=True,
                seed_id=provider,
            ),
        )
        await optio.adhoc_define(task)

        conv = await optio.launch_and_await_result(
            process_id, session_id=None, timeout=60,
        )
        await conv.send("hello")
        await conv.close()
        assert (await _wait_terminal(optio, process_id))["status"]["state"] == "done"
        await _wait_result_retired(optio, process_id)
        await _assert_free(mongo_db, old_seed, "old", prefix=prefix)
        watcher_events.clear()

        state["pool"] = "new"
        conv2 = await optio.launch_and_await_result(
            process_id, resume=True, session_id=None, timeout=60,
        )
        assert [(h, s) for h, s, _ in state["leases"]] == [
            (process_id, old_seed), (process_id, new_seed),
        ], state["leases"]
        acquired = state["leases"][-1][2]

        # While live: the watcher runs on the new seed and slides its lease.
        async def _renewed() -> bool:
            lease = await _lease(new_seed)
            return (
                lease is not None
                and lease["holder"] == process_id
                and lease["expiresAt"] > acquired["expiresAt"]
            )

        await _wait_for(_renewed)
        assert watcher_events == [("started", new_seed, process_id)], watcher_events

        await conv2.close()
        assert (await _wait_terminal(optio, process_id))["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)

    # session/load replayed the ACP session the snapshot stored.
    loads = [json.loads(line)["session_load"] for line in acp_record.read_text().splitlines() if line.strip()]
    assert loads == ["fake-grok-session"], loads

    # What grok found at each launch.
    fresh, resumed = _launches(record)
    assert _account(fresh["auth_json"])["key"] == "old-key"
    acct = _account(resumed["auth_json"])
    assert (acct["key"], acct["refresh_token"]) == ("new-key", "NEW"), acct
    assert 'model = "grok-old"' in resumed["config_toml"], resumed["config_toml"]
    assert "grok-new" not in resumed["config_toml"], resumed["config_toml"]

    # Teardown stopped the watcher, then released the new seed's lease.
    await _wait_for(lambda: ("cancelled",) in watcher_events)
    assert watcher_events == [("started", new_seed, process_id), ("cancelled",)], watcher_events
    await _assert_free(mongo_db, new_seed, "new", prefix=prefix)
