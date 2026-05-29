# Seed Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "seeded environment, fresh conversation" support — start a fresh, logged-in, configured claude session from a stored seed, and capture such a seed from an interactively-configured session.

**Architecture:** A generic tar/encrypt/store/load/extract engine lives in `optio_host/seeds.py`, parameterized by a `SeedManifest` (HOME layout + include list + consume-time transform). `optio-claudecode` is the first adopter: it defines `CLAUDE_SEED_MANIFEST`, wires `capture_seed`/`merge_seed` into the session finally/body brackets, adds two config fields, and re-exports thin GC wrappers. The demo drives the full lifecycle via baked-at-creation `seed_id` + in-process `resync`.

**Tech Stack:** Python 3, `dataclass`, motor (async MongoDB) + GridFS, `tar`/`gzip` over the Host abstraction, pytest + pytest-asyncio, MongoDB-via-Docker.

**Source spec:** `docs/2026-05-29-optio-claudecode-seed-design.md`

---

## Plan-level notes (deviations from spec, resolved)

These are deliberate, minor concretizations made while mapping the spec onto the existing code. They do not change the spec's intent.

1. **`SeedManifest` gains a `version: int` field.** The spec passes `manifest_version` to `insert_seed` but `capture_seed(ctx, host, *, manifest, suffix, encrypt)` has no version param. The cleanest source of that value is the manifest itself, so `SeedManifest` carries `version`, and `capture_seed` reads `manifest.version`.
2. **`optio_host/seeds.py` may import `optio_core` / `bson` / `motor`.** Confirmed with the user: optio-host already depends on optio-core. We mirror the existing `optio_host/context.py` convention (local `from bson import ObjectId`, `TYPE_CHECKING` for motor types) to avoid a hard top-level dep, but this is style, not a constraint.
3. **Seed-blob tar members are relative to `home_subdir`** (e.g. `.claude.json`, `.claude/.credentials.json`), extracted with `tar -C <workdir>/<home_subdir>`. This differs from the snapshot tar (which keeps the `home/` prefix). Capture and merge are symmetric, so the difference is internal to the engine.
4. **Demo keeps the existing `claudecode-demo` task** (the `ANTHROPIC_API_KEY`/`credentials_json` one) and *adds* the setup task + per-seed tasks. The seed feature is additive to the demo, matching the spec's "Demo usage" section.
5. **The demo's setup task uses `supports_resume=False`.** A setup session has nothing to resume; disabling snapshotting keeps it clean. Seed capture runs *before* the snapshot bracket regardless, so this does not affect capture.

---

## File Structure

**Created:**
- `packages/optio-host/src/optio_host/seeds.py` — generic engine: `SeedManifest`, Mongo helpers (`insert_seed`/`load_seed`/`delete_seed`/`list_seeds`), `capture_seed`/`merge_seed`, internal tar helpers.
- `packages/optio-host/tests/test_seeds.py` — engine + Mongo-helper tests against a fake manifest + a local host.
- `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py` — `CLAUDE_SEED_MANIFEST`, `CLAUDE_SEED_SUFFIX`, `CLAUDE_SEED_MANIFEST_VERSION`, `_rekey_claude_json_projects`, and `delete_seed`/`list_seeds` ergonomic wrappers.
- `packages/optio-claudecode/tests/test_session_seed_capture.py`
- `packages/optio-claudecode/tests/test_session_seed_consume.py`
- `packages/optio-claudecode/tests/test_session_seed_unknown_id.py`
- `packages/optio-claudecode/tests/test_seed_config.py`

**Modified:**
- `packages/optio-host/tests/conftest.py` — add a `mongo_db` fixture (engine tests need Mongo + GridFS).
- `packages/optio-claudecode/src/optio_claudecode/types.py` — add `seed_id`, `on_seed_saved`.
- `packages/optio-claudecode/src/optio_claudecode/session.py` — `_call_maybe_async`, `_has_transcript`, seeded-fresh merge wiring, seed-capture wiring, D3 no-transcript safety, resume-ignores-seed logging.
- `packages/optio-claudecode/src/optio_claudecode/__init__.py` — export `delete_seed`, `list_seeds`, `CLAUDE_SEED_MANIFEST`, `CLAUDE_SEED_SUFFIX`.
- `packages/optio-claudecode/tests/fake_claude.py` — new `seed` scenario that plants INCLUDE + EXCLUDE files under `$HOME/.claude` and a `$HOME/.claude.json` with a `projects` map keyed to the run's cwd.
- `packages/optio-claudecode/tests/test_session_resume.py` — extend with the D3 (no-transcript → no `--continue`) test.
- `packages/optio-demo/src/optio_demo/__main__.py` — expose `db` + `prefix` in `services`.
- `packages/optio-demo/src/optio_demo/tasks/__init__.py` — `await` the now-async claudecode generator, passing `services`.
- `packages/optio-demo/src/optio_demo/tasks/claudecode.py` — async `get_tasks(services)`: setup task with `on_seed_saved`, per-seed task generation, demo registry collection.

---

## Task 1: Engine — `SeedManifest` + Mongo helpers

**Files:**
- Create: `packages/optio-host/src/optio_host/seeds.py`
- Modify: `packages/optio-host/tests/conftest.py`
- Test: `packages/optio-host/tests/test_seeds.py`

- [ ] **Step 1: Add a `mongo_db` fixture to optio-host conftest**

Append to `packages/optio-host/tests/conftest.py`:

```python
import os

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def mongo_db():
    """Per-test MongoDB database, dropped after each test."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_host_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()
```

(Keep the existing `tmp_workdir` fixture and its imports.)

- [ ] **Step 2: Write the failing test for the Mongo helpers**

Create `packages/optio-host/tests/test_seeds.py`:

