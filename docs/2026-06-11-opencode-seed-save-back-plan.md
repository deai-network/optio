# Opencode Seed Save-Back Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make opencode seeds durable: an in-session watcher saves rotated `auth.json` back into the seed, and a standalone `verify_and_refresh_seed` probes a seed's liveness (and refreshes it) before the pool hands it out.

**Architecture:** Close port of `optio-claudecode`'s `cred_watcher.py` + seed/lease session wiring, plus a run-the-binary verify (challenge-answer probe, stdout-only verdict, host-free write-back via `seeds.overwrite_seed_member`). Three engine-decoupling extractions in `host_actions.py` let verify run without a `HookContext`. One new host-free helper in `optio-agents` (`plant_seed`) lets verify plant a seed without a `ProcessContext`.

**Tech Stack:** Python (asyncio, motor/MongoDB GridFS), pytest + pytest-asyncio, LocalHost/RemoteHost from optio-host, MongoDB via Docker (already running on `localhost:27017`).

**Spec:** `docs/2026-06-11-opencode-seed-save-back-design.md`

**Deliberate divergence from claudecode (do NOT "fix" to match):** claudecode's teardown releases the seed lease *before* the final credential save-back (`optio_claudecode/session.py:660-679`), leaving a tiny window where a new acquirer can merge the stale blob. This plan uses the safe order — final save-back FIRST, then lease release. (Fixing claudecode is a separate follow-up, out of scope here.)

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-agents/src/optio_agents/seeds.py` | Modify | Add host-free `plant_seed(db, host, …)`; `merge_seed` delegates to it |
| `packages/optio-agents/tests/test_seeds_plant.py` | Create | Unit test for `plant_seed` |
| `packages/optio-opencode/src/optio_opencode/seed_manifest.py` | Modify | Add `OPENCODE_CRED_MANIFEST` (auth.json only) |
| `packages/optio-opencode/src/optio_opencode/cred_watcher.py` | Create | Fingerprint + gates, `save_back_if_changed`, `run_credential_watcher` (lease-aware) |
| `packages/optio-opencode/src/optio_opencode/types.py` | Modify | `SeedProvider` alias; `seed_id: str \| SeedProvider \| None` |
| `packages/optio-opencode/src/optio_opencode/host_actions.py` | Modify | Extractions: `ensure_opencode_installed(host, download, …)`, `build_host`, `curl_downloader`, `run_opencode_probe` |
| `packages/optio-opencode/src/optio_opencode/session.py` | Modify | Callable seed_id + lease, baseline capture, watcher start/stop, teardown order, capture gate, resume cred re-merge |
| `packages/optio-opencode/src/optio_opencode/verify.py` | Create | `verify_and_refresh_seed` + probe constants |
| `packages/optio-opencode/src/optio_opencode/__init__.py` | Modify | Export `verify_and_refresh_seed`, `OPENCODE_CRED_MANIFEST`, `SeedProvider` |
| `packages/optio-opencode/tests/test_cred_watcher.py` | Create | Watcher unit tests (port of claudecode's + gates + lease-loss) |
| `packages/optio-opencode/tests/test_verify_seed.py` | Create | Verify unit tests (fake binary, verdict, write-back, stamping) |
| `packages/optio-opencode/tests/test_session_seed_saveback.py` | Create | Session-level integration: rotation during a run updates the seed |
| `packages/optio-opencode/tests/test_smart_install.py` | Modify | Adapt to new `ensure_opencode_installed` signature |
| `packages/optio-opencode/tests/test_session_local.py`, `test_session_seed.py`, `test_session_resume.py`, `test_session_hooks.py`, `test_session_remote.py` | Modify | Adapt monkeypatched `_ensure` fakes to new signature |

Run all tests from the package dir, e.g. `cd packages/optio-opencode && python -m pytest tests/ -x -q`. MongoDB must be up (Docker, `localhost:27017` — already running). If the repo uses a venv per package, activate it first (check for `.venv/`).

---

### Task 0: Feature branch

- [ ] **Step 1: Create the branch (in place, from current main)**

```bash
cd /home/csillag/deai/optio && git checkout -b feature/opencode-seed-save-back
```

Expected: `Switched to a new branch 'feature/opencode-seed-save-back'`

---

### Task 1: `seeds.plant_seed` (host-free seed planting in optio-agents)

`merge_seed(ctx, host, …)` requires a `ProcessContext`. Verify has only a `db`. Add `plant_seed(db, host, *, prefix, …)` — same extract logic, db-first (reads the blob via `AsyncIOMotorGridFSBucket(db)`, the same bucket `ctx.store_blob`/`overwrite_seed_member` use) — and make `merge_seed` delegate.

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/seeds.py` (merge_seed is at ~line 304)
- Create: `packages/optio-agents/tests/test_seeds_plant.py` (verify the tests dir name first: `ls packages/optio-agents/tests/`; if tests live elsewhere, put it next to the existing seeds tests)

- [ ] **Step 1: Write the failing test**

