# Pooled / Leased Claude Code Seeds -- optio-claudecode Plumbing (Spec B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. **This plan is PARALLEL-SHAPED:** the three implementation tasks are **edit-only** and run concurrently as one wave -- they do NOT run tests, lint, or git. A single final **Verify phase** runs all tests, fixes cross-task breakage, lints, and commits. No per-task test/commit steps.

**Goal:** Let a claudecode session draw its seed from a leased pool and verify/refresh it host-free before use -- the optio-claudecode library plumbing the excavator app (Spec B2) builds on.

**Architecture:** Three disjoint areas: (A) two generic optio-agents seed ops (`declare_metadata`, host-free `overwrite_seed_member`); (B) a new optio-claudecode `oauth.py` with the host-free OAuth/verify functions (`validate_token`, `fetch_usage`, `refresh_oauth_token`, `summarize_profile`, `verify_and_refresh_seed`); (C) session integration (provider-resolved seed_id, lease renew/release/abort in the keepalive loop). A/B/C touch disjoint files and code to the shared contract below.

**Tech Stack:** Python 3, asyncio, motor (Mongo/GridFS), urllib in executor, Python tarfile, pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-06-05-pooled-leased-seeds-claudecode-lib-design.md`. Builds on the shipped pool layer (`695918c`) and credential save-back.

**Test runner:** `cd ~/deai/optio && .venv/bin/python -m pytest <path> -v` (local Mongo at `mongodb://localhost:27017`).

---

## File Structure

- `packages/optio-agents/src/optio_agents/seeds.py` -- `declare_metadata`, `overwrite_seed_member` (Owner A).
- `packages/optio-claudecode/src/optio_claudecode/oauth.py` (NEW) -- OAuth/verify functions (Owner B).
- `packages/optio-claudecode/src/optio_claudecode/types.py` + `session.py` + `cred_watcher.py` -- provider + lease wiring (Owner C).
- Tests: `packages/optio-agents/tests/test_seeds.py`, `packages/optio-claudecode/tests/test_oauth.py` (NEW), `packages/optio-claudecode/tests/test_seed_provider.py` (NEW).

**Shared contract (all owners code to this verbatim):**

```python
# optio_agents/seeds.py
async def declare_metadata(db, *, prefix, suffix, seed_id, metadata: dict) -> None
async def overwrite_seed_member(db, *, prefix, suffix, seed_id, member_path: str,
                                content: bytes, encrypt, decrypt) -> None

# optio_claudecode/oauth.py
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
USER_AGENT = "claude-cli/2.1.165 (external, cli)"
async def validate_token(access_token: str) -> bool
async def fetch_usage(access_token: str) -> dict | None
async def refresh_oauth_token(refresh_token: str) -> dict | None   # None on invalid_grant
async def summarize_profile(access_token: str) -> dict | None      # {"uuid", "summary"}
async def verify_and_refresh_seed(db, *, prefix, suffix, seed_id, encrypt, decrypt) -> dict
#   -> {"alive": bool, "usage": dict|None, "account": {"uuid","summary"}|None}

# optio_claudecode/types.py
SeedProvider = Callable[[str], Awaitable[str]]   # (process_id) -> seed_id
class SeedUnavailableError(Exception): ...
# ClaudeCodeTaskConfig.seed_id: "str | SeedProvider | None"
```

---

## Task A (WAVE, edit-only): generic seed ops in `seeds.py`

**Owner files:** `packages/optio-agents/src/optio_agents/seeds.py`, `packages/optio-agents/tests/test_seeds.py`. Edit only -- no tests/lint/git.

- [ ] **Step 1: Append the two ops to `seeds.py`**

Append at end of `seeds.py`. `_collection`, `datetime`, `timezone`, `_merge_tar_members`, `_read_blob_bytes`, `update_seed_blob`, `load_seed`, `_oid_or_none` already exist.