```python
"""Tests for the generic optio-host seed engine."""

import io
import tarfile

import pytest
from bson import ObjectId

from optio_host import seeds
from optio_host.host import LocalHost


SUFFIX = "_fake_seeds"


async def test_insert_load_delete_list_roundtrip(mongo_db):
    blob_id = ObjectId()
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=blob_id, manifest_version=1,
    )
    assert isinstance(seed_id, str)
    assert ObjectId(seed_id)  # hex parses

    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc is not None
    assert doc["blobId"] == blob_id
    assert doc["manifestVersion"] == 1
    assert "createdAt" in doc

    listed = await seeds.list_seeds(mongo_db, prefix="t", suffix=SUFFIX)
    assert listed == [{"seedId": seed_id, "createdAt": doc["createdAt"]}]

    removed_blob = await seeds.delete_seed(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id,
    )
    assert removed_blob == blob_id
    assert await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id) is None


async def test_load_and_delete_tolerate_bad_id(mongo_db):
    assert await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id="not-hex") is None
    assert await seeds.delete_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id="not-hex") is None
    missing = str(ObjectId())
    assert await seeds.delete_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=missing) is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd packages/optio-host && python -m pytest tests/test_seeds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optio_host.seeds'`.

- [ ] **Step 4: Write the engine module (manifest + Mongo helpers only)**

Create `packages/optio-host/src/optio_host/seeds.py`:

```python
"""Generic, agent-agnostic seed engine.

A *seed* is a stored, optionally-encrypted tar.gz of the *environment*
subset of an agent's isolated HOME (credentials, settings, plugins,
global config) — no conversation/session data. The mechanism here knows
nothing about claude or opencode; agent-specific behavior is supplied via
a `SeedManifest`.

Seeds are stored in a Mongo collection `{prefix}{suffix}` (the agent
package owns `suffix`) with the encrypted blob in GridFS. Each capture
mints a new, opaque, optio-generated id (an `ObjectId` hex string).

optio-host depends on optio-core, so importing `ProcessContext` for
typing is allowed; we keep `bson`/`motor` as local/TYPE_CHECKING imports
to mirror the `optio_host.context` convention.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from bson import ObjectId
    from motor.motor_asyncio import AsyncIOMotorDatabase

    from optio_core.context import ProcessContext
    from optio_host.host import Host


@dataclass(frozen=True)
class SeedManifest:
    """Agent-specific description of what a seed contains.

    - `home_subdir`: HOME relative to the workdir (e.g. "home").
    - `include`: environment paths relative to `home_subdir` (files or
      directories); only those that exist at capture time are tarred.
    - `version`: manifest ruleset version, recorded on each seed doc.
    - `consume_transform`: optional async fixup applied after extract
      (e.g. rekey config to the new cwd). Receives the Host
      (`host.workdir` is the new cwd). None = no transform.
    """

    home_subdir: str
    include: list[str]
    version: int = 1
    consume_transform: "Callable[[Host], Awaitable[None]] | None" = None


# --- Mongo helpers ---------------------------------------------------------


def _collection(db: "AsyncIOMotorDatabase", prefix: str, suffix: str):
    return db[f"{prefix}{suffix}"]


async def insert_seed(
    db: "AsyncIOMotorDatabase",
    *,
    prefix: str,
    suffix: str,
    blob_id: "ObjectId",
    manifest_version: int,
) -> str:
    """Insert one seed doc; return the generated seed_id (ObjectId hex)."""
    doc = {
        "createdAt": datetime.now(timezone.utc),
        "blobId": blob_id,
        "manifestVersion": manifest_version,
    }
    result = await _collection(db, prefix, suffix).insert_one(doc)
    return str(result.inserted_id)


async def load_seed(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
) -> dict | None:
    """Look up a seed doc by id. Returns None for unknown or malformed id."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(seed_id)
    except (InvalidId, TypeError):
        return None
    return await _collection(db, prefix, suffix).find_one({"_id": oid})


async def delete_seed(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
) -> "ObjectId | None":
    """Delete a seed doc; return its blobId so the caller removes the
    GridFS blob (mirrors the snapshot-prune contract). None if absent."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(seed_id)
    except (InvalidId, TypeError):
        return None
    doc = await _collection(db, prefix, suffix).find_one_and_delete({"_id": oid})
    return doc["blobId"] if doc else None


async def list_seeds(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str,
) -> list[dict]:
    """Return [{seedId, createdAt}, ...] for all seeds in the collection."""
    out: list[dict] = []
    cursor = _collection(db, prefix, suffix).find({}, projection={"createdAt": 1})
    async for d in cursor:
        out.append({"seedId": str(d["_id"]), "createdAt": d.get("createdAt")})
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd packages/optio-host && python -m pytest tests/test_seeds.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-host/src/optio_host/seeds.py packages/optio-host/tests/conftest.py packages/optio-host/tests/test_seeds.py
git commit -m "feat(optio-host): seed engine — SeedManifest + Mongo helpers"
```

---

## Task 2: Engine — `capture_seed` / `merge_seed`

**Files:**
- Modify: `packages/optio-host/src/optio_host/seeds.py`
- Test: `packages/optio-host/tests/test_seeds.py`

- [ ] **Step 1: Write the failing capture/merge round-trip test**

Append to `packages/optio-host/tests/test_seeds.py`:

```python
async def _local_ctx(mongo_db, taskdir):
    """A minimal real ProcessContext for GridFS blob I/O."""
    import asyncio

    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["t_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid,
        process_id="p",
        root_oid=oid,
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="t",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


def _plant_env(host_workdir: str) -> None:
    """Plant INCLUDE + EXCLUDE files under <workdir>/home."""
    import os

    home = os.path.join(host_workdir, "home")
    claude = os.path.join(home, ".claude")
    os.makedirs(os.path.join(claude, "plugins", "marketplace"), exist_ok=True)
    os.makedirs(os.path.join(claude, "projects", "old-cwd"), exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "x"}')
    with open(os.path.join(claude, "plugins", "marketplace", "p.json"), "w") as fh:
        fh.write("{}")
    with open(os.path.join(claude, "projects", "old-cwd", "transcript.jsonl"), "w") as fh:
        fh.write('{"msg": "secret"}')
    with open(os.path.join(home, ".claude.json"), "w") as fh:
        fh.write('{"userID": "u1"}')


FAKE_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".claude/.credentials.json", ".claude/plugins", ".claude.json"],
    version=7,
)


async def test_capture_then_merge_roundtrip(mongo_db, tmp_workdir):
    import os

    # capture host
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "src"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )

    # seed doc records the manifest version
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["manifestVersion"] == 7

    # the stored tar contains ONLY include paths — never the transcript
    payload = bytearray()
    async with ctx.load_blob(doc["blobId"]) as reader:
        while chunk := await reader.read(1 << 20):
            payload.extend(chunk)
    with tarfile.open(fileobj=io.BytesIO(bytes(payload)), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith(".credentials.json") for n in names)
    assert any("plugins" in n for n in names)
    assert any(n == ".claude.json" for n in names)
    assert not any("projects" in n for n in names), names
    assert not any("transcript.jsonl" in n for n in names), names

    # merge into a fresh host
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "dst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude", ".credentials.json"))
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))
    assert not os.path.exists(os.path.join(dst.workdir, "home", ".claude", "projects"))


async def test_capture_encrypt_decrypt_roundtrip(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "esrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    def enc(b: bytes) -> bytes:
        return bytes((x + 1) & 0xFF for x in b)

    def dec(b: bytes) -> bytes:
        return bytes((x - 1) & 0xFF for x in b)

    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=enc,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "edst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=dec,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))


async def test_merge_unknown_seed_raises(mongo_db, tmp_workdir):
    import os

    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "u"))
    await dst.setup_workdir()
    ctx = await _local_ctx(mongo_db, dst.taskdir)
    with pytest.raises(KeyError):
        await seeds.merge_seed(
            ctx, dst, seed_id=str(ObjectId()), manifest=FAKE_MANIFEST,
            suffix=SUFFIX, decrypt=None,
        )


async def test_consume_transform_runs_after_extract(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "tsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    async def _stamp(host) -> None:
        await host.run_command(f"touch {host.workdir}/home/.transform-ran")

    manifest = seeds.SeedManifest(
        home_subdir="home", include=[".claude.json"], version=1, consume_transform=_stamp,
    )
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=manifest, suffix=SUFFIX, encrypt=None,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "tdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=manifest, suffix=SUFFIX, decrypt=None,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".transform-ran"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/optio-host && python -m pytest tests/test_seeds.py -k "capture or merge or consume" -v`
Expected: FAIL — `AttributeError: module 'optio_host.seeds' has no attribute 'capture_seed'`.

- [ ] **Step 3: Add the engine functions + tar helpers**

Append to `packages/optio-host/src/optio_host/seeds.py`:

```python
# --- engine ----------------------------------------------------------------


async def _read_blob_bytes(ctx: "ProcessContext", blob_id) -> bytes:
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _archive_include(host: "Host", *, home_subdir: str, include: list[str]) -> bytes:
    """tar.gz the include paths (those that exist) relative to home_subdir."""
    workdir = host.workdir.rstrip("/")
    home_abs = f"{workdir}/{home_subdir}"
    tmpfile = f"{workdir}/.optio-seed-capture.tar.gz"

    existing: list[str] = []
    for rel in include:
        probe = await host.run_command(f"test -e {shlex.quote(home_abs + '/' + rel)}")
        if probe.exit_code == 0:
            existing.append(rel)

    if existing:
        paths = " ".join(shlex.quote(p) for p in existing)
        cmd = f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)} {paths}"
    else:
        # No env files yet (e.g. a brand-new vanilla session). Produce a
        # valid, empty tar so capture still succeeds.
        cmd = f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)} -T /dev/null"

    r = await host.run_command(cmd)
    if r.exit_code != 0:
        raise RuntimeError(
            f"seed tar failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _extract_seed(host: "Host", *, home_subdir: str, plain: bytes) -> None:
    """Extract the decrypted seed tar over <workdir>/<home_subdir>."""
    workdir = host.workdir.rstrip("/")
    home_abs = f"{workdir}/{home_subdir}"
    tmpfile = f"{workdir}/.optio-seed-restore.tar.gz"
    await host.run_command(f"mkdir -p {shlex.quote(home_abs)}")
    await host.put_file_to_host(plain, tmpfile)
    try:
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"seed untar failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def capture_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    manifest: SeedManifest,
    suffix: str,
    encrypt: "Callable[[bytes], bytes] | None",
) -> str:
    """tar include -> encrypt -> store blob -> insert doc. Returns seed_id."""
    raw = await _archive_include(
        host, home_subdir=manifest.home_subdir, include=manifest.include,
    )
    enc = encrypt or (lambda b: b)
    payload = enc(raw)
    async with ctx.store_blob("seed") as writer:
        await writer.write(payload)
        blob_id = writer.file_id
    return await insert_seed(
        ctx._db, prefix=ctx._prefix, suffix=suffix,
        blob_id=blob_id, manifest_version=manifest.version,
    )


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
    doc = await load_seed(ctx._db, prefix=ctx._prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    payload = await _read_blob_bytes(ctx, doc["blobId"])
    dec = decrypt or (lambda b: b)
    plain = dec(payload)
    await _extract_seed(host, home_subdir=manifest.home_subdir, plain=plain)
    if manifest.consume_transform is not None:
        await manifest.consume_transform(host)
```

- [ ] **Step 4: Run the full engine test suite to verify it passes**

Run: `cd packages/optio-host && python -m pytest tests/test_seeds.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-host/src/optio_host/seeds.py packages/optio-host/tests/test_seeds.py
git commit -m "feat(optio-host): seed engine — capture_seed / merge_seed"
```

---

## Task 3: claudecode adapter — manifest, suffix, rekey transform, GC wrappers

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/__init__.py`
- Test: `packages/optio-claudecode/tests/test_seed_config.py` (the rekey unit tests live here)

- [ ] **Step 1: Write the failing rekey-transform test**

Create `packages/optio-claudecode/tests/test_seed_config.py`:

```python
"""Config defaults + the claudecode seed-manifest rekey transform."""

import json
import os

from optio_host.host import LocalHost

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import (
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    _rekey_claude_json_projects,
)


def test_seed_config_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.seed_id is None
    assert cfg.on_seed_saved is None


def test_manifest_shape():
    assert CLAUDE_SEED_SUFFIX == "_claudecode_seeds"
    assert CLAUDE_SEED_MANIFEST.home_subdir == "home"
    assert ".claude/.credentials.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude.json" in CLAUDE_SEED_MANIFEST.include
    assert ".claude/plugins" in CLAUDE_SEED_MANIFEST.include