```python
"""plant_seed: host-free (db-first) variant of merge_seed."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_agents_plant_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


_MANIFEST = seeds.SeedManifest(home_subdir="home", include=[".cfg/file.txt"])


async def test_plant_seed_extracts_into_host(mongo_db, tmp_path):
    src = LocalHost(taskdir=str(tmp_path / "src"))
    await src.setup_workdir()
    target = os.path.join(src.workdir, "home", ".cfg")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "file.txt"), "w") as fh:
        fh.write("CONTENT-1")

    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=_MANIFEST, suffix="_t_seeds", encrypt=None,
    )

    dst = LocalHost(taskdir=str(tmp_path / "dst"))
    await dst.setup_workdir()
    # db-first: no ProcessContext
    await seeds.plant_seed(
        mongo_db, dst, prefix="test", seed_id=seed_id,
        manifest=_MANIFEST, suffix="_t_seeds", decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".cfg", "file.txt")) as fh:
        assert fh.read() == "CONTENT-1"


async def test_plant_seed_unknown_id_raises(mongo_db, tmp_path):
    dst = LocalHost(taskdir=str(tmp_path / "d2"))
    await dst.setup_workdir()
    with pytest.raises(KeyError):
        await seeds.plant_seed(
            mongo_db, dst, prefix="test", seed_id=str(ObjectId()),
            manifest=_MANIFEST, suffix="_t_seeds", decrypt=None,
        )
```

Note: if the existing optio-agents tests use a shared `tmp_workdir`/`mongo_db` conftest fixture, reuse it instead of the local fixtures above — match the neighboring test files' style.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && python -m pytest tests/test_seeds_plant.py -v`
Expected: FAIL / ERROR with `AttributeError: module 'optio_agents.seeds' has no attribute 'plant_seed'`

- [ ] **Step 3: Implement `plant_seed`; delegate `merge_seed`**

In `packages/optio-agents/src/optio_agents/seeds.py`, replace the body of `merge_seed` (keep its signature and docstring) and add `plant_seed` directly below it:

```python
async def merge_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """load doc -> load blob -> decrypt -> extract -> consume_transform.

    Raises KeyError if `seed_id` is unknown (no silent fallback). Decrypt
    failure propagates (tampering / key rotation).
    """
    await plant_seed(
        ctx._db, host, prefix=ctx._prefix, seed_id=seed_id,
        manifest=manifest, suffix=suffix, decrypt=decrypt,
    )


async def plant_seed(
    db: "AsyncIOMotorDatabase",
    host: "Host",
    *,
    prefix: str,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Host-free-engine variant of merge_seed: same load -> decrypt ->
    extract -> consume_transform, but db-first (no ProcessContext). Reads
    the blob from GridFS directly — the same bucket ctx.store_blob writes
    (cf. overwrite_seed_member). Raises KeyError if `seed_id` is unknown.
    """
    import io
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    plain = dec(buf.getvalue())
    await _extract_seed(
        host, home_subdir=manifest.home_subdir, plain=plain, include=manifest.include,
    )
    if manifest.consume_transform is not None:
        await manifest.consume_transform(host)
```

- [ ] **Step 4: Run the new test AND the existing seeds tests**

Run: `cd packages/optio-agents && python -m pytest tests/test_seeds_plant.py -v && python -m pytest tests/ -x -q -k seed`
Expected: all PASS (merge_seed delegation must not break existing capture/merge/refresh tests)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents
git commit -m "feat(optio-agents): host-free seeds.plant_seed; merge_seed delegates"
```

---

### Task 2: `OPENCODE_CRED_MANIFEST`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/seed_manifest.py` (manifest at lines 20-29)

- [ ] **Step 1: Add the credential-only manifest**

Below `OPENCODE_SEED_MANIFEST`, add:

```python
# Credential-only manifest for in-session save-back (the write-back analog
# of the full OPENCODE_SEED_MANIFEST; mirrors claudecode's
# CLAUDE_CRED_MANIFEST). Only auth.json is re-captured — the seed's
# opencode.json / plugins are never touched by save-back.
OPENCODE_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".local/share/opencode/auth.json"],
    version=OPENCODE_SEED_MANIFEST_VERSION,
    consume_transform=None,
)
```

- [ ] **Step 2: Smoke-import**

Run: `cd packages/optio-opencode && python -c "from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST; print(OPENCODE_CRED_MANIFEST.include)"`
Expected: `['.local/share/opencode/auth.json']`

- [ ] **Step 3: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/seed_manifest.py
git commit -m "feat(optio-opencode): credential-only seed manifest for save-back"
```

---

### Task 3: `cred_watcher.py` (fingerprint, gates, save-back, watcher with lease)

Port of `optio_claudecode/cred_watcher.py` (read it: `packages/optio-claudecode/src/optio_claudecode/cred_watcher.py` — 107 lines). Differences: auth path, multi-provider validity gate (no single `refreshToken` field), an extra capture gate (model required), and the opencode manifest/suffix.

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/cred_watcher.py`
- Create: `packages/optio-opencode/tests/test_cred_watcher.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-opencode/tests/test_cred_watcher.py`:

