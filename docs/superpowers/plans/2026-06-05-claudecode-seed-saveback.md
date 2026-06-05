# Claude Code Seed Credential Save-Back Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist rotating OAuth credentials back into the claudecode seed so a seed survives in-session token refresh, on both fresh and resumed sessions.

**Architecture:** The seed becomes the single source of truth for credentials. A narrow "cred" manifest (credentials only) is used three ways: save-back (home → seed, in place), resume cred-injection (seed → home overlay), while the existing full manifest still drives capture and fresh-start. optio-agents gains member-filtered extraction + an in-place `refresh_seed`; optio-claudecode gains a credential watcher (hash-poll + final backstop) and a resume overlay. No engine changes.

**Tech Stack:** Python 3, asyncio, motor (Mongo + GridFS), Python `tarfile`, pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-06-05-claudecode-seed-saveback-design.md`.

**Test runner:** `cd ~/deai/optio && .venv/bin/python -m pytest <path> -v` (needs a local Mongo at `mongodb://localhost:27017`).

---

## File Structure

- `packages/optio-agents/src/optio_agents/seeds.py` — add `update_seed_blob`, `_merge_tar_members`, `refresh_seed`; widen `_extract_seed`/`merge_seed` with member filtering.
- `packages/optio-agents/tests/test_seeds.py` — extraction-filter + `refresh_seed` tests.
- `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py` — add `CLAUDE_CRED_MANIFEST`, compose `CLAUDE_SEED_MANIFEST` from it.
- `packages/optio-claudecode/src/optio_claudecode/cred_watcher.py` — NEW: hash/validity helpers, `save_back_if_changed`, `run_credential_watcher`.
- `packages/optio-claudecode/src/optio_claudecode/session.py` — compute baseline, start/stop watcher, final backstop, resume narrow overlay, update the resume warning.
- `packages/optio-claudecode/tests/test_cred_watcher.py` — NEW: watcher unit tests.
- `packages/optio-claudecode/tests/test_session_seed_saveback.py` — NEW: end-to-end save-back via the final backstop.

---

## Task 1: Member-filtered seed extraction (optio-agents)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/seeds.py` (`_extract_seed`, `merge_seed`)
- Test: `packages/optio-agents/tests/test_seeds.py`

- [ ] **Step 1: Write the failing test**

Add to `test_seeds.py`:

```python
async def test_merge_narrow_overlay_extracts_only_listed_members(mongo_db, tmp_workdir):
    import os

    # capture a FULL seed (creds + plugins + .claude.json)
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "nsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )

    # destination already has an OLD creds file + an unrelated file that a
    # narrow overlay must NOT delete
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "ndst"))
    await dst.setup_workdir()
    claude = os.path.join(dst.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "OLD"}')
    with open(os.path.join(claude, "keep.txt"), "w") as fh:
        fh.write("keep me")

    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=narrow, suffix=SUFFIX, decrypt=None,
    )

    # creds overwritten from the seed; plugins NOT injected; unrelated file kept
    with open(os.path.join(claude, ".credentials.json")) as fh:
        assert fh.read() == '{"token": "x"}'  # the seed value from _plant_env
    assert not os.path.exists(os.path.join(claude, "plugins"))
    assert os.path.exists(os.path.join(claude, "keep.txt"))


async def test_merge_tolerates_include_member_absent_from_archive(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "asrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "adst"))
    await dst.setup_workdir()
    # ask for a member the archive does not contain -> no error, no extraction
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/settings.json"], version=7,
    )
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=narrow, suffix=SUFFIX, decrypt=None,
    )
    assert not os.path.exists(os.path.join(dst.workdir, "home", ".claude", "settings.json"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py::test_merge_narrow_overlay_extracts_only_listed_members packages/optio-agents/tests/test_seeds.py::test_merge_tolerates_include_member_absent_from_archive -v`
Expected: FAIL — current `_extract_seed` ignores `include`, so it injects everything (plugins present) and the narrow test's `plugins` assertion fails.

- [ ] **Step 3: Implement member filtering**