```python
async def declare_metadata(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, metadata: dict,
) -> None:
    """$set-merge opaque `metadata.<k>` keys onto a seed doc. The pool stores
    metadata opaquely; consumers own its meaning."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return
    sets = {f"metadata.{k}": v for k, v in metadata.items()}
    sets["updatedAt"] = datetime.now(timezone.utc)
    await _collection(db, prefix, suffix).update_one({"_id": oid}, {"$set": sets})


def _single_member_targz(member_path: str, content: bytes) -> bytes:
    """A tar.gz containing exactly one file member."""
    import io
    import tarfile

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_path)
        info.size = len(content)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


async def overwrite_seed_member(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
    member_path: str, content: bytes,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Host-free: overwrite one member of a seed's tar blob with `content`,
    in place. Crash-safe blob swap (store new -> repoint doc -> delete old).
    Reads/writes GridFS from `db` directly (no ProcessContext). Raises KeyError
    if the seed is unknown."""
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    old_blob_id = doc["blobId"]
    bucket = AsyncIOMotorGridFSBucket(db)

    import io
    buf = io.BytesIO()
    await bucket.download_to_stream(old_blob_id, buf)
    dec = decrypt or (lambda b: b)
    enc = encrypt or (lambda b: b)
    base = dec(buf.getvalue())
    overlay = _single_member_targz(member_path, content)
    merged = _merge_tar_members(base, overlay)
    new_blob_id = await bucket.upload_from_stream("seed", enc(merged))

    await update_seed_blob(
        db, prefix=prefix, suffix=suffix, seed_id=seed_id, new_blob_id=new_blob_id,
    )
    try:
        await bucket.delete(old_blob_id)
    except Exception:
        pass
```

- [ ] **Step 2: Append tests to `test_seeds.py`**

```python
async def test_declare_metadata_merges_keys(mongo_db):
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    await seeds.declare_metadata(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, metadata={"usage": {"a": 1}},
    )
    await seeds.declare_metadata(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, metadata={"account": {"uuid": "u1"}},
    )
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["metadata"]["usage"] == {"a": 1}
    assert doc["metadata"]["account"] == {"uuid": "u1"}


async def test_overwrite_seed_member_replaces_in_place(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "ov"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    await seeds.overwrite_seed_member(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id,
        member_path=".claude/.credentials.json", content=b'{"token": "NEW"}',
        encrypt=None, decrypt=None,
    )

    # blob swapped; member replaced; other members intact; reflected by merge
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] != old_blob
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "ovdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "NEW"}'
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))
    import gridfs
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    with pytest.raises(gridfs.errors.NoFile):
        await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(old_blob)
```

---

## Task B (WAVE, edit-only): host-free OAuth/verify module `oauth.py`

**Owner files:** `packages/optio-claudecode/src/optio_claudecode/oauth.py` (NEW), `packages/optio-claudecode/tests/test_oauth.py` (NEW). Edit only. (Imports `seeds.overwrite_seed_member`/`declare_metadata` from Task A -- expected absent in your working copy.)

- [ ] **Step 1: Create `oauth.py`**