```python
"""Unit tests for the opencode credential watcher (LocalHost)."""

import asyncio
import json
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_opencode import cred_watcher
from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST, OPENCODE_SEED_SUFFIX


def _write_auth(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", ".local", "share", "opencode")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(text)


def _write_model_config(workdir: str, model: str | None) -> None:
    d = os.path.join(workdir, "home", ".config", "opencode")
    os.makedirs(d, exist_ok=True)
    cfg = {"model": model} if model is not None else {}
    with open(os.path.join(d, "opencode.json"), "w") as fh:
        fh.write(json.dumps(cfg))


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


# --- save-back gate (cred_fingerprint) ---------------------------------

async def test_fingerprint_none_when_missing(host):
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_unparseable(host):
    _write_auth(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_no_providers(host):
    _write_auth(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_changes_with_content(host):
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T2"}})
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


# --- capture gate -------------------------------------------------------

async def test_capture_gate_requires_auth_and_model(host):
    # no auth, no model
    assert not await cred_watcher.capture_gate_ok(host)
    # auth only
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T"}})
    assert not await cred_watcher.capture_gate_ok(host)
    # auth + empty model config
    _write_model_config(host.workdir, None)
    assert not await cred_watcher.capture_gate_ok(host)
    # auth + model
    _write_model_config(host.workdir, "openai/gpt-5.4-mini")
    assert await cred_watcher.capture_gate_ok(host)


# --- watcher integration (real Mongo) ------------------------------------

@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_credwatch_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_watcher_saves_back_on_change(mongo_db, host, tmp_path, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=OPENCODE_CRED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T2"}})
    try:
        for i in range(40):
            await asyncio.sleep(0.05)
            dst = LocalHost(taskdir=str(tmp_path / f"chk{i}"))
            await dst.setup_workdir()
            await seeds.merge_seed(
                ctx, dst, seed_id=seed_id, manifest=OPENCODE_CRED_MANIFEST,
                suffix=OPENCODE_SEED_SUFFIX, decrypt=None,
            )
            p = os.path.join(
                dst.workdir, "home", ".local", "share", "opencode", "auth.json",
            )
            with open(p) as fh:
                if "T2" in fh.read():
                    break
        else:
            raise AssertionError("watcher did not save back the rotated auth.json")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watcher_cancels_session_on_lease_loss(mongo_db, host, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, {"xai": {"type": "oauth", "refresh": "T1"}})
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=OPENCODE_CRED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        poolKey="pool1", holder="p",
    )
    assert got == seed_id
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="p",
    ))
    # steal the lease: release as p, re-acquire as another holder
    await seeds.release(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX,
        poolKey="pool1", holder="thief",
    )
    assert stolen == seed_id

    # watcher must notice the CAS failure and set the cancellation flag
    for _ in range(60):
        await asyncio.sleep(0.05)
        if ctx.cancellation_flag.is_set():
            break
    else:
        task.cancel()
        raise AssertionError("watcher did not flag cancellation on lease loss")
    await task  # returns (not raises): lease-loss exit is a normal return
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && python -m pytest tests/test_cred_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` for `optio_opencode.cred_watcher`

- [ ] **Step 3: Implement `cred_watcher.py`**

Create `packages/optio-opencode/src/optio_opencode/cred_watcher.py`:

```python
"""In-session credential save-back for opencode seeds.

OAuth providers with rotating refresh tokens (xAI, OpenAI/Codex) make
refresh tokens single-use: opencode's plugin loader() refreshes a token on
use, the provider rotates the refresh token, and opencode persists the
rotated pair to auth.json (best-effort). This watcher keeps the seed
current by writing the changed in-session auth.json back into the existing
seed, plus a final backstop at teardown. Provider-agnostic: opencode does
the refreshing; the watcher only persists the file.

The seed is the single source of truth for credentials; see
docs/2026-06-11-opencode-seed-save-back-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_agents import seeds
from optio_host.host import Host

from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST, OPENCODE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.local/share/opencode/auth.json"
_MODEL_RELPATH = "home/.config/opencode/opencode.json"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live auth.json, or None when it is missing,
    unparseable, or carries no provider entry (i.e. nothing worth saving
    back). The multi-provider analog of claudecode's refresh-token gate —
    guards against corrupting a seed with a half-written/logged-out file."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    return hashlib.sha256(raw).hexdigest()


async def capture_gate_ok(host: Host) -> bool:
    """Stricter gate for seed CAPTURE: valid auth.json (cred_fingerprint)
    AND a non-empty `model` in the live opencode.json. A model-less seed is
    unusable — a consuming task gets no default and verify has nothing to
    probe. Save-back deliberately does NOT use this gate: save-back only
    replaces auth.json (the seed's opencode.json is untouched), and blocking
    it over an unrelated field would drop a rotated refresh token."""
    if await cred_fingerprint(host) is None:
        return False
    path = f"{host.workdir.rstrip('/')}/{_MODEL_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        cfg = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(cfg, dict) and bool(cfg.get("model"))


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live auth.json differs from `baseline` and is valid, save it
    back into the seed and return the new fingerprint. Otherwise return
    `baseline` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=OPENCODE_CRED_MANIFEST,
            suffix=OPENCODE_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: auth.json saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: auth.json save-back failed", seed_id)
        return baseline


async def run_credential_watcher(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
    lease_holder: str | None = None,
) -> None:
    """Poll every CRED_WATCH_INTERVAL_S: save back rotated auth.json, and
    (when `lease_holder` is set) renew the seed's lease. If the lease is
    lost, signal the session to stop (set the cancellation flag) and exit —
    continuing would mean a token-rotation collision with the new holder.
    Runs until cancelled. Best-effort save-back; lease-loss is decisive."""
    current = baseline
    while True:
        await asyncio.sleep(CRED_WATCH_INTERVAL_S)
        current = await save_back_if_changed(
            ctx, host, seed_id=seed_id, baseline=current,
            encrypt=encrypt, decrypt=decrypt,
        )
        if lease_holder is not None:
            ok = await seeds.renew_lease(
                ctx._db, prefix=ctx._prefix, suffix=OPENCODE_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && python -m pytest tests/test_cred_watcher.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/cred_watcher.py packages/optio-opencode/tests/test_cred_watcher.py
git commit -m "feat(optio-opencode): credential watcher with validity gates and lease renewal"
```