async def test_rekey_single_entry_to_new_cwd(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "r"))
    await host.setup_workdir()
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cj = os.path.join(home, ".claude.json")
    with open(cj, "w") as fh:
        json.dump({"userID": "u", "projects": {"/old/cwd": {"allowedTools": ["Bash"]}}}, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        data = json.load(fh)
    assert list(data["projects"].keys()) == [host.workdir]
    assert data["projects"][host.workdir] == {"allowedTools": ["Bash"]}
    assert data["userID"] == "u"


async def test_rekey_multi_entry_left_untouched(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "m"))
    await host.setup_workdir()
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cj = os.path.join(home, ".claude.json")
    original = {"projects": {"/a": {}, "/b": {}}}
    with open(cj, "w") as fh:
        json.dump(original, fh)

    await _rekey_claude_json_projects(host)

    with open(cj) as fh:
        assert json.load(fh) == original


async def test_rekey_missing_file_is_noop(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "n"))
    await host.setup_workdir()
    await _rekey_claude_json_projects(host)  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_seed_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optio_claudecode.seed_manifest'` (and `seed_id`/`on_seed_saved` AttributeError once that import is fixed — those land in Task 4).

- [ ] **Step 3: Write the adapter module**

Create `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py`:

```python
"""claudecode adopter of the generic optio-host seed engine.

Defines the claudecode seed manifest (HOME layout + capture-time include
triage + consume-time rekey), the Mongo collection suffix, and ergonomic
`delete_seed` / `list_seeds` wrappers that bind the suffix for consuming
apps.
"""

from __future__ import annotations

import json
import logging

from optio_host import seeds
from optio_host.host import Host

_LOG = logging.getLogger(__name__)

CLAUDE_SEED_SUFFIX = "_claudecode_seeds"
CLAUDE_SEED_MANIFEST_VERSION = 1


async def _rekey_claude_json_projects(host: Host) -> None:
    """Rewrite the single `projects` entry in home/.claude.json to the new
    cwd, preserving its value (trust flags, allowedTools, MCP enablement)
    so an autonomous task isn't blocked by claude's trust prompt.

    Empty / multi-entry / missing / malformed -> left as-is (a fresh trust
    prompt is the safe fallback).
    """
    workdir = host.workdir.rstrip("/")
    path = f"{workdir}/home/.claude.json"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _LOG.warning("seed: .claude.json is not valid JSON; leaving projects as-is")
        return
    projects = data.get("projects")
    if not isinstance(projects, dict) or len(projects) != 1:
        return
    (old_value,) = projects.values()
    data["projects"] = {workdir: old_value}
    await host.put_file_to_host(
        json.dumps(data).encode("utf-8"), path,
    )


CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        ".claude/.credentials.json",
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/plugins",
        ".claude.json",
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)


async def delete_seed(db, prefix: str, seed_id: str):
    """Delete a claudecode seed doc; returns its GridFS blobId (or None).

    Ergonomic wrapper binding `CLAUDE_SEED_SUFFIX` so consuming apps don't
    need to know the collection suffix. The caller still removes the
    returned blob from GridFS.
    """
    return await seeds.delete_seed(
        db, prefix=prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(db, prefix: str) -> list[dict]:
    """List claudecode seeds as [{seedId, createdAt}, ...]."""
    return await seeds.list_seeds(db, prefix=prefix, suffix=CLAUDE_SEED_SUFFIX)
```

- [ ] **Step 4: Export the public surface from the package `__init__`**

In `packages/optio-claudecode/src/optio_claudecode/__init__.py`, add after the `from optio_claudecode.types import (...)` block:

```python
from optio_claudecode.seed_manifest import (
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    delete_seed,
    list_seeds,
)
```

And add to `__all__`:

```python
    "CLAUDE_SEED_MANIFEST",
    "CLAUDE_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
```

- [ ] **Step 5: Run the rekey + manifest tests (config-default tests still fail until Task 4)**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_seed_config.py -k "rekey or manifest_shape" -v`
Expected: PASS for the rekey + `test_manifest_shape` tests. (`test_seed_config_defaults_none` stays RED until Task 4 — that is expected and acceptable; it is the failing test that drives Task 4.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/seed_manifest.py packages/optio-claudecode/src/optio_claudecode/__init__.py packages/optio-claudecode/tests/test_seed_config.py
git commit -m "feat(optio-claudecode): seed manifest + rekey transform + GC wrappers"
```

---

## Task 4: claudecode config — `seed_id`, `on_seed_saved`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`
- Test: `packages/optio-claudecode/tests/test_seed_config.py` (the `defaults_none` test from Task 3)

- [ ] **Step 1: Confirm the failing test**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_seed_config.py::test_seed_config_defaults_none -v`
Expected: FAIL — `AttributeError: 'ClaudeCodeTaskConfig' object has no attribute 'seed_id'`.

- [ ] **Step 2: Add the two fields**

In `packages/optio-claudecode/src/optio_claudecode/types.py`, update imports and add fields after the `on_resume_refresh` field (line 71).

Change the import line:

```python
from typing import Any, Awaitable, Callable, Literal
```

Add after the `on_resume_refresh` field:

```python
    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment into a
    # fresh workdir before launch, beginning a NEW conversation (no
    # --continue). Baked at task-creation time; no per-launch channel.
    seed_id: str | None = None
    # Capture intent: a (sync or async) callback fired with the generated
    # seed_id after a successful capture on teardown of a fresh session.
    # Its presence is what enables seed capture. Both default None, so
    # existing consumers are unaffected. Both are ignored on resume.
    on_seed_saved: "Callable[[str], Awaitable[None] | None] | None" = None
```

(No `__post_init__` change: both fields default None and need no validation.)

- [ ] **Step 3: Run to verify it passes**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_seed_config.py -v`
Expected: PASS (all tests in the file now pass).