```python
"""Host-free Claude Code OAuth + seed verification.

Works from a decrypted seed blob (no session host): validate / refresh-and-
save-back / fetch account + usage / stamp raw results as seed metadata. Reused
by the excavator `gimme` provider (per checkout) and the verify-free action.

OAuth facts verified 2026-06-05 (see the seed-maintenance specs): refresh tokens
rotate single-use; all calls need the claude-cli User-Agent.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from optio_agents import seeds

from optio_claudecode.account import format_account_summary
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
USER_AGENT = "claude-cli/2.1.165 (external, cli)"
_BETA = "oauth-2025-04-20"

_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_VALIDATE_URL = "https://platform.claude.com/api/oauth/validate"
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

_CRED_MEMBER = ".claude/.credentials.json"


def _req(url, *, method, access_token=None, body=None):
    headers = {"User-Agent": USER_AGENT, "anthropic-beta": _BETA}
    data = None
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    return urllib.request.Request(url, headers=headers, data=data, method=method)


def _validate_sync(access_token: str) -> bool:
    try:
        with urllib.request.urlopen(
            _req(_VALIDATE_URL, method="POST", access_token=access_token, body={}), timeout=15,
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("valid"))
    except HTTPError:
        return False
    except (URLError, OSError, ValueError):
        return False


def _usage_sync(access_token: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            _req(_USAGE_URL, method="GET", access_token=access_token), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


def _refresh_sync(refresh_token: str) -> dict | None:
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    try:
        with urllib.request.urlopen(
            _req(_TOKEN_URL, method="POST", body=body), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError:
        return None  # invalid_grant / 4xx -> dead lineage
    except (URLError, OSError, ValueError):
        return None


def _profile_sync(access_token: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            _req(_PROFILE_URL, method="GET", access_token=access_token), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


async def validate_token(access_token: str) -> bool:
    return await _in_executor(_validate_sync, access_token)


async def fetch_usage(access_token: str) -> dict | None:
    return await _in_executor(_usage_sync, access_token)


async def refresh_oauth_token(refresh_token: str) -> dict | None:
    return await _in_executor(_refresh_sync, refresh_token)


async def summarize_profile(access_token: str) -> dict | None:
    profile = await _in_executor(_profile_sync, access_token)
    if not isinstance(profile, dict):
        return None
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    uuid = account.get("uuid")
    return {"uuid": uuid, "summary": format_account_summary(profile)}


def _read_seed_creds(blob_plain: bytes) -> dict | None:
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_CRED_MEMBER)
            if f is None:
                return None
            return json.loads(f.read().decode("utf-8")).get("claudeAiOauth")
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None


def _build_creds_json(oauth: dict, token_resp: dict) -> bytes:
    """New .credentials.json bytes from a refresh response, preserving scopes/
    subscription where the response omits them."""
    new = dict(oauth)
    new["accessToken"] = token_resp["access_token"]
    new["refreshToken"] = token_resp["refresh_token"]
    expires_in = token_resp.get("expires_in") or 0
    # server clock not available here; expiry is advisory (claude re-checks).
    new["expiresAt"] = int(datetime.now(timezone.utc).timestamp() * 1000) + expires_in * 1000
    if token_resp.get("scope"):
        new["scopes"] = token_resp["scope"].split()
    return json.dumps({"claudeAiOauth": new}).encode("utf-8")


async def verify_and_refresh_seed(
    db, *, prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id, encrypt, decrypt,
) -> dict:
    """Verify a seed host-free; refresh + save back if needed; stamp raw usage +
    account as metadata. Returns {alive, usage, account}. Never raises for a
    dead/limited seed -- a dead lineage is alive=False."""
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "usage": None, "account": None}
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    oauth = _read_seed_creds(dec(buf.getvalue()))
    if not oauth or not oauth.get("refreshToken"):
        return {"alive": False, "usage": None, "account": None}

    access = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt") or 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    need_refresh = expires_at <= now_ms
    if not need_refresh:
        need_refresh = not await validate_token(access)

    if need_refresh:
        resp = await refresh_oauth_token(oauth["refreshToken"])
        if resp is None:
            return {"alive": False, "usage": None, "account": None}
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_CRED_MEMBER, content=_build_creds_json(oauth, resp),
            encrypt=encrypt, decrypt=decrypt,
        )
        access = resp["access_token"]

    usage = await fetch_usage(access)
    account = await summarize_profile(access)
    await seeds.declare_metadata(
        db, prefix=prefix, suffix=suffix, seed_id=seed_id,
        metadata={
            "usage": usage,
            "usageFetchedAt": datetime.now(timezone.utc),
            "account": account,
        },
    )
    return {"alive": True, "usage": usage, "account": account}
```

- [ ] **Step 2: Create `test_oauth.py`**