---

### Task 4: `SeedProvider` + callable `seed_id` in types

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py` (seed surface at lines 56-62)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-opencode/tests/test_types.py` (read the file first; match its existing style):

```python
async def test_seed_id_accepts_callable_provider():
    from optio_opencode.types import OpencodeTaskConfig, SeedProvider  # noqa: F401

    async def provider(process_id: str) -> str:
        return "abc123"

    cfg = OpencodeTaskConfig(consumer_instructions="x", seed_id=provider)
    assert callable(cfg.seed_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-opencode && python -m pytest tests/test_types.py -v -k callable`
Expected: FAIL with `ImportError: cannot import name 'SeedProvider'`

- [ ] **Step 3: Implement**

In `types.py`: add after the `SSHConfig` import block (module level, near line 14):

```python
# Async resolver used as the callable form of ``seed_id``: receives the
# process_id, returns the seed to consume. The consuming app's resolver
# typically acquires a pooled seed lease inside (holder = process_id);
# the session then renews that lease for the lifetime of the run and
# releases it at teardown. Mirrors optio-claudecode.
SeedProvider = Callable[[str], Awaitable[str]]
```

Change `__all__` (line 16) to include `"SeedProvider"`. Change the field (line 57):

```python
    seed_id: "str | SeedProvider | None" = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-opencode && python -m pytest tests/test_types.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/types.py packages/optio-opencode/tests/test_types.py
git commit -m "feat(optio-opencode): callable SeedProvider form for seed_id"
```

---

### Task 5: host_actions extractions (engine-decoupled install, host builder, curl downloader, probe runner)

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host_actions.py` (`_install_opencode_from_zip` at 147, `ensure_opencode_installed` at 213)
- Modify: `packages/optio-opencode/src/optio_opencode/session.py:61-76` (`_build_host`), `:116-120` (ensure call site)
- Modify: `packages/optio-opencode/tests/test_smart_install.py` + the `_ensure` monkeypatch fakes in `test_session_local.py:141`, `test_session_seed.py:89`, `test_session_resume.py:71`, `test_session_hooks.py:125`, `test_session_remote.py:118`

- [ ] **Step 1: Change the two install functions to injected dependencies**

New signatures (bodies unchanged except the substitutions noted):

```python
async def _install_opencode_from_zip(
    host: "Host",
    download: "Callable[[str, str], Awaitable[None]]",
    url: str,
    *,
    install_dir: str | None = None,
) -> str:
    # body: delete `host = hook_ctx._host`; replace
    # `await hook_ctx.download_file(url, zip_path)` with
    # `await download(url, zip_path)`