Replace `_extract_seed` (currently `seeds.py:184-200`) with:

```python
async def _extract_seed(
    host: "Host", *, home_subdir: str, plain: bytes, include: list[str] | None = None,
) -> None:
    """Extract the decrypted seed tar over <workdir>/<home_subdir>.

    When `include` is given, extract ONLY the archive members that match one
    of those paths (exact file, or a directory prefix); members absent from the
    archive are silently skipped. Extraction is overlay — it overwrites the
    listed members and never deletes others. `include=None` extracts everything.
    """
    workdir = host.workdir.rstrip("/")
    home_abs = f"{workdir}/{home_subdir}"
    tmpfile = f"{workdir}/.optio-seed-restore.tar.gz"
    await host.run_command(f"mkdir -p {shlex.quote(home_abs)}")
    await host.put_file_to_host(plain, tmpfile)
    try:
        members_arg = ""
        if include is not None:
            listing = await host.run_command(f"tar -tzf {shlex.quote(tmpfile)}")
            if listing.exit_code != 0:
                raise RuntimeError(
                    f"seed list failed (exit {listing.exit_code}): "
                    f"{listing.stderr.strip()[:200]}"
                )
            names = [n for n in listing.stdout.splitlines() if n]
            wanted = [
                n for n in names
                if any(
                    n == rel or n.rstrip("/") == rel or n.startswith(rel + "/")
                    for rel in include
                )
            ]
            if not wanted:
                return  # nothing in the archive matches the requested members
            members_arg = " " + " ".join(shlex.quote(n) for n in wanted)
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)}{members_arg}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"seed untar failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")
```

Then in `merge_seed` (currently `seeds.py:246`), pass the manifest's include:

```python
    await _extract_seed(
        host, home_subdir=manifest.home_subdir, plain=plain, include=manifest.include,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py -v`
Expected: PASS — all existing seed tests still pass (full-manifest merge extracts every member, since the archive contains exactly the full include set) plus the two new tests.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py
git commit -m "feat(seeds): member-filtered extraction for narrow seed overlays"
```

---

## Task 2: `update_seed_blob` helper (optio-agents)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/seeds.py`
- Test: `packages/optio-agents/tests/test_seeds.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_update_seed_blob_swaps_blobid_and_stamps_updatedat(mongo_db):
    old = ObjectId()
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=old, manifest_version=1,
    )
    new = ObjectId()
    await seeds.update_seed_blob(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id, new_blob_id=new,
    )
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] == new
    assert "updatedAt" in doc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py::test_update_seed_blob_swaps_blobid_and_stamps_updatedat -v`
Expected: FAIL with `AttributeError: module 'optio_agents.seeds' has no attribute 'update_seed_blob'`.

- [ ] **Step 3: Implement the helper**

Add after `insert_seed` (around `seeds.py:77`):

```python
async def update_seed_blob(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
    new_blob_id: "ObjectId",
) -> None:
    """Point an existing seed doc at a new blob and stamp `updatedAt`.

    Used by `refresh_seed` for in-place credential save-back; the seed id is
    stable, only the blob changes."""
    from bson import ObjectId

    await _collection(db, prefix, suffix).update_one(
        {"_id": ObjectId(seed_id)},
        {"$set": {"blobId": new_blob_id, "updatedAt": datetime.now(timezone.utc)}},
    )
```