```python
"""Unit tests for host-free OAuth/verify (stubbed network)."""

import os

import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_claudecode import oauth


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = f"optio_cc_oauth_{os.getpid()}"
    db = client[name]
    yield db
    await client.drop_database(name)
    client.close()


SUFFIX = seeds  # placeholder, replaced below


async def _ctx(mongo_db, taskdir):
    import asyncio
    from optio_core.context import ProcessContext
    oid = ObjectId()
    await mongo_db["t_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="t", cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


def _plant_creds(workdir, access, refresh, expires_at):
    claude = os.path.join(workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    import json
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        json.dump({"claudeAiOauth": {
            "accessToken": access, "refreshToken": refresh, "expiresAt": expires_at,
            "scopes": ["user:inference"], "subscriptionType": "max",
        }}, fh)


async def _seed_with_creds(mongo_db, tmp_workdir, name, *, access, refresh, expires_at):
    src = LocalHost(taskdir=os.path.join(tmp_workdir, name))
    await src.setup_workdir()
    _plant_creds(src.workdir, access, refresh, expires_at)
    ctx = await _ctx(mongo_db, src.taskdir)
    manifest = seeds.SeedManifest(home_subdir="home", include=[".claude/.credentials.json"], version=1)
    sid = await seeds.capture_seed(ctx, src, manifest=manifest, suffix="_cc_seeds", encrypt=None)
    return sid


async def test_verify_fresh_valid_token_no_refresh(mongo_db, tmp_workdir, monkeypatch):
    future = 9999999999999
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v1", access="AT", refresh="RT", expires_at=future)

    async def fake_validate(t): assert t == "AT"; return True
    async def fake_usage(t): return {"five_hour": {"utilization": 1.0, "resets_at": None}}
    async def fake_profile(t): return {"uuid": "u1", "summary": "Plan: Max for <a@b>"}
    async def fail_refresh(rt): raise AssertionError("must not refresh a valid token")
    monkeypatch.setattr(oauth, "validate_token", fake_validate)
    monkeypatch.setattr(oauth, "fetch_usage", fake_usage)
    monkeypatch.setattr(oauth, "summarize_profile", fake_profile)
    monkeypatch.setattr(oauth, "refresh_oauth_token", fail_refresh)

    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is True
    assert res["account"] == {"uuid": "u1", "summary": "Plan: Max for <a@b>"}
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid)
    assert "usage" in doc["metadata"] and doc["metadata"]["account"]["uuid"] == "u1"


async def test_verify_expired_refreshes_and_saves_back(mongo_db, tmp_workdir, monkeypatch):
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v2", access="OLD", refresh="RT", expires_at=1)

    async def fake_refresh(rt):
        assert rt == "RT"
        return {"access_token": "NEW_AT", "refresh_token": "NEW_RT", "expires_in": 28800, "scope": "user:inference"}
    async def fake_usage(t): assert t == "NEW_AT"; return {"five_hour": {"utilization": 1.0}}
    async def fake_profile(t): return {"uuid": "u2", "summary": "s"}
    monkeypatch.setattr(oauth, "refresh_oauth_token", fake_refresh)
    monkeypatch.setattr(oauth, "fetch_usage", fake_usage)
    monkeypatch.setattr(oauth, "summarize_profile", fake_profile)

    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is True
    # creds saved back: re-read the seed's credentials member
    import io, tarfile, json
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid)
    b = io.BytesIO(); await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], b)
    with tarfile.open(fileobj=io.BytesIO(b.getvalue()), mode="r:gz") as tar:
        creds = json.loads(tar.extractfile(".claude/.credentials.json").read())["claudeAiOauth"]
    assert creds["accessToken"] == "NEW_AT" and creds["refreshToken"] == "NEW_RT"


async def test_verify_dead_on_invalid_grant(mongo_db, tmp_workdir, monkeypatch):
    sid = await _seed_with_creds(mongo_db, tmp_workdir, "v3", access="OLD", refresh="RT", expires_at=1)

    async def dead_refresh(rt): return None
    monkeypatch.setattr(oauth, "refresh_oauth_token", dead_refresh)
    res = await oauth.verify_and_refresh_seed(
        mongo_db, prefix="t", suffix="_cc_seeds", seed_id=sid, encrypt=None, decrypt=None,
    )
    assert res["alive"] is False
```

> Note: drop the stray `SUFFIX = seeds` placeholder line if your transcription includes it; tests use the literal suffix `"_cc_seeds"`. The verify phase will catch it.

---

## Task C (WAVE, edit-only): provider + lease wiring in the session

**Owner files:** `packages/optio-claudecode/src/optio_claudecode/types.py`, `session.py`, `cred_watcher.py`, and `packages/optio-claudecode/tests/test_seed_provider.py` (NEW). Edit only.

- [ ] **Step 1: `types.py` -- widen `seed_id`, add provider alias + error**

Add near the top imports (where `Callable`/`Awaitable` are imported; add if absent):

```python
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the message
    is surfaced as the process failure."""
```

Change the `seed_id` field (currently `seed_id: str | None = None`):

```python
    seed_id: "str | SeedProvider | None" = None
```

- [ ] **Step 2: `cred_watcher.py` -- renew the lease each tick; abort on lost lease**

Change `run_credential_watcher` to accept an optional `lease_holder` and renew per tick. Replace the function with:

```python
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
    """Poll every CRED_WATCH_INTERVAL_S: save back rotated creds, and (when
    `lease_holder` is set) renew the seed's lease. If the lease is lost, signal
    the session to stop (set the cancellation flag) and exit. Runs until
    cancelled. Best-effort save-back; lease-loss is decisive."""
    current = baseline
    while True:
        await asyncio.sleep(CRED_WATCH_INTERVAL_S)
        current = await save_back_if_changed(
            ctx, host, seed_id=seed_id, baseline=current,
            encrypt=encrypt, decrypt=decrypt,
        )
        if lease_holder is not None:
            ok = await seeds.renew_lease(
                ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
```

- [ ] **Step 3: `session.py` -- resolve provider, thread the resolved id, renew/release**

3a. Near the existing seed/watcher locals (currently `cred_baseline`/`cred_watch_task` at ~line 106), add:

```python
    resolved_seed_id: str | None = None
    lease_holder: str | None = None
```

3b. Add `resolved_seed_id, lease_holder` to the `nonlocal` declaration inside `_claudecode_body` (the line that currently lists `cred_baseline, cred_watch_task`).

3c. At the very start of `_claudecode_body` (before the fresh/resume branch that uses `config.seed_id`), resolve the provider once:

```python
        nonlocal resolved_seed_id, lease_holder
        if callable(config.seed_id):
            resolved_seed_id = await config.seed_id(ctx.process_id)  # may raise SeedUnavailableError
            lease_holder = ctx.process_id  # provider holds the lease under this holder
        else:
            resolved_seed_id = config.seed_id
```

3d. Replace every use of `config.seed_id` inside `_claudecode_body` and the teardown with `resolved_seed_id`. Specifically: the fresh-merge guard + `merge_seed(seed_id=...)` (~196-209), the resume narrow-overlay guard + `merge_seed` (~225-233), the watcher-start guard + `seed_id=` (~304-308), and the final-backstop guard + `seed_id=` (~394-399). (Use the surrounding text to locate them; do not rely on line numbers.) Do **not** change `config.on_seed_saved` references.

3e. Pass `lease_holder` to the watcher start (the `run_credential_watcher(...)` call ~line 305):

```python
            cred_watch_task = asyncio.create_task(cred_watcher.run_credential_watcher(
                ctx, host,
                seed_id=resolved_seed_id,
                baseline=cred_baseline,
                encrypt=config.session_blob_encrypt,
                decrypt=config.session_blob_decrypt,
                lease_holder=lease_holder,
            ))
```

3f. Make the tmux-alive loop also stop when the session is cancelled (so a lost-lease abort breaks it). Change the loop condition:

```python
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)
```

3g. Release the lease in teardown. In the outer `finally`, after the watcher is cancelled (the `if cred_watch_task is not None: cred_watch_task.cancel()...` block ~387) and before/after the final save-back backstop, add:

```python
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")
```

(`_seeds` is the module alias already imported in session.py; `CLAUDE_SEED_SUFFIX` is already imported.)

- [ ] **Step 4: Create `test_seed_provider.py`**