async def ensure_opencode_installed(
    host: "Host",
    *,
    download: "Callable[[str, str], Awaitable[None]]",
    report_progress: "Callable | None" = None,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    # body: delete `host = hook_ctx._host`; guard both progress calls:
    # `if report_progress is not None: report_progress(None, "…")`;
    # pass-through: `_install_opencode_from_zip(host, download, url, install_dir=resolved_install_dir)`
```

Extend `ensure_opencode_installed`'s docstring with the invariant (verbatim):

```
    INVARIANT: install-dir resolution (_resolve_install_dir) runs against the
    host's REAL environment, never under _isolation_env. If the per-task
    isolation env leaked in, XDG_CACHE_HOME would point inside the (possibly
    throwaway) workdir: the binary would re-download per run and be deleted
    at teardown. The shared worker cache must stay outside every workdir.
```

- [ ] **Step 2: Add `curl_downloader` and `build_host` to host_actions.py**

```python
def curl_downloader(host: "Host") -> "Callable[[str, str], Awaitable[None]]":
    """Context-free downloader for engine-less callers (verify): fetch a URL
    to a host path via curl on the host itself, vs the engine's child-task
    download_file."""
    async def download(url: str, dest: str) -> None:
        r = await host.run_command(
            f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(dest)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"curl download failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )
    return download


def build_host(ssh, taskdir: str) -> "Host":
    """ssh_config + taskdir -> LocalHost/RemoteHost. Lifted from
    session._build_host so engine-less callers (verify) share it."""
    import os as _os
    from optio_host.host import LocalHost, RemoteHost

    if ssh is None:
        _os.makedirs(taskdir, exist_ok=True)
        host = LocalHost(taskdir=taskdir)
        _os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=ssh, taskdir=taskdir)
```

- [ ] **Step 3: Add `run_opencode_probe` to host_actions.py**

```python
async def run_opencode_probe(
    host: "Host",
    *,
    opencode_executable: str,
    model: str,
    prompt: str,
    wrap: "list[str] | None" = None,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot `opencode run` under the per-task isolation env.
    Returns (stdout, exit_code). `wrap` is an argv prefix seam (future
    claustrum fs-isolation). Plain output — the caller's verdict is a
    challenge-answer match on stdout; exit code is diagnostics only."""
    import asyncio as _asyncio

    argv = [*(wrap or []), opencode_executable, "run", "--model", model, prompt]
    cmd = " ".join(shlex.quote(a) for a in argv)
    result = await _asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=_isolation_env(host)),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)
```

- [ ] **Step 4: Adapt session.py**

`_build_host` (61-76) delegates, keeping the monkeypatch seam:

```python
def _build_host(config: OpencodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host object for the given config.

    Extracted so tests can monkeypatch ``optio_opencode.session._build_host``
    to inject a fake host without launching real subprocesses or SSH.
    Delegates to host_actions.build_host (shared with verify).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-opencode",
    )
    return host_actions.build_host(config.ssh, taskdir)
```

Ensure call site (116-120):

```python
        opencode_exec = await host_actions.ensure_opencode_installed(
            hook_ctx._host,
            download=hook_ctx.download_file,
            report_progress=hook_ctx.report_progress,
            install_if_missing=config.install_if_missing,
            install_dir=config.opencode_install_dir,
        )
```

- [ ] **Step 5: Adapt the tests**

In the five session test files, the monkeypatched fake takes the new shape — first positional param becomes the host, kwargs absorbed, e.g.:

```python
    async def _ensure(host, **kwargs):
        return shim_path  # whatever the existing fake returned
```

In `test_smart_install.py`, calls change mechanically: where a fake `hook_ctx` was passed, pass its parts instead — `ensure_opencode_installed(fake_host, download=fake_download, report_progress=None, …)` and `_install_opencode_from_zip(fake_host, fake_download, url, …)`. The existing fake hook_ctx objects already carry `_host`/`download_file` attrs; unwrap them.

- [ ] **Step 6: Run the affected suites**

Run: `cd packages/optio-opencode && python -m pytest tests/test_smart_install.py tests/test_session_local.py tests/test_session_seed.py tests/test_session_hooks.py -x -q`
Expected: all PASS (remote tests need the sshd container; skip here, covered in Task 9's full run)

- [ ] **Step 7: Commit**

```bash
git add packages/optio-opencode
git commit -m "refactor(optio-opencode): engine-decoupled install + shared host builder + probe runner"
```

---

### Task 6: session.py seed/lease/watcher wiring

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`

All edits inside `run_opencode_session` (and its closures). Mirror claudecode (`optio_claudecode/session.py` — body wiring at 278-292/394-415, teardown at 645-712) with ONE deliberate order change: final save-back BEFORE lease release (see plan header).

- [ ] **Step 1: Add state + imports**

Imports: add `from optio_opencode import cred_watcher` and `from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST` (extend the existing seed_manifest import line). State, next to `resuming = False` (~line 101):

```python
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
    resolved_seed_id: str | None = None
    lease_holder: str | None = None
```

- [ ] **Step 2: Resolve callable seed_id at the top of `_opencode_body`**

First statements of `_opencode_body` (after the `nonlocal` lines; add `nonlocal cred_baseline, cred_watch_task, resolved_seed_id, lease_holder`):

```python
        if callable(config.seed_id):
            # The provider acquires a pooled seed lease inside (holder =
            # process_id); the watcher renews it, teardown releases it.
            resolved_seed_id = await config.seed_id(ctx.process_id)
            lease_holder = ctx.process_id
        else:
            resolved_seed_id = config.seed_id
```

- [ ] **Step 3: Fresh path — use `resolved_seed_id`, capture baseline**

In the fresh branch, change `if config.seed_id is not None:` to `if resolved_seed_id is not None:` and `seed_id=config.seed_id` to `seed_id=resolved_seed_id` in the `merge_seed` call. Immediately after the `merge_seed` call (still inside the `if`), and ALSO after the whole fresh-branch block for the unseeded case, set the baseline once, after planting is done (place it right after the seed `if`-block, before the `else:`-resume comment):

```python
            cred_baseline = await cred_watcher.cred_fingerprint(host)
```

- [ ] **Step 4: Resume path — re-merge seed credentials, capture baseline**

At the top of the resume `else:` branch (before `_maybe_refresh_on_resume`):

```python
            # The seed is the source of truth for credentials; the snapshot
            # may carry a now-rotated/dead token. Overlay the seed's CURRENT
            # auth.json over the restored workdir (mirrors claudecode).
            if resolved_seed_id is not None:
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    manifest=OPENCODE_CRED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
            cred_baseline = await cred_watcher.cred_fingerprint(host)
```

- [ ] **Step 5: Start the watcher after launch**

After `ctx.report_progress(None, "opencode is live")` (~line 323):

```python
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))
```

- [ ] **Step 6: Teardown — cancel watcher, final save-back, release lease, gate capture**

In the outer `finally`, AFTER `terminate_subprocess` (~line 418) and BEFORE the `on_seed_saved` capture block, insert:

```python
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: opencode's
        # own auth write-back is best-effort (auth.set().catch(() => {})) and
        # the provider has already consumed the old refresh token; a rotation
        # in the last poll window is saved ONLY here. Runs after the
        # subprocess terminated so the on-disk auth.json is final.
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("final credential save-back failed")

        # Release AFTER the final save-back (deliberate divergence from
        # claudecode, which releases first): a new acquirer must never merge
        # the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=OPENCODE_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("lease release failed (TTL will reclaim)")
```

Then wrap the existing capture block (420-437) with the capture gate — after `_write_seed_model_config` would have run, i.e. restructure to:

```python
        if not resuming and config.on_seed_saved is not None:
            try:
                if seed_model is not None:
                    # Write the model default into the seed's opencode.json
                    # before capture so it travels in the seed.
                    await _write_seed_model_config(host, seed_model)
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: auth.json invalid/absent or no "
                        "model in opencode.json (unusable seed)",
                    )
                else:
                    seed_id_out = await _seeds.capture_seed(
                        ctx, host,
                        manifest=OPENCODE_SEED_MANIFEST,
                        suffix=OPENCODE_SEED_SUFFIX,
                        encrypt=config.session_blob_encrypt,
                    )
                    # 2nd arg: the resolved "providerID/modelID" (or None).
                    await _call_maybe_async(
                        config.on_seed_saved, seed_id_out, seed_model,
                    )
            except Exception:  # noqa: BLE001
                _LOG.exception("opencode seed capture failed; callback not fired")
```

- [ ] **Step 7: Run the session suites**

Run: `cd packages/optio-opencode && python -m pytest tests/test_session_local.py tests/test_session_seed.py tests/test_session_resume.py tests/test_session_hooks.py -x -q`
Expected: PASS, EXCEPT any existing capture test that captures without a model config — if one fails on the new gate, fix the TEST by planting a model config (`_write_seed_model_config`-shaped file) in its setup; the gate is the spec'd behavior.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests
git commit -m "feat(optio-opencode): seed save-back wiring — watcher, lease, capture gate"
```

---

### Task 7: `verify.py` + unit tests

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/verify.py`
- Create: `packages/optio-opencode/tests/test_verify_seed.py`

- [ ] **Step 1: Write the failing tests**

```python
"""verify_and_refresh_seed unit tests (fake opencode binary, real Mongo)."""

import asyncio
import json
import os
import shlex
import stat

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_opencode import host_actions
from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX
from optio_opencode.verify import verify_and_refresh_seed


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_verify_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_seed(mongo_db, tmp_path, *, model="prov/model-1"):
    """Capture a seed with auth.json (+ optional model config) via a scratch host."""
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".local", "share", "opencode")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({"xai": {"type": "oauth", "refresh": "ORIGINAL"}}))
    c = os.path.join(src.workdir, "home", ".config", "opencode")
    os.makedirs(c, exist_ok=True)
    with open(os.path.join(c, "opencode.json"), "w") as fh:
        fh.write(json.dumps({"model": model} if model else {}))
    return await seeds.capture_seed(
        ctx, src, manifest=OPENCODE_SEED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )


def _fake_opencode(tmp_path, script_body: str) -> str:
    """A fake opencode binary; `script_body` handles the `run` subcommand."""
    p = tmp_path / "opencode"
    p.write_text(f"#!/usr/bin/env bash\nif [ \"$1\" = run ]; then\n{script_body}\nfi\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def _patch_install(monkeypatch):
    """Route verify's install step to a fake binary set per-test."""
    def patch(path):
        async def _ensure(host, **kwargs):
            return path
        monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)
    return patch


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path / "tasks"))