(`datetime`/`timezone` are already imported at the top of `seeds.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py::test_update_seed_blob_swaps_blobid_and_stamps_updatedat -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py
git commit -m "feat(seeds): add update_seed_blob for in-place blob swap"
```

---

## Task 3: `_merge_tar_members` in-memory overlay (optio-agents)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/seeds.py`
- Test: `packages/optio-agents/tests/test_seeds.py`

- [ ] **Step 1: Write the failing test**

```python
def _mk_targz(members: dict) -> bytes:
    import io, tarfile, time

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_merge_tar_members_overrides_and_preserves():
    import io, tarfile

    base = _mk_targz({"a.txt": b"OLD-A", "b.txt": b"B"})
    overlay = _mk_targz({"a.txt": b"NEW-A"})
    merged = seeds._merge_tar_members(base, overlay)
    with tarfile.open(fileobj=io.BytesIO(merged), mode="r:gz") as tar:
        got = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    assert got == {"a.txt": b"NEW-A", "b.txt": b"B"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py::test_merge_tar_members_overrides_and_preserves -v`
Expected: FAIL with `AttributeError: ... has no attribute '_merge_tar_members'`.

- [ ] **Step 3: Implement the function**

Add near the other tar helpers (after `_extract_seed`):

```python
def _merge_tar_members(base_gz: bytes, overlay_gz: bytes) -> bytes:
    """Return a new tar.gz = base with `overlay`'s members overwriting any
    same-named base member; all other base members are preserved. Pure
    in-memory; no host access."""
    import io
    import tarfile

    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(overlay_gz), mode="r:gz") as ov:
        ov_members = ov.getmembers()
        overlay_names = {m.name for m in ov_members}
        with tarfile.open(fileobj=out, mode="w:gz") as w:
            with tarfile.open(fileobj=io.BytesIO(base_gz), mode="r:gz") as base:
                for m in base.getmembers():
                    if m.name in overlay_names:
                        continue
                    w.addfile(m, base.extractfile(m) if m.isfile() else None)
            for m in ov_members:
                w.addfile(m, ov.extractfile(m) if m.isfile() else None)
    return out.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py::test_merge_tar_members_overrides_and_preserves -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py
git commit -m "feat(seeds): add in-memory tar member overlay merge"
```

---

## Task 4: `refresh_seed` — in-place credential save-back (optio-agents)

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/seeds.py`
- Test: `packages/optio-agents/tests/test_seeds.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_refresh_seed_replaces_credentials_in_place(mongo_db, tmp_workdir):
    import os

    # capture a full seed with creds={"token":"x"}
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "rsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    # a NEW live home whose creds have rotated
    live = LocalHost(taskdir=os.path.join(tmp_workdir, "rlive"))
    await live.setup_workdir()
    claude = os.path.join(live.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "ROTATED"}')

    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    await seeds.refresh_seed(
        ctx, live, seed_id=seed_id, manifest=narrow, suffix=SUFFIX,
        encrypt=None, decrypt=None,
    )

    # seed id unchanged; blob swapped; updatedAt stamped
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] != old_blob
    assert "updatedAt" in doc

    # merging the refreshed seed yields the rotated creds, and the rest of the
    # full environment is still intact
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "rdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "ROTATED"}'
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude", "plugins"))

    # old blob removed
    import gridfs
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    with pytest.raises(gridfs.errors.NoFile):
        await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(old_blob)


async def test_refresh_seed_unknown_id_raises(mongo_db, tmp_workdir):
    import os

    live = LocalHost(taskdir=os.path.join(tmp_workdir, "rk"))
    await live.setup_workdir()
    ctx = await _local_ctx(mongo_db, live.taskdir)
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    with pytest.raises(KeyError):
        await seeds.refresh_seed(
            ctx, live, seed_id=str(ObjectId()), manifest=narrow, suffix=SUFFIX,
            encrypt=None, decrypt=None,
        )


async def test_refresh_seed_crash_before_doc_update_keeps_old_blob(
    mongo_db, tmp_workdir, monkeypatch,
):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "csrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    live = LocalHost(taskdir=os.path.join(tmp_workdir, "clive"))
    await live.setup_workdir()
    claude = os.path.join(live.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "ROTATED"}')

    async def boom(*a, **k):
        raise RuntimeError("simulated crash before doc update")

    monkeypatch.setattr(seeds, "update_seed_blob", boom)
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    with pytest.raises(RuntimeError):
        await seeds.refresh_seed(
            ctx, live, seed_id=seed_id, manifest=narrow, suffix=SUFFIX,
            encrypt=None, decrypt=None,
        )

    # doc still points at the original blob and still decodes to the old creds
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] == old_blob
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "cdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "x"}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py -k refresh_seed -v`
Expected: FAIL with `AttributeError: ... has no attribute 'refresh_seed'`.