- [ ] **Step 4: Run the existing type-lock test to confirm no regression**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_types.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py
git commit -m "feat(optio-claudecode): add seed_id + on_seed_saved config fields"
```

---

## Task 5: claudecode session wiring — merge, capture, D3, resume-ignore

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Test: covered by Tasks 6–7 (integration). This task is implementation; its tests are the session-level tests written next. To honor TDD ordering, **do Task 6 (fake_claude scenario) and the first capture test in Task 7 before implementing this task's body** — see the note below.

> **TDD ordering note:** The session wiring is exercised only through integration tests that need the `seed` fake_claude scenario (Task 6) and the capture test (Task 7, Step 1–2). Implement those test scaffolds first (they will fail), then implement this task to make them pass. The steps below describe the exact edits; run the Task 7 capture/consume tests as the RED→GREEN signal.

- [ ] **Step 1: Add the `_call_maybe_async` and `_has_transcript` helpers**

In `packages/optio-claudecode/src/optio_claudecode/session.py`, add `import inspect` to the imports, and add these helpers in the `# --- helpers ---` section (after `_read_blob_bytes`):

```python
async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


async def _has_transcript(host: Host) -> bool:
    """True if the restored snapshot carries a claude transcript.

    D3 safety: claude exits at startup if `--continue` is passed with no
    session to continue. Detect by looking for any `*.jsonl` under
    home/.claude/projects/.
    """
    workdir = host.workdir.rstrip("/")
    projects = f"{workdir}/home/.claude/projects"
    r = await host.run_command(
        f"find {shlex.quote(projects)} -name '*.jsonl' -print -quit 2>/dev/null || true"
    )
    return bool(r.stdout.strip())
```

- [ ] **Step 2: Add the seed-engine + manifest imports**

In `packages/optio-claudecode/src/optio_claudecode/session.py`, add near the other `optio_host` / `optio_claudecode` imports:

```python
from optio_host import seeds as _seeds

from optio_claudecode.seed_manifest import CLAUDE_SEED_MANIFEST, CLAUDE_SEED_SUFFIX
```

- [ ] **Step 3: Wire the D3 safety + resume-ignores-seed logging into the resume block**

Replace the resume block (current lines 101–120) so it computes a separate `pass_continue` flag and logs ignored seed inputs:

```python
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )

    resuming = snapshot is not None
    # `pass_continue` decides whether claude is launched with --continue.
    # It is NOT the same as `resuming`: a restored snapshot with no
    # transcript must launch WITHOUT --continue (D3).
    pass_continue = False
    if resuming:
        if config.seed_id is not None or config.on_seed_saved is not None:
            _LOG.warning(
                "resume takes precedence; seed_id/on_seed_saved ignored "
                "(the snapshot already carries the full environment)",
            )
        await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
        payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
        decrypt = config.session_blob_decrypt or (lambda b: b)
        plain = decrypt(payload)
        await _extract_home_claude(host, plain)
        await _rotate_optio_log(host)
        pass_continue = await _has_transcript(host)
        if not pass_continue:
            _LOG.warning(
                "resume: restored snapshot has no transcript; launching "
                "without --continue (D3 safety)",
            )
```

- [ ] **Step 4: Wire the seeded-fresh merge into the fresh-start body branch**

In `_claudecode_body`, inside the `if not resuming:` branch, after the `plant_home_files(...)` call (current lines 130–134) and before the `write_text("AGENTS.md", ...)` call, insert:

```python
            if config.seed_id is not None:
                # Seeded fresh: overlay the stored environment on top of
                # any consumer-planted creds/config (seed wins), then
                # rekey .claude.json projects to the new cwd. Begins a NEW
                # conversation — no --continue.
                _trace("body: merge_seed START id=%s", config.seed_id)
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=config.seed_id,
                    manifest=CLAUDE_SEED_MANIFEST,
                    suffix=CLAUDE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
                _trace("body: merge_seed DONE")
```

- [ ] **Step 5: Update the `build_claude_flags` call to use `pass_continue`**

Change the `claude_flags = host_actions.build_claude_flags(...)` call (current line 159–164) so its `resuming=` argument is `pass_continue`:

```python
        claude_flags = host_actions.build_claude_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            resuming=pass_continue,
        )
```

- [ ] **Step 6: Wire seed capture into the finally — BEFORE the snapshot block**

In the `finally:` block, insert the seed-capture block **immediately after the `terminate_subprocess` block and before the `if config.supports_resume:` snapshot block** (the snapshot capture does `rm -rf home/.claude`, which would destroy the seed source if it ran first):

```python
        if not resuming and config.on_seed_saved is not None:
            _trace("finally: capture_seed START")
            try:
                seed_id = await _seeds.capture_seed(
                    ctx, host,
                    manifest=CLAUDE_SEED_MANIFEST,
                    suffix=CLAUDE_SEED_SUFFIX,
                    encrypt=config.session_blob_encrypt,
                )
                _trace("finally: capture_seed DONE id=%s", seed_id)
                await _call_maybe_async(config.on_seed_saved, seed_id)
                _trace("finally: on_seed_saved fired")
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )
                _trace("finally: capture_seed RAISED")
```

- [ ] **Step 7: Run the session seed tests (written in Tasks 6–7) to verify GREEN**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_session_seed_capture.py tests/test_session_seed_consume.py tests/test_session_seed_unknown_id.py -v`
Expected: PASS (after Tasks 6 & 7 scaffolds exist).

- [ ] **Step 8: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(optio-claudecode): wire seed merge/capture + D3 no-transcript safety"
```

---

## Task 6: fake_claude `seed` scenario

**Files:**
- Modify: `packages/optio-claudecode/tests/fake_claude.py`

- [ ] **Step 1: Add the `seed` scenario that plants INCLUDE + EXCLUDE files**

In `packages/optio-claudecode/tests/fake_claude.py`, add `"seed"` to the `SCENARIOS` tuple, add the scenario function, and register it in the dispatch dict.

Add to `SCENARIOS`:

```python
SCENARIOS = (
    "happy", "deliverable", "error", "long",
    "long_then_signaled", "idempotent_done", "seed",
)
```

Add the scenario function (after `_scenario_idempotent_done`):