async def _seed_auth(mongo_db, seed_id) -> dict:
    """Extract auth.json from the seed blob for assertions."""
    import io
    import tarfile
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".local/share/opencode/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def test_alive_and_writes_back_rotated_auth(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path)
    # fake: rotates auth.json (as opencode's loader would), answers the probe
    _patch_install(_fake_opencode(tmp_path, (
        '  mkdir -p "$XDG_DATA_HOME/opencode"\n'
        '  printf %s \'{"xai": {"type": "oauth", "refresh": "ROTATED"}}\' '
        '> "$XDG_DATA_HOME/opencode/auth.json"\n'
        "  printf 'The capital of France is Paris.\\n'\n"
        "  exit 0"
    )))
    result = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
    )
    assert result["alive"] is True
    assert result["model"] == "prov/model-1"
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["xai"]["refresh"] == "ROTATED"
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["metadata"]["verify"]["alive"] is True
    assert doc["status"] == "alive"


async def test_dead_on_auth_error(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(tmp_path, "  echo 'Error: Unauthorized'\n  exit 1"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["status"] == "dead"


async def test_prompt_echo_does_not_false_positive(mongo_db, tmp_path, _patch_install, task_root):
    # An error path that echoes the full prompt must NOT count as alive —
    # the challenge-answer property: the answer token is absent from the prompt.
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(
        tmp_path, '  echo "cannot process request: ${@: -1}"\n  exit 1',
    ))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False


async def test_exit_code_carries_no_verdict(mongo_db, tmp_path, _patch_install, task_root):
    # Answer present + nonzero exit -> still alive (stdout-only verdict).
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(tmp_path, "  echo 'Paris'\n  exit 3"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is True


async def test_modelless_seed_is_dead_without_probe(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path, model=None)
    marker = tmp_path / "ran"
    _patch_install(_fake_opencode(tmp_path, f"  touch {shlex.quote(str(marker))}\n  echo Paris"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False
    assert result["model"] is None
    assert not marker.exists()  # probe never ran


async def test_unknown_seed(mongo_db, task_root):
    result = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
    )
    assert result == {"alive": False, "model": None}
```

NOTE: check how `optio_host.paths.task_dir` resolves the local task root (read `packages/optio-host/src/optio_host/paths.py`). If the env var differs from `OPTIO_OPENCODE_TASK_ROOT`, fix the `task_root` fixture accordingly (the session tests' `task_root` fixture is the reference).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-opencode && python -m pytest tests/test_verify_seed.py -v`
Expected: FAIL with `ModuleNotFoundError` for `optio_opencode.verify`

- [ ] **Step 3: Implement `verify.py`**

```python
"""Standalone seed verify/refresh for opencode seeds.

Engine-free: db-first, no ProcessContext/HookContext. Runs the opencode
binary once against a throwaway workdir (option B in the design spec —
zero per-provider auth code; opencode's own loader() refreshes/rotates
tokens) and writes the refreshed auth.json back into the seed blob.

See docs/2026-06-11-opencode-seed-save-back-design.md.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from optio_agents import seeds
from optio_host.paths import task_dir

from optio_opencode import host_actions
from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# Challenge-answer probe: the answer token must NOT appear in the prompt
# (an error path that echoes/quotes the prompt can then never false-
# positive) and must be improbable in error noise (a word, not a digit).
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)

_AUTH_RELPATH = "home/.local/share/opencode/auth.json"
_AUTH_MEMBER = ".local/share/opencode/auth.json"
_CONFIG_RELPATH = "home/.config/opencode/opencode.json"


async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = OPENCODE_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> dict:
    """Verify a seed by probing its default provider; refresh + save back.

    Returns {"alive": bool, "model": str | None}. Never raises for a dead
    seed. Stamps the verdict as seed metadata and marks the seed's pool
    status (dead seeds are never handed out by seeds.acquire).

    Call only on a FREE seed, or one whose lease the caller holds: the
    probe rotates single-use refresh tokens, so verifying a seed in use by
    a live session leaves that session's next refresh stranded (and its
    save-back would clobber this one). The caller owns the lease
    discipline; this function does not acquire or check leases.

    Run on a host whose environment carries no provider API keys —
    inherited env vars could mask a dead seed.
    """
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "model": None}

    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-opencode",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    alive = False
    model: str | None = None
    try:
        await host.setup_workdir()
        opencode_exec = await host_actions.ensure_opencode_installed(
            host,
            download=host_actions.curl_downloader(host),
            report_progress=None,
            install_dir=install_dir,
        )
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=OPENCODE_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )

        # Default-provider model: small_model if set, else model. Must stay
        # on the DEFAULT provider — that is whose token a seed-pinned task
        # will drive (and whose liveness we are certifying).
        workdir = host.workdir.rstrip("/")
        try:
            raw = await host.fetch_bytes_from_host(f"{workdir}/{_CONFIG_RELPATH}")
            cfg = json.loads(raw.decode("utf-8"))
            if isinstance(cfg, dict):
                model = cfg.get("small_model") or cfg.get("model") or None
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            model = None
        if not model:
            _LOG.warning("seed %s: no model in opencode.json; unusable -> dead", seed_id)
        else:
            stdout, exit_code = await host_actions.run_opencode_probe(
                host, opencode_executable=opencode_exec,
                model=model, prompt=PROBE_PROMPT,
            )
            # Verdict: stdout-only. The exit code carries zero verdict bits
            # (answer present proves the full chain regardless; requiring
            # exit 0 would only add a false-dead path) — diagnostics only.
            alive = PROBE_ANSWER_RE.search(stdout) is not None
            if not alive:
                _LOG.info(
                    "seed %s: probe dead (exit=%s, stdout[:200]=%r)",
                    seed_id, exit_code, stdout[:200],
                )

            # Write back the (possibly refreshed/rotated) auth.json — valid
            # files only (same validity bar as the watcher's save-back gate).
            try:
                auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
                auth = json.loads(auth_raw.decode("utf-8"))
                if isinstance(auth, dict) and auth:
                    await seeds.overwrite_seed_member(
                        db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                        member_path=_AUTH_MEMBER, content=auth_raw,
                        encrypt=encrypt, decrypt=decrypt,
                    )
            except (FileNotFoundError, ValueError, UnicodeDecodeError):
                _LOG.warning("seed %s: no valid auth.json after probe; skipping write-back", seed_id)

        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {
                "alive": alive,
                "checkedAt": datetime.now(timezone.utc),
                "probedModel": model,
            }},
        )
        await seeds.mark_seed_status(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            status="alive" if alive else "dead",
        )
        return {"alive": alive, "model": model}
    finally:
        try:
            await host.cleanup_taskdir(aggressive=True)
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: host.disconnect failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-opencode && python -m pytest tests/test_verify_seed.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/verify.py packages/optio-opencode/tests/test_verify_seed.py
git commit -m "feat(optio-opencode): verify_and_refresh_seed (challenge-answer probe + write-back)"
```

---

### Task 8: Session-level save-back integration test

**Files:**
- Create: `packages/optio-opencode/tests/test_session_seed_saveback.py`

- [ ] **Step 1: Write the test**

Copy the module-local fixtures from `tests/test_session_seed.py` verbatim (`mongo_db`, `task_root`, the `_supply_scenario` fake-opencode substitution, the ProcessContext builder — same rationale: self-contained module). Then:

```python
async def test_rotation_during_session_updates_seed(mongo_db, task_root, ...):
    """Seeded session; auth.json rotates mid-run; teardown backstop (or the
    watcher) persists it; assert the seed blob carries the rotated token."""
    # 1. Capture a seed whose auth.json has refresh "T1" and a model config
    #    (reuse test_session_seed.py's planting approach: before_execute hook
    #    writes the files under <workdir>/home).
    # 2. Build OpencodeTaskConfig with:
    #      seed_id=<async callable returning the seed_id>   # exercises SeedProvider
    #      before_execute=<hook that rewrites auth.json with refresh "T2">
    #    (before_execute fires after merge_seed planted T1 and after the
    #    baseline was captured, so the rewrite registers as a change).
    # 3. run_opencode_session(ctx, config) with the fake binary scenario
    #    that exits promptly (same scenario test_session_seed.py uses).
    # 4. After the session: merge the seed into a fresh LocalHost and assert
    #    auth.json contains "T2" (use the OPENCODE_CRED_MANIFEST merge +
    #    file-read assertion pattern from test_cred_watcher.py).
```

Write the real code following those numbered steps — every referenced pattern exists in the two named files; no new machinery. Key assertion at the end:

```python
    with open(auth_path) as fh:
        assert "T2" in fh.read()
```

- [ ] **Step 2: Run it**

Run: `cd packages/optio-opencode && python -m pytest tests/test_session_seed_saveback.py -v`
Expected: PASS. If the fake-opencode scenario exits before one watcher interval (10s default is long for tests), the teardown backstop covers it — that is part of what this test proves. Do NOT shrink the interval here; the backstop path is the load-bearing one.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-opencode/tests/test_session_seed_saveback.py
git commit -m "test(optio-opencode): session-level seed save-back integration"
```

---

### Task 9: Exports, full suite, spec cross-check

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/__init__.py`

- [ ] **Step 1: Export the new public surface**

Add to the existing import-and-`__all__` blocks:

```python
from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST  # extend existing import
from optio_opencode.types import SeedProvider  # extend existing import
from optio_opencode.verify import verify_and_refresh_seed
```

and append `"OPENCODE_CRED_MANIFEST"`, `"SeedProvider"`, `"verify_and_refresh_seed"` to `__all__`.

- [ ] **Step 2: Full test runs (both touched packages)**

```bash
cd packages/optio-agents && python -m pytest tests/ -q
cd ../optio-opencode && python -m pytest tests/ -q
```

Expected: PASS. Known flake: optio-core `test_cancel_shared_deadline_across_subtree` (unrelated, pre-existing) — re-run before suspecting a regression. Remote-host tests need the sshd Docker container (`tests/docker-compose.sshd.yml`); start it if they error on connection.

- [ ] **Step 3: Spec coverage check**

Confirm against `docs/2026-06-11-opencode-seed-save-back-design.md`: watcher (Component 1) ✓ Tasks 2/3/6 · verify (Component 2) ✓ Tasks 1/5/7 · extractions + invariant ✓ Task 5 · gates ✓ Tasks 3/6 · leases ✓ Tasks 3/4/6 · verdict persistence ✓ Task 7 · wrap seam ✓ Task 5 (`run_opencode_probe(wrap=…)`).

- [ ] **Step 4: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/__init__.py
git commit -m "feat(optio-opencode): export seed save-back public surface"
```