```python
"""Provider resolution + lease wiring (no real claude; unit-level)."""

import asyncio
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_claudecode import cred_watcher
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX
from optio_claudecode.types import SeedProvider, SeedUnavailableError
from optio_agents import seeds


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = f"optio_cc_provider_{os.getpid()}"
    db = client[name]
    yield db
    await client.drop_database(name)
    client.close()


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "proc-1"})
    return ProcessContext(
        process_oid=oid, process_id="proc-1", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test", cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


async def _pooled_seed(mongo_db, pool):
    sid = await seeds.insert_seed(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, blob_id=ObjectId(), manifest_version=1)
    await seeds.assign_to_pool(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, seed_id=sid, poolKey=pool)
    return sid


def test_seed_provider_type_and_error_exist():
    assert SeedProvider is not None
    with pytest.raises(SeedUnavailableError):
        raise SeedUnavailableError("seed shortage")


async def test_watcher_renews_lease_and_aborts_when_lost(mongo_db, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    ctx = await _ctx(mongo_db)
    sid = await _pooled_seed(mongo_db, "pool-1")
    # proc-1 holds the lease
    got = await seeds.acquire(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, poolKey="pool-1", holder="proc-1")
    assert got == sid

    # cred save-back is irrelevant here; stub it to a no-op
    async def noop_saveback(*a, **k): return None
    monkeypatch.setattr(cred_watcher, "save_back_if_changed", noop_saveback)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host=None, seed_id=sid, baseline=None, encrypt=None, decrypt=None,
        lease_holder="proc-1",
    ))
    await asyncio.sleep(0.2)  # several renew ticks; lease stays held
    assert ctx.should_continue() is True

    # steal the lease out from under proc-1: expire it, re-acquire as someone else
    from datetime import datetime, timezone, timedelta
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    await mongo_db[f"test{CLAUDE_SEED_SUFFIX}"].update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "proc-1", "expiresAt": past}}},
    )
    assert await seeds.acquire(mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, poolKey="pool-1", holder="thief") == sid

    await asyncio.sleep(0.2)  # next renew tick sees the loss
    assert ctx.cancellation_flag.is_set()  # watcher signalled abort
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

---

## Verify phase (single agent, after the wave): test, fix, lint, commit

- [ ] **Step 1: Run the three suites**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py packages/optio-claudecode/tests/test_oauth.py packages/optio-claudecode/tests/test_seed_provider.py -v`
Expected: pass.

- [ ] **Step 2: Fix cross-task drift**

Reconcile any signature/name mismatch against the Shared Contract + spec (`docs/superpowers/specs/2026-06-05-pooled-leased-seeds-claudecode-lib-design.md`). Known watch-items: tz-aware vs naive datetimes (the test motor client is `tz_aware=False` -- compare against naive UTC, as the pool layer already does); the stray `SUFFIX = seeds` placeholder in `test_oauth.py` (delete it); `run_credential_watcher` being called with `host=None` in the provider test (fine -- save-back is stubbed). Do NOT weaken tests; fix real causes.

- [ ] **Step 3: Full regression -- both packages**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests packages/optio-claudecode/tests -v`
Expected: PASS (no regression in the existing seed/session/save-back tests).

- [ ] **Step 4: Lint touched files**

Run: `cd ~/deai/optio && .venv/bin/ruff check packages/optio-agents/src/optio_agents/seeds.py packages/optio-claudecode/src/optio_claudecode/oauth.py packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-claudecode/src/optio_claudecode/cred_watcher.py packages/optio-claudecode/src/optio_claudecode/types.py packages/optio-agents/tests/test_seeds.py packages/optio-claudecode/tests/test_oauth.py packages/optio-claudecode/tests/test_seed_provider.py` (or `uvx ruff check ...`). Fix lint in touched files only.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py \
        packages/optio-claudecode/src/optio_claudecode/oauth.py packages/optio-claudecode/tests/test_oauth.py \
        packages/optio-claudecode/src/optio_claudecode/types.py \
        packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/src/optio_claudecode/cred_watcher.py \
        packages/optio-claudecode/tests/test_seed_provider.py
git commit -m "feat(claudecode): pooled/leased seed plumbing -- provider, lease wiring, host-free verify"
```

(Repo BANS the `Co-Authored-By` trailer -- omit it.)

---

## Self-Review Notes

- **Spec coverage:** provider interface + `SeedUnavailableError` (Task C/types); lease renew + lost-lease abort + release (Task C/cred_watcher+session); `verify_and_refresh_seed` decision tree + raw-usage/account metadata stamping (Task B); host-free credential write + `declare_metadata` (Task A); no-behavior-change for fixed-`str` path (lease ops gated on `lease_holder`/provider). In-session mid-run detection intentionally absent (deferred). No excavator changes.
- **Placeholder scan:** the one intentional artifact (`SUFFIX = seeds` in the oauth test) is called out for deletion in the verify phase; no other placeholders.
- **Type/name consistency:** the Shared Contract signatures match the impls and call-sites; `verify_and_refresh_seed` returns `{alive, usage, account}`; `run_credential_watcher(..., lease_holder=)` matches the session call; `overwrite_seed_member`/`declare_metadata` names match between Task A and Task B's calls.
- **Known approximations the verify phase will settle:** exact `session.py` anchor lines (locate by surrounding text); `expiresAt` computed from the app clock (advisory -- claude re-checks); the `_build_creds_json` scope/field preservation.