```python
def _scenario_seed() -> None:
    """Plant a representative environment under the isolated HOME so seed
    capture has INCLUDE files to tar and EXCLUDE files to skip, then DONE.

    `$HOME` is `<workdir>/home` under HOME-isolation. The `.claude.json`
    `projects` map is keyed to the run's cwd so the consume-time rekey has
    a single entry to rewrite.
    """
    home = os.environ.get("HOME")
    if home:
        claude = Path(home) / ".claude"
        (claude / "plugins" / "marketplace").mkdir(parents=True, exist_ok=True)
        (claude / "projects" / "session-x").mkdir(parents=True, exist_ok=True)
        # INCLUDE (environment)
        (claude / ".credentials.json").write_text('{"token": "abc"}', encoding="utf-8")
        (claude / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
        (claude / "mcp-needs-auth-cache.json").write_text("{}", encoding="utf-8")
        (claude / "plugins" / "marketplace" / "p.json").write_text("{}", encoding="utf-8")
        # EXCLUDE (session / transcript) — must NOT travel in the seed
        (claude / "projects" / "session-x" / "transcript.jsonl").write_text(
            '{"msg": "secret-transcript"}', encoding="utf-8",
        )
        (claude / "history.jsonl").write_text("h\n", encoding="utf-8")
        # .claude.json with a single projects entry keyed to the run cwd
        (Path(home) / ".claude.json").write_text(
            json.dumps({
                "userID": "u1",
                "oauthAccount": {"email": "x@y.z"},
                "projects": {str(Path.cwd()): {"allowedTools": ["Bash"]}},
            }),
            encoding="utf-8",
        )
    time.sleep(0.05)
    _log("STATUS: 10% configuring environment")
    time.sleep(0.05)
    _log("DONE: seed environment ready")
    time.sleep(30.0)
```

Register in the dispatch dict in `main()`:

```python
        "idempotent_done": _scenario_idempotent_done,
        "seed": _scenario_seed,
    }[scenario]()
```

- [ ] **Step 2: Sanity-check the scenario parses**

Run: `cd packages/optio-claudecode && python -c "import tests.fake_claude as f; print('seed' in f.SCENARIOS)"`
Expected: prints `True`.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/fake_claude.py
git commit -m "test(optio-claudecode): fake_claude seed scenario plants env + transcript"
```

---

## Task 7: claudecode session seed tests (capture / consume / unknown id)

**Files:**
- Create: `packages/optio-claudecode/tests/test_session_seed_capture.py`
- Create: `packages/optio-claudecode/tests/test_session_seed_consume.py`
- Create: `packages/optio-claudecode/tests/test_session_seed_unknown_id.py`

> These tests reuse the resume test's `_make_ctx` / `mongo_db` / `task_root` patterns. Each file is self-contained (repeats the small helpers) so it can be read in isolation.

- [ ] **Step 1: Write the capture test**

Create `packages/optio-claudecode/tests/test_session_seed_capture.py`:

```python
"""A fresh session with on_seed_saved captures an env-only seed."""

import asyncio
import io
import os
import tarfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX
from optio_claudecode.session import run_claudecode_session
from optio_host import seeds


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_cap_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


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