- [ ] **Step 3: Implement `refresh_seed`**

Add after `merge_seed` (around `seeds.py:249`):

```python
async def refresh_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Merge the live host's `manifest.include` files INTO an existing seed,
    in place: the seed id is stable, only the blob is replaced.

    Crash-safe ordering: store the new blob fully, then atomically repoint the
    doc, then delete the old blob. A crash at any point leaves at worst an
    orphan GridFS blob; the doc never points at a half-written blob.

    Raises KeyError if `seed_id` is unknown.
    """
    doc = await load_seed(ctx._db, prefix=ctx._prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    old_blob_id = doc["blobId"]

    dec = decrypt or (lambda b: b)
    enc = encrypt or (lambda b: b)
    base = dec(await _read_blob_bytes(ctx, old_blob_id))
    overlay = await _archive_include(
        host, home_subdir=manifest.home_subdir, include=manifest.include,
    )
    merged = _merge_tar_members(base, overlay)
    payload = enc(merged)

    async with ctx.store_blob("seed") as writer:
        await writer.write(payload)
        new_blob_id = writer.file_id

    await update_seed_blob(
        ctx._db, prefix=ctx._prefix, suffix=suffix,
        seed_id=seed_id, new_blob_id=new_blob_id,
    )
    await ctx.delete_blob(old_blob_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py -k refresh_seed -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py
git commit -m "feat(seeds): in-place refresh_seed for credential save-back"
```

---

## Task 5: Narrow credential manifest (optio-claudecode)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/seed_manifest.py`
- Test: `packages/optio-claudecode/tests/test_seed_config.py` (add cases)

- [ ] **Step 1: Write the failing test**

Add to `packages/optio-claudecode/tests/test_seed_config.py`:

```python
def test_cred_manifest_is_credentials_only():
    from optio_claudecode.seed_manifest import (
        CLAUDE_CRED_MANIFEST, CLAUDE_SEED_MANIFEST,
    )

    assert CLAUDE_CRED_MANIFEST.include == [".claude/.credentials.json"]
    # narrow manifest needs no rekey transform
    assert CLAUDE_CRED_MANIFEST.consume_transform is None
    # full manifest is composed FROM the narrow one (no duplicated path)
    assert CLAUDE_SEED_MANIFEST.include[:1] == CLAUDE_CRED_MANIFEST.include
    assert ".claude/plugins" in CLAUDE_SEED_MANIFEST.include
    assert CLAUDE_SEED_MANIFEST.consume_transform is not None
```

(If `test_seed_config.py` does not already exist with these imports, this still works as a standalone test module.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_seed_config.py::test_cred_manifest_is_credentials_only -v`
Expected: FAIL with `ImportError: cannot import name 'CLAUDE_CRED_MANIFEST'`.

- [ ] **Step 3: Add the narrow manifest and compose the full one**

In `seed_manifest.py`, replace the `CLAUDE_SEED_MANIFEST = ...` block (currently lines 52-63) with:

```python
CLAUDE_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".claude/.credentials.json"],
    version=CLAUDE_SEED_MANIFEST_VERSION,
)


CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CLAUDE_CRED_MANIFEST.include + [
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/plugins",
        ".claude.json",
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_seed_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/seed_manifest.py packages/optio-claudecode/tests/test_seed_config.py
git commit -m "feat(claudecode): add narrow credential seed manifest"
```

---

## Task 6: Credential watcher helpers (optio-claudecode)

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/cred_watcher.py`
- Test: `packages/optio-claudecode/tests/test_cred_watcher.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_cred_watcher.py`:

```python
"""Unit tests for the credential watcher helpers (LocalHost)."""

import os

from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_claudecode import cred_watcher
from optio_claudecode.seed_manifest import CLAUDE_CRED_MANIFEST, CLAUDE_SEED_SUFFIX


def _write_creds(host_workdir: str, refresh_token: str) -> None:
    claude = os.path.join(host_workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"claudeAiOauth": {"refreshToken": "%s"}}' % refresh_token)


async def test_cred_fingerprint_none_when_missing(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "m"))
    await host.setup_workdir()
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_cred_fingerprint_none_when_malformed_or_no_token(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "b"))
    await host.setup_workdir()
    claude = os.path.join(host.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write("not json")
    assert await cred_watcher.cred_fingerprint(host) is None
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"claudeAiOauth": {"refreshToken": ""}}')
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_cred_fingerprint_changes_with_content(tmp_workdir):
    host = LocalHost(taskdir=os.path.join(tmp_workdir, "c"))
    await host.setup_workdir()
    _write_creds(host.workdir, "T1")
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_creds(host.workdir, "T2")
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1
```

(The `tmp_workdir` fixture is provided by the optio-claudecode test conftest, same as the seed tests use.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_cred_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'optio_claudecode.cred_watcher'`.

- [ ] **Step 3: Implement the fingerprint + save-back helpers**

Create `cred_watcher.py`:

```python
"""In-session credential save-back for claudecode seeds.

Claude Code OAuth refresh tokens rotate (single-use): each refresh issues a new
token and invalidates the old. This watcher keeps the seed current by writing
refreshed credentials back into the existing seed whenever the in-session
`.claude/.credentials.json` changes, plus a final backstop at teardown.

The seed is the single source of truth for credentials; see
docs/superpowers/specs/2026-06-05-claudecode-seed-saveback-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_agents import seeds
from optio_host.host import Host

from optio_claudecode.seed_manifest import CLAUDE_CRED_MANIFEST, CLAUDE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.claude/.credentials.json"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live credentials file, or None when it is missing,
    unparseable, or carries no non-empty refresh token (i.e. nothing worth
    saving back). Guards against corrupting a seed with logged-out/half-written
    credentials."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        token = data["claudeAiOauth"]["refreshToken"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        return None
    if not token:
        return None
    return hashlib.sha256(raw).hexdigest()


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live credentials differ from `baseline` and are valid, save them
    back into the seed and return the new fingerprint. Otherwise return
    `baseline` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=CLAUDE_CRED_MANIFEST,
            suffix=CLAUDE_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: credentials saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: credential save-back failed", seed_id)
        return baseline
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_cred_watcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/cred_watcher.py packages/optio-claudecode/tests/test_cred_watcher.py
git commit -m "feat(claudecode): credential fingerprint + save-back helpers"
```

---

## Task 7: Watcher poll loop (optio-claudecode)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/cred_watcher.py`
- Test: `packages/optio-claudecode/tests/test_cred_watcher.py`

- [ ] **Step 1: Write the failing test**

Add to `test_cred_watcher.py`:

```python
import asyncio

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_credwatch_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _ctx(mongo_db, taskdir):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_run_credential_watcher_saves_on_change_then_cancels(
    mongo_db, tmp_workdir, monkeypatch,
):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)

    host = LocalHost(taskdir=os.path.join(tmp_workdir, "w"))
    await host.setup_workdir()
    _write_creds(host.workdir, "T1")
    ctx = await _ctx(mongo_db, host.taskdir)

    # seed the customer's seed with T1
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CLAUDE_CRED_MANIFEST, suffix=CLAUDE_SEED_SUFFIX, encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    # rotate creds; the watcher should pick it up within a few intervals
    _write_creds(host.workdir, "T2")
    for _ in range(40):
        await asyncio.sleep(0.05)
        dst = LocalHost(taskdir=os.path.join(tmp_workdir, f"chk{_}"))
        await dst.setup_workdir()
        await seeds.merge_seed(
            ctx, dst, seed_id=seed_id, manifest=CLAUDE_CRED_MANIFEST,
            suffix=CLAUDE_SEED_SUFFIX, decrypt=None,
        )
        with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
            if "T2" in fh.read():
                break
    else:
        task.cancel()
        raise AssertionError("watcher did not save back the rotated credentials")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_cred_watcher.py::test_run_credential_watcher_saves_on_change_then_cancels -v`
Expected: FAIL with `AttributeError: module 'optio_claudecode.cred_watcher' has no attribute 'run_credential_watcher'`.

- [ ] **Step 3: Implement the loop**

Append to `cred_watcher.py`:

```python
async def run_credential_watcher(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Poll the live credentials every CRED_WATCH_INTERVAL_S; save back to the
    seed on change. Runs until cancelled. Best-effort: a save-back failure is
    logged and the loop continues."""
    current = baseline
    while True:
        await asyncio.sleep(CRED_WATCH_INTERVAL_S)
        current = await save_back_if_changed(
            ctx, host, seed_id=seed_id, baseline=current,
            encrypt=encrypt, decrypt=decrypt,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_cred_watcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/cred_watcher.py packages/optio-claudecode/tests/test_cred_watcher.py
git commit -m "feat(claudecode): credential watcher poll loop"
```

---

## Task 8: Wire watcher, final backstop, and resume overlay into session.py

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Test: `packages/optio-claudecode/tests/test_session_seed_saveback.py` (new)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-claudecode/tests/test_session_seed_saveback.py`:

```python
"""End-to-end: a seeded session that rotates its credentials saves them back
to the seed (via the teardown backstop)."""

import asyncio
import os

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_agents import seeds
from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.seed_manifest import CLAUDE_SEED_MANIFEST, CLAUDE_SEED_SUFFIX
from optio_claudecode.session import run_claudecode_session


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_saveback_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=False,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


async def test_session_saves_rotated_credentials_back_to_seed(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "seed")

    # 1) capture an initial seed (creds token "x" from the seed scenario plant)
    captured: list[str] = []

    async def _on_seed_saved(seed_id, info=None) -> None:
        captured.append(seed_id)

    ctx1 = await _make_ctx(mongo_db, "cc_sb_src")
    await run_claudecode_session(ctx1, ClaudeCodeTaskConfig(
        consumer_instructions="(seed setup)",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        on_seed_saved=_on_seed_saved,
    ))
    seed_id = captured[0]

    # 2) a seeded session whose before_execute rotates the credentials file on
    # the host; the teardown backstop must save it back to THIS seed.
    async def _rotate(hook_ctx):
        host = hook_ctx._host
        await host.put_file_to_host(
            b'{"claudeAiOauth": {"refreshToken": "ROTATED"}}',
            f"{host.workdir}/home/.claude/.credentials.json",
        )

    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx2 = await _make_ctx(mongo_db, "cc_sb_run")
    await run_claudecode_session(ctx2, ClaudeCodeTaskConfig(
        consumer_instructions="(seeded run)",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=False,
        seed_id=seed_id,
        before_execute=_rotate,
    ))

    # 3) the seed now carries the rotated credentials
    dst = ctx2  # reuse a ctx only for blob I/O; merge needs a fresh host
    from optio_host.host import LocalHost
    check = LocalHost(taskdir=os.path.join(task_root, "sb_check"))
    await check.setup_workdir()
    await seeds.merge_seed(
        ctx2, check, seed_id=seed_id, manifest=CLAUDE_SEED_MANIFEST,
        suffix=CLAUDE_SEED_SUFFIX, decrypt=None,
    )
    with open(os.path.join(check.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert "ROTATED" in fh.read()
```

> Note: the seed-capture session uses no encryption (`session_blob_encrypt` unset), so the save-back path likewise runs with `encrypt=None`/`decrypt=None`. The session must thread `config.session_blob_encrypt`/`session_blob_decrypt` into the watcher and backstop (Step 3).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_session_seed_saveback.py -v`
Expected: FAIL — the seed still carries the original `"x"` token (no save-back wired yet).

- [ ] **Step 3: Wire into `session.py`**

3a. Add imports near the existing seed imports at the top of `session.py`:

```python
from optio_claudecode import cred_watcher
from optio_claudecode.seed_manifest import CLAUDE_CRED_MANIFEST
```

3b. Declare watcher state alongside the other `nonlocal`-tracked locals in `run_claudecode_session` (near `launched_handle`, before the `try`):

```python
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
```

3c. In `_claudecode_body`, after the **fresh-start** credentials are laid down — immediately after the `merge_seed` block (currently ends at `session.py:201`, inside `if config.seed_id is not None:`) — establish the baseline:

```python
                _trace("body: merge_seed DONE")
                cred_baseline = await cred_watcher.cred_fingerprint(host)
```

Add `cred_baseline` and `cred_watch_task` to the `nonlocal` declaration at the top of `_claudecode_body` (line 170 currently lists `launched_handle, tmux_path, tmux_socket, tmux_session`).

3d. In `_claudecode_body`, change the **resume** branch (currently `session.py:211-214`) to overlay the seed's current credentials onto the restored home and set the baseline:

```python
        else:
            # Resume: home/.claude (credentials, settings) was restored from
            # the session blob. Overlay the seed's CURRENT credentials on top
            # (the seed is the source of truth for creds; the snapshot may carry
            # a now-rotated/dead token). Non-credential home files are untouched.
            if config.seed_id is not None:
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=config.seed_id,
                    manifest=CLAUDE_CRED_MANIFEST,
                    suffix=CLAUDE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
            cred_baseline = await cred_watcher.cred_fingerprint(host)
            refreshed_files = await _maybe_refresh_on_resume(host, hook_ctx, config)
```

3e. Update the resume guard warning (currently `session.py:135-139`) — `seed_id` is now used for the credential overlay; only `on_seed_saved` remains ignored on resume:

```python
            if config.on_seed_saved is not None:
                _LOG.warning(
                    "resume: on_seed_saved ignored (no full capture on resume); "
                    "seed_id is still used to overlay current credentials",
                )
```

3f. Start the watcher just before the tmux-alive loop (currently `session.py:284`), and stop it right after the loop:

```python
        if config.seed_id is not None:
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=config.seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
            ))

        while await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass
            cred_watch_task = None
```

3g. Add the **final backstop** in the outer `finally`, after `await_claude_gone` (currently `session.py:346-348`) and before the `capture_seed` block (currently `session.py:350`). Also defensively cancel the watcher in case the body raised before its own cancel:

```python
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        if config.seed_id is not None:
            try:
                await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=config.seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-claudecode/tests/test_session_seed_saveback.py packages/optio-claudecode/tests/test_session_seed_consume.py packages/optio-claudecode/tests/test_session_resume.py -v`
Expected: PASS — save-back works end-to-end and the existing seed-consume/resume behavior is unbroken.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-claudecode/tests/test_session_seed_saveback.py
git commit -m "feat(claudecode): save refreshed credentials back to the seed"
```

---

## Task 9: Full regression + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the optio-agents + optio-claudecode suites**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests packages/optio-claudecode/tests -v`
Expected: PASS (no regressions).

- [ ] **Step 2: Lint/type per repo convention**

Run the repo's configured linter (e.g. `cd ~/deai/optio && make lint` or `ruff check packages/optio-agents packages/optio-claudecode`).
Expected: clean.

- [ ] **Step 3: Commit any lint fixes**

```bash
cd ~/deai/optio
git add -A
git commit -m "chore(claudecode): lint fixes for seed save-back" || echo "nothing to commit"
```

---

## Self-Review Notes

- **Spec coverage:** Goals 1-4 map to Tasks 6/7 (watcher + final backstop), 1/4 (narrow inject + refresh_seed), 8 (no-callback wiring + resume overlay), 6 (validity guard). Non-goals (concurrency, non-cred files) are untouched. Affected-files list matches Tasks 1-8; "no engine changes" holds.
- **Type/name consistency:** `cred_fingerprint`, `save_back_if_changed`, `run_credential_watcher`, `refresh_seed`, `update_seed_blob`, `_merge_tar_members`, `CLAUDE_CRED_MANIFEST`, `CRED_WATCH_INTERVAL_S` are used identically across tasks. `refresh_seed` takes both `encrypt` and `decrypt`; `merge_seed`/`_extract_seed` gain `include`.
- **Open risk to watch during execution:** the exact line numbers in Task 8 (`session.py`) are from base `c542dec`; if drift moved them, locate by the quoted anchor code, not the number.