async def test_capture_fires_callback_and_stores_env_only_seed(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")
    captured: list[str] = []

    async def _on_seed_saved(seed_id: str) -> None:
        captured.append(seed_id)

    ctx = await _make_ctx(mongo_db, "cc_seed_cap")
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
    )
    await run_claudecode_session(ctx, cfg)

    # callback fired with a hex id
    assert len(captured) == 1
    seed_id = captured[0]

    # a seed doc + blob exist
    doc = await seeds.load_seed(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id)
    assert doc is not None

    # the seed tar contains ONLY INCLUDE paths, never the transcript
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(doc["blobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith(".credentials.json") for n in names)
    assert any(n.endswith("settings.json") for n in names)
    assert any(n == ".claude.json" for n in names)
    assert any("plugins" in n for n in names)
    assert not any("projects" in n for n in names), names
    assert not any("history.jsonl" in n for n in names), names
```

- [ ] **Step 2: Run the capture test — RED first, then GREEN after Task 5**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_session_seed_capture.py -v`
Expected before Task 5 wiring: FAIL (callback never fires). After Task 5: PASS.

- [ ] **Step 3: Write the consume test**

Create `packages/optio-claudecode/tests/test_session_seed_consume.py`:

```python
"""Capture a seed, then a second fresh session consumes it."""

import asyncio
import json
import os

import pytest
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


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


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
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")

    # 1) capture
    captured: list[str] = []

    async def _on_seed_saved(seed_id: str) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cc_seed_src")
    await run_claudecode_session(ctx1, ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        claude_install_dir=str(shim_install_dir),
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
        cj = await hook_ctx.read_text_from_host("home/.claude.json")
        observed["projects_key"] = list(json.loads(cj)["projects"].keys())
        observed["new_cwd"] = wd

    # the second session must NOT re-run the seed scenario's planting on top;
    # use the "happy" scenario so the planted files come purely from the seed.
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx2 = await _make_ctx(mongo_db, "cc_seed_dst")
    await run_claudecode_session(ctx2, ClaudeCodeTaskConfig(
        consumer_instructions="(seeded fresh)",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_probe,
    ))

    assert observed["creds"] is True
    assert observed["plugins"] is True
    # transcript dir from the seed-source session must NOT be restored
    assert observed["projects_dir"] is False
    # .claude.json projects rekeyed to the new cwd
    assert observed["projects_key"] == [observed["new_cwd"]]
```

- [ ] **Step 4: Write the unknown-id test**

Create `packages/optio-claudecode/tests/test_session_seed_unknown_id.py`:

```python
"""A bogus seed_id fails loudly — no silent vanilla fallback."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_seed_unk_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", str(tmp_path))
    return tmp_path


async def _make_ctx(mongo_db, process_id):
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
        cancellation_flag=asyncio.Event(), child_counter={"next": 0}, resume=False,
    )


async def test_unknown_seed_id_raises(mongo_db, task_root, shim_install_dir, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_ctx(mongo_db, "cc_seed_unknown")
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(bad seed)",
        claude_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=str(ObjectId()),  # well-formed but absent
    )
    with pytest.raises(Exception):  # KeyError surfaces through the session
        await run_claudecode_session(ctx, cfg)
```

- [ ] **Step 5: Run all three seed session tests**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_session_seed_capture.py tests/test_session_seed_consume.py tests/test_session_seed_unknown_id.py -v`
Expected: PASS (with Task 5 implemented).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_seed_capture.py packages/optio-claudecode/tests/test_session_seed_consume.py packages/optio-claudecode/tests/test_session_seed_unknown_id.py
git commit -m "test(optio-claudecode): seed capture/consume/unknown-id session tests"
```

---

## Task 8: D3 no-transcript resume test

**Files:**
- Modify: `packages/optio-claudecode/tests/test_session_resume.py`

- [ ] **Step 1: Write the failing D3 test**

Append to `packages/optio-claudecode/tests/test_session_resume.py`:

```python
async def test_resume_with_no_transcript_launches_without_continue(
    mongo_db, task_root, shim_install_dir, monkeypatch,
):
    """D3: a restored snapshot whose home/.claude/projects has no *.jsonl
    must launch WITHOUT --continue (passing it makes claude exit at
    startup). The `happy` scenario never writes a transcript, so its
    snapshot has none — the resumed launch must omit --continue."""
    import io, tarfile

    pid = "cc_d3_no_transcript"
    # First cycle: fresh `happy` run captures a snapshot with NO transcript.
    await _run_cycle(mongo_db, pid, shim_install_dir, "happy", False, monkeypatch)
    # Second cycle: resume. D3 must suppress --continue.
    await _run_cycle(mongo_db, pid, shim_install_dir, "happy", True, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers() if m.name.endswith("fake_claude_argv.json")
        )
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()
    launches = [json.loads(line) for line in argv_lines if line]
    # Neither the fresh launch nor the resumed launch may carry --continue,
    # because no transcript ever existed.
    assert all("--continue" not in launch for launch in launches), launches
```

- [ ] **Step 2: Run the D3 test**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_session_resume.py::test_resume_with_no_transcript_launches_without_continue -v`
Expected: PASS (Task 5 implemented the D3 `_has_transcript` gate). If it FAILS with a `--continue` present, the D3 wiring in Task 5 Step 3/5 is wrong — fix there.

- [ ] **Step 3: Run the full resume suite to confirm no regression**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_session_resume.py -v`
Expected: PASS — including `test_resume_creates_second_snapshot_and_passes_continue` (which uses `idempotent_done`; that scenario's argv recording lives under `.claude/fake_claude_argv.json`, an EXCLUDE path, so a transcript `*.jsonl` is absent — **verify this test still passes**, since the `idempotent_done` snapshot also lacks a `*.jsonl` transcript).

> **Risk flag for the implementer:** `test_resume_creates_second_snapshot_and_passes_continue` asserts `--continue` IS passed on resume, but `_has_transcript` only looks for `*.jsonl` under `home/.claude/projects/`. fake_claude writes `fake_claude_argv.json` (not under `projects/`, not `.jsonl`), so `_has_transcript` returns False and D3 would suppress `--continue` — breaking that existing test. **Resolve before merging** by one of: (a) having the `idempotent_done` scenario also write a `home/.claude/projects/<x>/<y>.jsonl` so a transcript genuinely exists (the realistic case — a real resumed session has a transcript), or (b) refining `_has_transcript` to match real claude transcript layout. Option (a) is preferred: it makes the existing test's premise honest (resume implies a transcript) and keeps `_has_transcript` simple. Update the `idempotent_done` scenario in `fake_claude.py` accordingly and re-run both tests.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-claudecode/tests/test_session_resume.py packages/optio-claudecode/tests/fake_claude.py
git commit -m "test(optio-claudecode): D3 — no-transcript resume omits --continue"
```

---

## Task 9: Demo — setup task, registry, per-seed tasks, resync

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/__main__.py`
- Modify: `packages/optio-demo/src/optio_demo/tasks/__init__.py`
- Modify: `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

> The demo has no automated test here (it is exercised manually per the spec's operator flow). The verification step is an import/smoke check that the task generator runs against a live Mongo and emits the setup task.

- [ ] **Step 1: Expose `db` + `prefix` in the demo `services` dict**

In `packages/optio-demo/src/optio_demo/__main__.py`, change the `services=` argument:

```python
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"optio": fw, "db": db, "prefix": prefix},
        get_task_definitions=get_task_definitions,
    )
```

- [ ] **Step 2: Make the aggregator await the async claudecode generator**

In `packages/optio-demo/src/optio_demo/tasks/__init__.py`, change the claudecode import + call:

```python
from optio_demo.tasks.claudecode import get_tasks as claudecode_tasks


async def get_task_definitions(
    services: dict,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
        *marimo_tasks(),
        *opencode_tasks(),
        *await claudecode_tasks(services),
    ]
```

- [ ] **Step 3: Rewrite the claudecode demo generator**

In `packages/optio-demo/src/optio_demo/tasks/claudecode.py`:

3a. Add imports at the top (after the existing imports):

```python
from datetime import datetime, timezone
```

3b. Add the demo registry constant and the seed-related prompts near `CONSUMER_PROMPT`:

```python
DEMO_SEED_COLLECTION_SUFFIX = "_demo_claude_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session. Use the terminal to log into "
    "Claude Code (run `/login` and follow the prompts) and install any "
    "plugins or MCP servers you want available to demo tasks. When you "
    "are done, STOP this task from the dashboard — your configuration "
    "(credentials, settings, plugins) will be captured as a reusable "
    "seed, and a new 'Claude Code demo' task pinned to that seed will "
    "appear automatically."
)
```

3c. Replace the `def get_tasks() -> list[TaskInstance]:` function (current lines 134–169) with an async generator that builds the setup task + per-seed tasks:

```python
def _make_on_seed_saved(db, prefix: str, fw):
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]

    async def _on_seed_saved(seed_id: str) -> None:
        # Cosmetic numbering; a concurrent-save race may reuse a number —
        # acceptable, the seedId is the real key.
        count = await coll.count_documents({})
        name = f"Config #{count + 1}"
        await coll.insert_one({
            "seedId": seed_id,
            "name": name,
            "createdAt": datetime.now(timezone.utc),
        })
        # Regenerate the task list so a seed-pinned demo task appears.
        await fw.resync()

    return _on_seed_saved


async def get_tasks(services: dict) -> list[TaskInstance]:
    db = services["db"]
    prefix = services["prefix"]
    fw = services["optio"]
    ssh = _resolve_ssh_config()

    tasks: list[TaskInstance] = [
        # The existing API-key / credentials demo task (unchanged behavior).
        create_claudecode_task(
            process_id="claudecode-demo",
            name="Claude Code demo",
            description=(
                "Claude Code session that reads a context file shipped "
                "by before_execute, asks for a favorite color, ships a "
                "deliverable combining both colors and a code-name, then "
                "after_execute reports a session-log summary. Set "
                "ANTHROPIC_API_KEY for the agent to authenticate."
            ),
            config=ClaudeCodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_resolve_env(),
                permission_mode="bypassPermissions",
                ssh=ssh,
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
                supports_resume=True,
            ),
        ),
        # The seed setup task: vanilla (no seed_id), on_seed_saved wired.
        create_claudecode_task(
            process_id="claudecode-seed-setup",
            name="Setup Claude Code seed",
            description=(
                "One-time: log into Claude Code and configure plugins, "
                "then stop the task to capture a reusable seed. A new "
                "seed-pinned demo task appears afterward."
            ),
            config=ClaudeCodeTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                ssh=ssh,
                # Interactive login — no autonomous bypass, no resume.
                supports_resume=False,
                on_seed_saved=_make_on_seed_saved(db, prefix, fw),
            ),
        ),
    ]

    # One seed-pinned demo task per recorded seed.
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]
    async for rec in coll.find({}, projection={"seedId": 1, "name": 1}):
        seed_id = rec["seedId"]
        name = rec.get("name", seed_id)
        tasks.append(
            create_claudecode_task(
                process_id=f"claudecode-demo-seed-{seed_id}",
                name=f"Claude Code demo — {name}",
                description=(
                    "Fresh Claude Code session started from a captured "
                    f"seed ({name}): logged-in and configured, new "
                    "conversation. Reads context.txt, asks for a color, "
                    "ships a deliverable."
                ),
                config=ClaudeCodeTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    permission_mode="bypassPermissions",
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )

    return tasks
```

- [ ] **Step 4: Smoke-check the generator runs against a live Mongo**

Run (requires Mongo on `MONGO_URL` / localhost):

```bash
cd packages/optio-demo && python -c "
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from optio_demo.tasks.claudecode import get_tasks

async def main():
    client = AsyncIOMotorClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
    db = client['optio_demo_seed_smoke']
    tasks = await get_tasks({'db': db, 'prefix': 'optio', 'optio': None})
    names = [t.name for t in tasks]
    print(names)
    assert 'Setup Claude Code seed' in names
    assert 'Claude Code demo' in names
    await client.drop_database('optio_demo_seed_smoke')

asyncio.run(main())
"
```

Expected: prints a list including `'Setup Claude Code seed'` and `'Claude Code demo'`; no assertion error. (Passing `'optio': None` is fine here — `fw` is only touched inside the `on_seed_saved` callback, which this smoke check does not invoke.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-demo/src/optio_demo/__main__.py packages/optio-demo/src/optio_demo/tasks/__init__.py packages/optio-demo/src/optio_demo/tasks/claudecode.py
git commit -m "feat(optio-demo): seed setup task + registry + seed-pinned demo tasks"
```

---

## Task 10: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the optio-host suite**

Run: `cd packages/optio-host && python -m pytest -q`
Expected: PASS (including `test_seeds.py`).

- [ ] **Step 2: Run the optio-claudecode suite**

Run: `cd packages/optio-claudecode && python -m pytest -q`
Expected: PASS (all seed tests + the full resume suite + existing tests).

> Per project memory: run package suites in isolation (not `pnpm -r` style). MongoDB must be available via Docker on `MONGO_URL`.

- [ ] **Step 3: Confirm the public API exports resolve**

Run: `cd packages/optio-claudecode && python -c "from optio_claudecode import delete_seed, list_seeds, CLAUDE_SEED_MANIFEST, CLAUDE_SEED_SUFFIX; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: seed support full-suite verification fixups"
```

---

## Self-Review (completed)

**Spec coverage:**
- Generic engine in optio-host (SeedManifest + Mongo helpers + capture/merge) → Tasks 1–2. ✓
- claudecode adapter (manifest, suffix, rekey transform, GC wrappers) → Task 3. ✓
- Config fields `seed_id`/`on_seed_saved`, defaults None → Task 4. ✓
- Launch-mode matrix: seeded-fresh merge + no-`--continue`, capture gated on `on_seed_saved`, resume ignores seed inputs (logged), D3 no-transcript safety → Task 5. ✓
- Capture-time include/exclude triage (env-only, no transcript) → Tasks 2, 6, 7. ✓
- Seed id generation (opaque ObjectId hex, new id per capture) → Task 1 (`insert_seed`). ✓
- Encrypt-at-rest via existing hooks → Tasks 2 (engine), 5 (wiring). ✓
- Edge cases: unknown id raises (Task 7), decrypt failure propagates (engine has no fallback), capture failure caught/logged/callback-not-fired (Task 5 Step 6), empty include → empty tar (Task 2 `_archive_include`), multi-entry projects untouched (Task 3). ✓
- Demo usage: registry collection, setup task, on_seed_saved → resync, per-seed baked tasks → Task 9. ✓
- Testing section (`test_seeds.py`, three session tests, config test, D3 test) → Tasks 1–2, 7, 8. ✓

**Out of scope (per spec, deliberately absent):** opencode adoption; D2 (exit-at-startup → fail not flap); caller-supplied/overwrite ids; optio-side GC; cross-host migration tests. ✓

**Type consistency:** `capture_seed`/`merge_seed`/`insert_seed`/`load_seed`/`delete_seed`/`list_seeds` signatures match between Task 1/2 definitions and their Task 3/5/7 call sites. `CLAUDE_SEED_SUFFIX`, `CLAUDE_SEED_MANIFEST`, `_rekey_claude_json_projects` names consistent across Tasks 3, 5, and tests. `seed_id`/`on_seed_saved` field names consistent across Tasks 4, 5, 7, 9.

**Known risk surfaced for the implementer:** the `_has_transcript` D3 gate vs. the existing `test_resume_creates_second_snapshot_and_passes_continue` test — resolution documented in Task 8 Step 3.
