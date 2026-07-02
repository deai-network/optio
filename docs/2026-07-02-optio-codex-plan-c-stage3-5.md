# optio-codex Plan C — Stages 3–5 (Seeds, Leases + Cred Watcher + Verify, Binary Cache) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full Stage 3+4+5 parity for optio-codex per `docs/2026-07-02-optio-codex-design.md` and `docs/writing-agent-wrappers.md`, ported from optio-grok (the primary template): the seed lifecycle (capture / consume / CRUD) with codex's workdir pre-trust, the lease + credential-watcher + teardown save-back discipline, the engine-free `verify_and_refresh_seed`, and the optio-owned binary cache with a **real** GitHub-release auto-download (grok's documented gap — codex has a clean URL). Completes the demo trio's first two legs (seed-setup + seed-pinned iframe).

**Architecture:** Three new modules in `packages/optio-codex/src/optio_codex/` (`seed_manifest.py`, `cred_watcher.py`, `verify.py`), surgical extensions to `host_actions.py` (pre-trust TOML edit, headless probe, cache-backed install with download), `types.py` (seed config fields), `session.py` (seed/lease/watcher wiring + teardown ordering), `__init__.py` (exports); `tests/fake_codex.py` grows seed scenarios, a probe mode, and a durable argv record; `packages/optio-demo/src/optio_demo/tasks/codex.py` is upgraded from Plan A's plain iframe demo to the seed-setup + seed-pinned pattern. The generic seed engine (`optio_agents.seeds`) is consumed as-is — **nothing** is copied from it (SSOT).

**Why Stage 4 is MANDATORY for codex (design-doc pin):** ChatGPT-mode `auth.json` carries a **single-use rotating refresh token** (`tokens.refresh_token`; openai/codex#15410 — by design, a used refresh token invalidates all other copies) plus an 8-day proactive refresh (`TOKEN_REFRESH_INTERVAL`, manager.rs) and refresh-on-401. Without in-session save-back + a final backstop, the seed's stored token is dead after the first session that refreshes. One live lineage per seed; the lease layer is what prevents two sessions from rotating the same token concurrently.

**Tech Stack:** Python ≥3.11, pytest + pytest-asyncio (asyncio_mode=auto), optio-core/host/agents driver stack, `optio_agents.seeds` engine, tmux + ttyd, MongoDB via the existing test fixtures (Docker mongod on localhost:27017).

## Global Constraints

- Worktree: `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex`. All paths below are relative to this worktree root unless absolute.
- Python env: use the worktree venv **only**: `.venv/bin/python` / `.venv/bin/pip` (Plan A Task 1's venv). NEVER `pip install` against the global interpreter. If an import fails at baseline, install editable: `.venv/bin/pip install -e packages/optio-codex`.
- Test command shape (from worktree root): `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (requires local MongoDB on `localhost:27017`; if Mongo is down: `cd packages/optio-demo && make deps-up`).
- Commit style: conventional commits (`feat(optio-codex): …`), one commit per task step marked "Commit". **NO `Co-Authored-By` lines** (user rule).
- SSOT rule (user's never-duplicate rule): the seed/lease engine lives in `optio_agents.seeds` and is imported, never copied. `seed_manifest.py` / `cred_watcher.py` / `verify.py` are codex *adopters* of that engine, mirroring `optio_grok.*` structurally — the grok module docstrings' probe-pinned facts are re-derived for codex, not copy-pasted.
- **Sequencing precondition:** this plan builds on Plan A's end-state (`ensure_codex_installed` returns the per-task `<workdir>/home/.local/bin/codex` symlink; `_provision_task_home`; 5-tuple `launch_ttyd_with_codex`) **and Plan B's end-state (Stages 1–2, `docs/2026-07-02-optio-codex-plan-b-stage1-2.md`)**: the `ssh` guard removed, `supports_resume` / `workdir_exclude` config fields (Plan B Task 5), and a `resuming` flag + snapshot capture in `session.py`. Field ownership is disjoint — Plan B owns `supports_resume`/`workdir_exclude`, this plan owns `seed_id`/`on_seed_saved`; no collision. **Task 0 verifies the actual baseline and adapts:** where this plan's session diffs reference `resuming` / snapshot code, and Plan B has not landed yet, treat `resuming` as a new local initialized to `False` (no snapshot lookup) and leave a `# Plan B integration point` comment — the semantics ("seed merge and seed capture happen on FRESH launches only") are identical either way. Do NOT add `supports_resume`/`workdir_exclude` fields here if absent; they are Plan B's.
- Reference implementation is `packages/optio-grok` (branch `csillag/optio-grok`, readable at the MAIN checkout `/home/csillag/deai/optio/packages/optio-grok`). `optio-claudecode`/`optio-opencode` are secondary references. When this plan deliberately diverges from grok, the task says so; do not "fix back".
- Every task must leave the whole codex suite green before its commit.

**Codex-specific facts this plan encodes (from the design doc's live probes, codex-cli 0.142.5):**
- `CODEX_HOME = <workdir>/home/.codex` (already in `_isolation_env`). Credentials: `home/.codex/auth.json`; config: `home/.codex/config.toml`.
- `auth.json` shapes: ChatGPT mode `{"auth_mode": …, "tokens": {"id_token", "access_token", "refresh_token"}, "last_refresh": …}`; API-key mode `{"OPENAI_API_KEY": "sk-…"}` (written by `codex login --with-api-key`). Validity gate = `tokens` non-null OR `OPENAI_API_KEY` non-null.
- Seed consume must **pre-trust the workdir**: `[projects."<workdir>"] trust_level = "trusted"` in `config.toml` — a cwd-dependent, *post-merge host-side edit* in `_prepare` (design decision: NOT a manifest `consume_transform`; codex rewrites `config.toml` itself, so the edit is minimal append-if-absent).
- Headless probe surface: `codex exec --json -s read-only --skip-git-repo-check '<prompt>'` (stdin closed; no approvals in exec mode).
- Binary: single static musl binary from `https://github.com/openai/codex/releases/download/rust-v<ver>/codex-<triple>.tar.gz`, triples `{x86_64,aarch64}-unknown-linux-musl`.

---

### Task 0: Baseline — environment sanity, green suite, Plan A/B state check

**Files:** none (verification only).

- [ ] **Step 1: Verify the venv + editable install**

Run: `.venv/bin/python -c "import optio_codex, optio_agents; print(optio_codex.__file__)"`
Expected: a path inside this worktree's `packages/optio-codex/src/`. Otherwise `.venv/bin/pip install -e packages/optio-codex` and re-check.

- [ ] **Step 2: Green baseline**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (Plan A's expanded suite, ~24 tests; more if Plan B landed). Do not proceed on red. Known repo flakes (`optio-core` cancel test) are outside this suite.

- [ ] **Step 3: Record the integration baseline**

Check and note (they steer later tasks; do not change anything):
- `grep -n "resuming" packages/optio-codex/src/optio_codex/session.py` — Plan B landed iff this exists.
- `grep -n "supports_resume\|workdir_exclude" packages/optio-codex/src/optio_codex/types.py` — same signal.
- `grep -n "return handle" packages/optio-codex/src/optio_codex/host_actions.py` — Plan A Task 5's 5-tuple return.
- `docs/2026-07-02-optio-codex-plan-b-stage1-2.md` exists (checked at plan-finalization time) and its `types.py` task (Plan B Task 5) adds only `supports_resume` + `workdir_exclude` — disjoint from this plan's `seed_id` / `on_seed_saved`. Confirm that is still true; any new overlap is a stop-and-ask.

*(No commit — nothing changed.)*

---

## Stage 3 — Seeds

### Task 1: `seed_manifest.py` — manifests, suffix, store-binding CRUD wrappers

The codex adopter of the generic seed engine. Two manifests: the full seed (auth + config) and the credential-only write-back manifest (`refresh_seed` targets it so save-back never touches the seed's `config.toml`). `consume_transform=None` — codex auth/config are cwd-independent; the one cwd-dependent piece (workdir trust) is deliberately NOT a manifest transform (Task 2).

**Files:**
- Create: `packages/optio-codex/src/optio_codex/seed_manifest.py`
- Modify: `packages/optio-codex/src/optio_codex/__init__.py`
- Test: `packages/optio-codex/tests/test_seed_manifest.py`

**Interfaces:**
- Consumes: `optio_agents.seeds.SeedManifest`, `seeds.delete_seed`/`list_seeds`/`purge_seed`.
- Produces: `CODEX_SEED_SUFFIX = "_codex_seeds"`, `CODEX_SEED_MANIFEST`, `CODEX_CRED_MANIFEST`, and store-binding wrappers `delete_seed(store, seed_id)` / `list_seeds(store)` / `purge_seed(store, seed_id)` (consumers hand over `optio.mongo_store`; Task 10's demo uses `list_seeds`).

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_seed_manifest.py`:

```python
"""Unit tests for the codex seed manifest (Stage 3, Task 1).

Adapted from optio-grok's seed-manifest shape (codex, like grok/opencode,
needs no cwd-rekey → consume_transform is None; the cwd-dependent workdir
pre-trust is a deliberate post-merge edit in _prepare, NOT a transform).
CODEX_HOME is <workdir>/home/.codex; the engine roots capture/extract at
host.workdir + "/" + home_subdir, so the manifest uses home_subdir="home"
with ".codex/" prefixes on the include paths.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_codex.seed_manifest import (
    CODEX_CRED_MANIFEST,
    CODEX_SEED_MANIFEST,
    CODEX_SEED_SUFFIX,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(CODEX_SEED_MANIFEST, seeds.SeedManifest)
    assert CODEX_SEED_MANIFEST.home_subdir == "home"
    assert ".codex/auth.json" in CODEX_SEED_MANIFEST.include
    assert ".codex/config.toml" in CODEX_SEED_MANIFEST.include


def test_seed_manifest_never_carries_junk():
    """The include list is an allowlist — the 286MB packages/ cache, sqlite
    session index, sessions/, logs etc. must never be members (the design
    doc's exclude-always list is enforced by NOT including them)."""
    assert set(CODEX_SEED_MANIFEST.include) == {
        ".codex/auth.json", ".codex/config.toml",
    }


def test_no_consume_transform():
    # codex auth/config are cwd-independent → no rekey. The workdir trust
    # entry is cwd-dependent but handled as a post-merge edit in _prepare.
    assert CODEX_SEED_MANIFEST.consume_transform is None
    assert CODEX_CRED_MANIFEST.consume_transform is None


def test_cred_manifest_is_auth_only():
    assert CODEX_CRED_MANIFEST.home_subdir == "home"
    assert CODEX_CRED_MANIFEST.include == [".codex/auth.json"]


def test_seed_suffix():
    assert CODEX_SEED_SUFFIX == "_codex_seeds"


def test_crud_wrappers_exported():
    from optio_codex import delete_seed, list_seeds, purge_seed  # noqa: F401
    from optio_codex.seed_manifest import (  # noqa: F401
        delete_seed as _d, list_seeds as _l, purge_seed as _p,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_seed_manifest.py -q`
Expected: all FAIL with `ModuleNotFoundError: No module named 'optio_codex.seed_manifest'`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/seed_manifest.py`**

```python
"""codex adopter of the generic optio-agents seed engine.

Defines the codex seed manifest (HOME layout + capture-time include triage),
the Mongo collection suffix, and ergonomic ``delete_seed`` / ``list_seeds`` /
``purge_seed`` wrappers that bind the suffix for consuming apps.

A codex *seed* carries the logged-in identity that lives under ``CODEX_HOME``
(``<workdir>/home/.codex``): ``auth.json`` (ChatGPT mode: ``auth_mode`` +
``tokens{id_token, access_token, refresh_token}`` + ``last_refresh``; API-key
mode: ``OPENAI_API_KEY``) plus ``config.toml``. Replanting it into a fresh
workdir is the answer to headless login.

The include list is an allowlist, which is also the exclusion mechanism:
``packages/`` (the ~286MB binary cache), ``*.sqlite*`` (absolute
rollout-path poison; rebuilt from rollouts), ``sessions/``, ``cache/``,
``tmp/``, logs etc. are simply never members.

Like grok/opencode (and unlike claudecode), codex needs no consume-time
rekey: auth/config are cwd-independent, so ``consume_transform`` is None.
The one cwd-dependent consume step — pre-trusting the new workdir via a
``[projects."<workdir>"]`` entry in config.toml — is deliberately a
post-merge edit in the session's ``_prepare`` (see
``host_actions.ensure_workdir_trusted``), NOT a manifest transform: codex
rewrites config.toml itself at runtime, so optio's edit must stay a
minimal, idempotent append against the *planted* file, applied exactly at
the point the workdir is known.

Path note: the engine roots capture/extract at ``host.workdir + "/" +
home_subdir`` (see ``SeedManifest.home_subdir``). CODEX_HOME is
``<workdir>/home/.codex``, so the manifest uses ``home_subdir="home"`` with
``.codex/`` prefixes on the include paths (mirroring grok's ``.grok/…``).
"""

from __future__ import annotations

from optio_agents import seeds

CODEX_SEED_SUFFIX = "_codex_seeds"
CODEX_SEED_MANIFEST_VERSION = 1


# Credential-only manifest for in-session save-back (the write-back analog
# of the full CODEX_SEED_MANIFEST; mirrors grok's GROK_CRED_MANIFEST and
# opencode's OPENCODE_CRED_MANIFEST). Only auth.json is re-captured — the
# seed's config.toml is never touched by save-back.
CODEX_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".codex/auth.json"],
    version=CODEX_SEED_MANIFEST_VERSION,
    consume_transform=None,
)


CODEX_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CODEX_CRED_MANIFEST.include + [
        ".codex/config.toml",
    ],
    version=CODEX_SEED_MANIFEST_VERSION,
    consume_transform=None,  # no cwd-rekey for codex (pre-trust is in _prepare)
)


async def delete_seed(store, seed_id: str):
    """Delete a codex seed doc; returns its GridFS blobId (or None).

    Takes an optio store binding (``optio.mongo_store`` — exposes ``db`` and
    ``prefix``) as-is, so consuming apps hand over the whole namespace handle
    instead of threading db+prefix (or knowing the collection suffix). The
    caller still removes the returned blob from GridFS.
    """
    return await seeds.delete_seed(
        store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )


async def list_seeds(store) -> list[dict]:
    """List codex seeds as [{seedId, createdAt}, ...]. Takes an optio store
    binding (``optio.mongo_store``) as-is."""
    return await seeds.list_seeds(store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX)


async def purge_seed(store, seed_id: str) -> None:
    """Fully expunge a codex seed (doc + its GridFS blob); raises KeyError if
    absent. Takes an optio store binding (``optio.mongo_store``) as-is.

    Mirrors ``optio_grok.purge_seed`` / ``optio_claudecode.purge_seed``; a
    thin re-export of the ``optio_agents.seeds.purge_seed`` engine."""
    return await seeds.purge_seed(
        store.db, prefix=store.prefix, suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
```

In `packages/optio-codex/src/optio_codex/__init__.py`, add after the optio-host import block:

```python
from optio_codex.seed_manifest import (
    CODEX_CRED_MANIFEST,
    CODEX_SEED_MANIFEST,
    CODEX_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
```

and extend `__all__` with: `"CODEX_SEED_MANIFEST", "CODEX_CRED_MANIFEST", "CODEX_SEED_SUFFIX", "delete_seed", "list_seeds", "purge_seed"`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/seed_manifest.py packages/optio-codex/src/optio_codex/__init__.py packages/optio-codex/tests/test_seed_manifest.py
git commit -m "feat(optio-codex): seed manifests + store-binding CRUD wrappers (Stage 3)

CODEX_SEED_MANIFEST (auth.json + config.toml under home/.codex) and the
credential-only CODEX_CRED_MANIFEST for save-back, suffix _codex_seeds.
Allowlist-only include keeps the 286MB packages/ cache and the sqlite
session index out of seeds by construction."
```

---

### Task 2: `ensure_workdir_trusted` — idempotent post-merge pre-trust edit

Codex refuses to operate (or prompts) in an untrusted directory; a seeded fresh workdir is never in the operator's trust list. Design decision: ensure `[projects."<workdir>"] trust_level = "trusted"` in `home/.codex/config.toml` as a **host-side, idempotent, append-if-absent edit** applied in `_prepare` right after `merge_seed` — minimal because codex rewrites config.toml itself (a heavier structured rewrite would fight it), and NOT a manifest transform (the manifest stays cwd-independent; the edit needs `host.workdir`).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py`
- Test: `packages/optio-codex/tests/test_workdir_trust.py`

**Interfaces:**
- Consumes: `Host.fetch_bytes_from_host`, `Host.write_text`, `Host.run_command` (mkdir).
- Produces: `async ensure_workdir_trusted(host) -> None` (session `_prepare` calls it in Task 4; it must be safe on a workdir with NO config.toml — a seed may legitimately lack one).

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_workdir_trust.py`:

```python
"""ensure_workdir_trusted: idempotent [projects."<workdir>"] trust edit.

The edit is deliberately minimal (append-if-absent, never a structured TOML
rewrite): codex rewrites config.toml itself at runtime, so optio only
guarantees the trust entry exists at launch and otherwise keeps its hands
off the file.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from optio_host.host import LocalHost

from optio_codex.host_actions import ensure_workdir_trusted


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


def _config_path(host) -> str:
    return os.path.join(host.workdir, "home", ".codex", "config.toml")


def _read(host) -> str:
    with open(_config_path(host), encoding="utf-8") as fh:
        return fh.read()


async def test_creates_config_with_trust_entry_when_absent(host):
    # No home/.codex at all (a seed may lack config.toml entirely).
    await ensure_workdir_trusted(host)
    text = _read(host)
    assert f'[projects."{host.workdir}"]' in text
    assert 'trust_level = "trusted"' in text


async def test_appends_to_existing_config_preserving_content(host):
    d = os.path.join(host.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as fh:
        fh.write('model = "gpt-5.5"\n')
    await ensure_workdir_trusted(host)
    text = _read(host)
    assert text.startswith('model = "gpt-5.5"\n')          # untouched prefix
    assert f'[projects."{host.workdir}"]' in text
    assert 'trust_level = "trusted"' in text


async def test_idempotent_second_call_is_byte_identical(host):
    await ensure_workdir_trusted(host)
    first = _read(host)
    await ensure_workdir_trusted(host)
    assert _read(host) == first


async def test_existing_trust_entry_not_duplicated(host):
    d = os.path.join(host.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    entry = f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n'
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as fh:
        fh.write(entry)
    await ensure_workdir_trusted(host)
    assert _read(host).count(f'[projects."{host.workdir}"]') == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_workdir_trust.py -q`
Expected: all FAIL with `ImportError: cannot import name 'ensure_workdir_trusted'`.

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add after `_provision_task_home`:

```python
async def ensure_workdir_trusted(host: "Host") -> None:
    """Ensure ``home/.codex/config.toml`` pre-trusts this task's workdir.

    Codex gates operation on per-directory trust recorded as
    ``[projects."<dir>"] trust_level = "trusted"`` in config.toml. A seeded
    fresh workdir was never trusted by the operator, so the session's
    ``_prepare`` calls this right after ``merge_seed`` (the design doc's
    "post-merge edit" decision — the entry is cwd-dependent, so it cannot
    live in the cwd-independent seed blob or a manifest transform).

    Deliberately minimal and idempotent: append the entry only when the
    exact ``[projects."<workdir>"]`` header is absent; never rewrite or
    reorder the rest of the file (codex itself rewrites config.toml at
    runtime — optio must not fight it). Also safe when the seed carried no
    config.toml at all (the file is created).
    """
    workdir = host.workdir.rstrip("/")
    config_rel = "home/.codex/config.toml"
    config_abs = f"{workdir}/{config_rel}"
    header = f'[projects."{workdir}"]'
    try:
        current = (await host.fetch_bytes_from_host(config_abs)).decode("utf-8")
    except FileNotFoundError:
        current = ""
    if header in current:
        return
    entry = f'{header}\ntrust_level = "trusted"\n'
    if current and not current.endswith("\n"):
        current += "\n"
    # write_text is workdir-relative and creates parent dirs as needed via
    # the host layer; keep the whole-file write (small file, atomic enough).
    await host.run_command(
        f"mkdir -p {shlex.quote(workdir + '/home/.codex')}"
    )
    await host.write_text(config_rel, current + entry)
```

**NOTE (verify against reality):** confirm `host.write_text` creates parent directories; if it does, drop the explicit `mkdir -p` line and the `run_command` import note. Check `packages/optio-host/src/optio_host/host.py` — grok's `_prepare` writes `home/.grok/sandbox.toml` via bare `write_text`, which is the precedent; mirror whatever it relies on.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_workdir_trust.py
git commit -m "feat(optio-codex): idempotent workdir pre-trust edit for seeded launches

ensure_workdir_trusted appends [projects.\"<workdir>\"] trust_level=trusted
to home/.codex/config.toml iff absent — a post-merge _prepare edit, not a
manifest transform (cwd-dependent; codex rewrites config.toml itself so
the edit stays minimal)."
```

---

## Stage 4 (part 1) — Credential watcher

*(The watcher module lands before the session seed wiring because seed capture (Task 4) gates on `capture_gate_ok`, which lives here. Building it now keeps Task 4 a pure wiring task.)*

### Task 3: `cred_watcher.py` — fingerprint, capture gate, save-back, lease renewal

Direct port of `optio_grok.cred_watcher` (grok → codex renames; credential path `home/.codex/auth.json`), with one codex-specific tightening: the validity gate requires `tokens` non-null OR `OPENAI_API_KEY` non-null (grok accepted any non-empty JSON object; codex's two documented auth shapes make a stricter gate checkable, and it protects seeds from a logged-out `{}` or a half-written file).

**Files:**
- Create: `packages/optio-codex/src/optio_codex/cred_watcher.py`
- Test: `packages/optio-codex/tests/test_cred_watcher.py`

**Interfaces:**
- Consumes: `optio_agents.seeds.refresh_seed` / `renew_lease`, `CODEX_CRED_MANIFEST`, `CODEX_SEED_SUFFIX`, `Host.fetch_bytes_from_host`, `ctx.cancellation_flag`, `ctx._db` / `ctx._prefix`.
- Produces: `CRED_WATCH_INTERVAL_S = 10.0`; `async cred_fingerprint(host) -> str | None`; `async capture_gate_ok(host) -> bool`; `async save_back_if_changed(ctx, host, *, seed_id, baseline, encrypt, decrypt) -> str | None`; `async run_credential_watcher(ctx, host, *, seed_id, baseline, encrypt, decrypt, lease_holder=None) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_cred_watcher.py`:

```python
"""Unit tests for the codex credential watcher (LocalHost + real Mongo).

Mirrors optio-grok's test_cred_watcher (grok → codex renames; codex's
single-use rotating refresh_token — openai/codex#15410 — is the exact
failure mode the watcher exists for). The credential lives at
``<workdir>/home/.codex/auth.json``; validity requires ``tokens`` non-null
OR ``OPENAI_API_KEY`` non-null (the two documented codex auth shapes).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_codex import cred_watcher
from optio_codex.seed_manifest import CODEX_CRED_MANIFEST, CODEX_SEED_SUFFIX


def _chatgpt_auth(refresh: str = "T1") -> dict:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": "fake-id", "access_token": "fake-access",
            "refresh_token": refresh,
        },
        "last_refresh": "2026-07-02T00:00:00Z",
    }


def _write_auth(workdir: str, payload: dict | str) -> None:
    d = os.path.join(workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(text)


@pytest_asyncio.fixture
async def host(tmp_path):
    h = LocalHost(taskdir=str(tmp_path / "t"))
    await h.setup_workdir()
    return h


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


# --- save-back gate (cred_fingerprint) ---------------------------------

async def test_fingerprint_none_when_missing(host):
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_unparseable(host):
    _write_auth(host.workdir, "not json")
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_none_when_logged_out(host):
    # Empty object and null-tokens shapes are both invalid — neither auth
    # mode is present, so there is nothing worth saving back.
    _write_auth(host.workdir, {})
    assert await cred_watcher.cred_fingerprint(host) is None
    _write_auth(host.workdir, {"auth_mode": "chatgpt", "tokens": None,
                               "OPENAI_API_KEY": None})
    assert await cred_watcher.cred_fingerprint(host) is None


async def test_fingerprint_valid_for_api_key_shape(host):
    _write_auth(host.workdir, {"OPENAI_API_KEY": "sk-fake"})
    assert await cred_watcher.cred_fingerprint(host) is not None


async def test_fingerprint_changes_with_content(host):
    _write_auth(host.workdir, _chatgpt_auth("T1"))
    fp1 = await cred_watcher.cred_fingerprint(host)
    assert fp1 is not None
    _write_auth(host.workdir, _chatgpt_auth("T2"))
    fp2 = await cred_watcher.cred_fingerprint(host)
    assert fp2 is not None and fp2 != fp1


# --- capture gate -------------------------------------------------------

async def test_capture_gate_requires_valid_auth(host):
    assert not await cred_watcher.capture_gate_ok(host)          # no auth
    _write_auth(host.workdir, "not json")
    assert not await cred_watcher.capture_gate_ok(host)          # unparseable
    _write_auth(host.workdir, {})
    assert not await cred_watcher.capture_gate_ok(host)          # logged-out
    _write_auth(host.workdir, _chatgpt_auth())
    assert await cred_watcher.capture_gate_ok(host)              # chatgpt mode
    _write_auth(host.workdir, {"OPENAI_API_KEY": "sk-fake"})
    assert await cred_watcher.capture_gate_ok(host)              # api-key mode


# --- save_back_if_changed ------------------------------------------------

async def test_save_back_only_on_change(mongo_db, host, tmp_path):
    ctx = await _ctx(mongo_db)
    _write_auth(host.workdir, _chatgpt_auth("T1"))
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CODEX_CRED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    # Unchanged: returns baseline, no write.
    fp = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp == baseline

    # Changed: writes, returns a new fingerprint.
    _write_auth(host.workdir, _chatgpt_auth("T2"))
    fp2 = await cred_watcher.save_back_if_changed(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    )
    assert fp2 is not None and fp2 != baseline

    # The seed now carries the rotated token.
    dst = LocalHost(taskdir=str(tmp_path / "chk"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=CODEX_CRED_MANIFEST,
        suffix=CODEX_SEED_SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".codex", "auth.json")) as fh:
        assert "T2" in fh.read()


# --- watcher integration (real Mongo) ------------------------------------

async def test_watcher_saves_back_on_change(mongo_db, host, tmp_path, monkeypatch):
    monkeypatch.setattr(cred_watcher, "CRED_WATCH_INTERVAL_S", 0.05)
    _write_auth(host.workdir, _chatgpt_auth("T1"))
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CODEX_CRED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline, encrypt=None, decrypt=None,
    ))
    _write_auth(host.workdir, _chatgpt_auth("T2"))
    try:
        for i in range(40):
            await asyncio.sleep(0.05)
            dst = LocalHost(taskdir=str(tmp_path / f"chk{i}"))
            await dst.setup_workdir()
            await seeds.merge_seed(
                ctx, dst, seed_id=seed_id, manifest=CODEX_CRED_MANIFEST,
                suffix=CODEX_SEED_SUFFIX, decrypt=None,
            )
            p = os.path.join(dst.workdir, "home", ".codex", "auth.json")
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
    _write_auth(host.workdir, _chatgpt_auth("T1"))
    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, host, manifest=CODEX_CRED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )
    got = await seeds.acquire(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        poolKey="pool1", holder="p",
    )
    assert got == seed_id
    baseline = await cred_watcher.cred_fingerprint(host)

    task = asyncio.create_task(cred_watcher.run_credential_watcher(
        ctx, host, seed_id=seed_id, baseline=baseline,
        encrypt=None, decrypt=None, lease_holder="p",
    ))
    # Steal the lease: release as p, re-acquire as another holder.
    await seeds.release(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        seed_id=seed_id, holder="p",
    )
    stolen = await seeds.acquire(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        poolKey="pool1", holder="thief",
    )
    assert stolen == seed_id

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

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_cred_watcher.py -q`
Expected: all FAIL with `ModuleNotFoundError: No module named 'optio_codex.cred_watcher'`.

- [ ] **Step 3: Implement `packages/optio-codex/src/optio_codex/cred_watcher.py`**

```python
"""In-session credential save-back for codex seeds.

Codex's ChatGPT-mode ``auth.json`` holds a **single-use rotating refresh
token** (``tokens.refresh_token``): the manager proactively refreshes after
8 days (``TOKEN_REFRESH_INTERVAL``, manager.rs) and on any 401, rewriting
auth.json in place — and a used refresh token invalidates every other copy
(openai/codex#15410, by design). That is the exact failure mode
optio-opencode's watcher was built for and optio-grok ported; this module
is the codex adaptation (credential path ``<workdir>/home/.codex/auth.json``).
OpenAI's own CI/CD guidance is the same restore → run → persist pattern.

The watcher keeps the seed current by writing the changed in-session
auth.json back into the existing seed, plus a final backstop at teardown.
It also renews the seed's pool lease each tick and aborts the session on
lease loss (a new holder must never rotate the same token concurrently).
The seed is the single source of truth for credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_host.host import Host

from optio_agents import seeds
from optio_codex.seed_manifest import CODEX_CRED_MANIFEST, CODEX_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.codex/auth.json"


def _auth_valid(data: object) -> bool:
    """True iff ``data`` is one of codex's two live auth shapes: ChatGPT
    mode (``tokens`` non-null) or API-key mode (``OPENAI_API_KEY``
    non-null). A logged-out ``{}``/null-tokens file is invalid — saving it
    back would clobber a good seed."""
    if not isinstance(data, dict) or not data:
        return False
    return data.get("tokens") is not None or data.get("OPENAI_API_KEY") is not None


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live ``home/.codex/auth.json``, or None when it is
    missing, unparseable, or logged-out (nothing worth saving back).

    Guards against corrupting a seed with a half-written / logged-out file —
    the codex analog of opencode's provider-entry gate, tightened to codex's
    two documented auth shapes (tokens / OPENAI_API_KEY).
    """
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not _auth_valid(data):
        return None
    return hashlib.sha256(raw).hexdigest()


async def capture_gate_ok(host: Host) -> bool:
    """Gate for seed CAPTURE: a valid ``auth.json`` is present.

    Codex, like grok, has no separate model requirement (the model lives in
    ``config.toml`` and is optional), so a valid credential is the whole
    gate. Save-back uses ``cred_fingerprint`` directly; this is the terminal
    capture gate."""
    return await cred_fingerprint(host) is not None


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live auth.json differs from ``baseline`` and is valid, save it
    back into the seed and return the new fingerprint. Otherwise return
    ``baseline`` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=CODEX_CRED_MANIFEST,
            suffix=CODEX_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
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
    """Poll every ``CRED_WATCH_INTERVAL_S``: save back the rotated auth.json,
    and (when ``lease_holder`` is set) renew the seed's lease. If the lease
    is lost, signal the session to stop (set the cancellation flag) and exit
    — continuing would mean a token-rotation collision with the new holder.

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
                ctx._db, prefix=ctx._prefix, suffix=CODEX_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/cred_watcher.py packages/optio-codex/tests/test_cred_watcher.py
git commit -m "feat(optio-codex): credential watcher — save-back + lease renewal (Stage 4)

Codex's ChatGPT-mode auth.json rotates a SINGLE-USE refresh token
(openai/codex#15410; 8-day proactive refresh), making save-back
mandatory. Validity gate: tokens non-null OR OPENAI_API_KEY non-null.
10s tick = cred save-back via CODEX_CRED_MANIFEST + renew_lease; lease
loss sets the cancellation flag."
```

---

## Stage 3 (continued) — Session seed wiring

### Task 4: Config fields + seed consume/capture in `session.py`

Adds `seed_id` (str | SeedProvider) and `on_seed_saved` to `CodexTaskConfig`; wires `_prepare` to merge the seed (fresh launches only) and pre-trust the workdir, and teardown to capture a seed gated on reached-live + `capture_gate_ok`. The fake agent gains the `seed` scenario and the durable `FAKE_CODEX_RECORD` argv/config log (the record lives OUTSIDE the workdir because teardown wipes it — it is how tests prove launch-time state).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py`
- Modify: `packages/optio-codex/src/optio_codex/session.py`
- Modify: `packages/optio-codex/src/optio_codex/__init__.py`
- Modify: `packages/optio-codex/tests/fake_codex.py`
- Test: `packages/optio-codex/tests/test_session_seed.py`

**Interfaces:**
- Consumes: `optio_agents.seeds.merge_seed` / `capture_seed`, `cred_watcher.capture_gate_ok` / `cred_fingerprint`, `host_actions.ensure_workdir_trusted`, `CODEX_SEED_MANIFEST` / `CODEX_SEED_SUFFIX`.
- Produces: `CodexTaskConfig.seed_id: str | SeedProvider | None = None`, `CodexTaskConfig.on_seed_saved: Callable[[str, str | None], Awaitable[None] | None] | None = None`; `SeedProvider` / `SeedUnavailableError` types; session locals `resolved_seed_id` / `lease_holder` / `cred_baseline` (the lease/watcher consumers arrive in Task 5); fake scenarios `seed` + `FAKE_CODEX_RECORD`.

- [ ] **Step 1: Extend the fake agent**

In `packages/optio-codex/tests/fake_codex.py`:

1. Add to the imports: `import json`, `import sys`.
2. Change `SCENARIOS` to include `"seed"` (Task 5 adds `"seed_rotate"`); if Plan A's Task 9 already extended it, append to the existing tuple:

```python
SCENARIOS = (
    "happy", "deliverable", "error", "exit_zero", "exit_nonzero", "long",
    "seed",
)
```

(If Plan A's scenarios are absent, do NOT add them here — extend whatever tuple exists with `"seed"` only.)

3. Add after `_log`:

```python
def _codex_home() -> Path:
    """The per-task CODEX_HOME (``<workdir>/home/.codex``) set by the launcher.

    Lives INSIDE the workdir, so anything written here is captured by seed
    capture (and later by workdir snapshots) — exactly like real codex's
    auth.json/config.toml.
    """
    ch = os.environ.get("CODEX_HOME") or str(Path.cwd() / "home" / ".codex")
    return Path(ch)


def _record_launch() -> None:
    """Durably record this launch's argv + the config.toml planted in
    CODEX_HOME at launch time.

    When ``FAKE_CODEX_RECORD`` names a path, append one JSON object per
    launch: ``{"argv": [...], "config_toml": <content|null>}``. The workdir
    is wiped on teardown, so this record (outside the workdir) is how tests
    assert launch-time facts — e.g. that the seeded config.toml carried the
    workdir pre-trust entry BEFORE codex started (Stage 3), and later which
    sandbox flags were passed (Stage 8). The fake ACCEPTS and otherwise
    IGNORES all flags — it enforces nothing.
    """
    dest = os.environ.get("FAKE_CODEX_RECORD")
    if not dest:
        return
    config_path = _codex_home() / "config.toml"
    try:
        config_toml = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        config_toml = None
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": sys.argv[1:],
            "config_toml": config_toml,
        }) + "\n")
        fh.flush()


def _scenario_seed() -> None:
    """Model codex's logged-in identity for the Stage-3 seed tests.

    Two roles, distinguished by whether ``auth.json`` is already present at
    launch:

    * CONSUME (seed already merged in): the seed engine planted
      ``home/.codex/auth.json`` before launch. Record that fact via a
      deliverable so the test can assert the seed reached the workdir
      before codex started.
    * CAPTURE (fresh login): no auth yet, so write a fake logged-in
      ChatGPT-mode identity (auth.json + config.toml) under CODEX_HOME.
      Teardown capture then stores it as a reusable seed.
    """
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    auth = ch / "auth.json"
    if auth.exists():
        workdir = Path.cwd()
        (workdir / "deliverables").mkdir(exist_ok=True)
        (workdir / "deliverables" / "seed_present.txt").write_text(
            "SEED_PRESENT\n", encoding="utf-8",
        )
        time.sleep(0.05)
        _log("DELIVERABLE: ./deliverables/seed_present.txt")
    else:
        auth.write_text(
            json.dumps({
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": "fake-id",
                    "access_token": "fake-access",
                    "refresh_token": "fake-refresh",
                },
                "last_refresh": "2026-07-02T00:00:00Z",
            }),
            encoding="utf-8",
        )
        (ch / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
    time.sleep(0.05)
    _log("STATUS: 10% seed scenario alive")
    time.sleep(0.05)
    _log("DONE: seed scenario completed")
    time.sleep(30.0)
```

4. In `main()`: call `_record_launch()` right before the scenario dispatch, and register `"seed": _scenario_seed` in the dispatch dict.

- [ ] **Step 2: Write the failing tests**

Create `packages/optio-codex/tests/test_session_seed.py`:

```python
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
        codex_install_dir=str(shim_install_dir),
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
    assert await coll.find_one({"_id": ObjectId(seed_id)}) is not None


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_seed.py -q`
Expected: FAIL — `CodexTaskConfig` has no `seed_id`/`on_seed_saved` field (`TypeError: unexpected keyword argument`).

- [ ] **Step 4: Implement — `types.py`**

In `packages/optio-codex/src/optio_codex/types.py`:

1. Extend the `typing` import: `from typing import Awaitable, Callable, Literal`.
2. Add after the Literal vocabulary block (before `CodexTaskConfig`):

```python
# A seed provider resolves a usable seed_id at launch time (e.g. leasing one
# from a pool). Mirrors optio-grok's SeedProvider; the callable/lease path
# is exercised by the Stage-4 wiring — a static string seed_id carries no
# lease.
SeedProvider = Callable[[str], Awaitable[str]]


class SeedUnavailableError(Exception):
    """Raised by a seed provider when no usable seed is available; the
    message is surfaced as the process failure."""
```

3. Add the two fields to `CodexTaskConfig` (after the hook-callback fields):

```python
    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (codex
    # auth.json + config.toml) into a fresh workdir before launch, beginning
    # a NEW session already logged-in; the workdir is pre-trusted right
    # after the merge. A plain string is used as-is; a SeedProvider callable
    # is awaited at launch to resolve one (lease path — holder is the
    # process_id). Ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a
    # fresh session after a successful capture, with two args:
    # (seed_id, info). ``info`` is a human-readable account summary (None
    # for now; resolved in a later stage). Its presence is what enables
    # seed capture. Ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None
```

4. Extend `__all__` with `"SeedProvider", "SeedUnavailableError"`.

In `packages/optio-codex/src/optio_codex/__init__.py`: import `SeedProvider, SeedUnavailableError` from `optio_codex.types` and add both to `__all__`.

- [ ] **Step 5: Implement — `session.py`**

In `packages/optio-codex/src/optio_codex/session.py`:

1. Add imports:

```python
import inspect

from optio_agents import seeds as _seeds

from optio_codex import cred_watcher
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
```

2. Add a module-level helper (after `_build_host`):

```python
async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result
```

3. In `run_codex_session`, add locals next to the existing ones:

```python
    # Resolved seed id for a fresh, seeded launch (Stage 3). Set by _prepare
    # (str seed_id → itself; SeedProvider callable → awaited). Stays None on
    # resume and when no seed_id is configured.
    resolved_seed_id: str | None = None
    # Stage 4 lease + credential save-back. ``lease_holder`` is the task's
    # process_id when the seed came from a lease-holding SeedProvider
    # (renewed by the watcher, released at teardown). ``cred_baseline`` is
    # the post-merge auth.json fingerprint the watcher/backstop diff against.
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None
```

If Plan B has NOT landed yet (Task 0 Step 3), also add `resuming = False` with a `# Plan B integration point: set from the snapshot lookup in _prepare` comment; if it HAS landed, use its existing `resuming`.

4. In `_prepare`, extend the `nonlocal` list with `resolved_seed_id, lease_holder, cred_baseline`, and insert AFTER the install/restore steps and BEFORE the `AGENTS.md` write (mirroring grok `session.py:148-172` — seed merge precedes AGENTS.md so codex launches already-authed and a restore never wipes the plant):

```python
        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise
            # SeedUnavailableError) and overlay the stored codex identity
            # (auth.json + config.toml) into the fresh workdir BEFORE
            # AGENTS.md, so codex launches already-authed. Codex auth/config
            # are cwd-independent, so no rekey is needed — but the new
            # workdir must be pre-trusted (cwd-dependent, hence a post-merge
            # edit here rather than a manifest transform).
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown
                # releases it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CODEX_SEED_MANIFEST,
                suffix=CODEX_SEED_SUFFIX,
                decrypt=None,
            )
            await host_actions.ensure_workdir_trusted(host)
            # Baseline the merged auth.json so the in-session watcher and
            # the teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)
```

5. In the `finally` block, add the seed-capture step **after** `teardown_session_tree` and **before** the snapshot-capture block if Plan B has landed (grok's exact ordering: … → lease release → seed capture → snapshot capture → cleanup), otherwise before `host.cleanup_taskdir`. Task 5 inserts the watcher-cancel / backstop / release steps above it — leave room:

```python
        # Seed capture (fresh only): store this session's codex identity as
        # a reusable seed so a later fresh task can start already-authed.
        # Reached-live gate: launched_handle is assigned strictly after a
        # successful launch — an interrupt before launch leaves it None.
        # Guarded on a VALID auth.json (capture_gate_ok) — never seed a
        # login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/.codex/auth.json absent "
                        "or invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=CODEX_SEED_MANIFEST,
                        suffix=CODEX_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later
                    # stage; None for now.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (the three new seed tests included).

- [ ] **Step 7: Commit**

```bash
git add packages/optio-codex/src/optio_codex/types.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/src/optio_codex/__init__.py packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_session_seed.py
git commit -m "feat(optio-codex): seed consume/capture wiring + workdir pre-trust (Stage 3)

seed_id (str | SeedProvider) merges the stored identity into a fresh
workdir before AGENTS.md and pre-trusts the workdir post-merge;
on_seed_saved captures on teardown behind reached-live + capture_gate_ok
gates. Fake codex gains the seed scenario and a durable
FAKE_CODEX_RECORD launch log (outside the workdir) that proves the
trust entry existed at launch time."
```

---

## Stage 4 (continued) — Lease + watcher wiring, teardown ordering

### Task 5: In-session watcher, backstop save-back, lease release — exact ordering

Wires Task 3's watcher into the session body and completes the teardown with grok's exact ordering discipline (`optio-grok/session.py:504-538`). The ordering is load-bearing three ways and each step carries its rationale comment:

1. **watcher-cancel BEFORE backstop save-back** — the two must never race on the same seed blob;
2. **backstop save-back AFTER the agent terminated** — auth.json is final; this backstop is LOAD-BEARING, not defensive: a rotation in the last poll window is persisted ONLY here (the old refresh token is already consumed server-side);
3. **lease release AFTER save-back** — a new acquirer must never merge the pre-save-back blob.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/session.py`
- Modify: `packages/optio-codex/tests/fake_codex.py`
- Test: `packages/optio-codex/tests/test_session_lease.py`

**Interfaces:**
- Consumes: `cred_watcher.run_credential_watcher` / `save_back_if_changed`, `optio_agents.seeds.release` / `acquire` / `assign_to_pool`, Task 4's `resolved_seed_id` / `lease_holder` / `cred_baseline` locals.
- Produces: watcher task spawned in `_codex_body` when seeded; teardown steps 2–4 above; fake scenario `seed_rotate`.

- [ ] **Step 1: Extend the fake agent**

In `packages/optio-codex/tests/fake_codex.py`, add `"seed_rotate"` to `SCENARIOS`, register `"seed_rotate": _scenario_seed_rotate` in the dispatch dict, and add:

```python
def _rotate_auth(ch: Path, new_refresh: str) -> None:
    """Rotate ``tokens.refresh_token`` in ``<CODEX_HOME>/auth.json``,
    modelling codex's single-use refresh-token rotation (manager.rs rewrites
    auth.json in place on refresh) — what the credential watcher must save
    back."""
    auth = ch / "auth.json"
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        tokens["refresh_token"] = new_refresh
    auth.write_text(json.dumps(data), encoding="utf-8")


def _scenario_seed_rotate() -> None:
    """CONSUME role that rotates the refresh token mid-session.

    The seed engine planted ``home/.codex/auth.json`` before launch; this
    run rotates its refresh_token (as real codex would on a token refresh),
    so the session's teardown save-back must write the rotated auth.json
    back into the seed. Used by the Stage-4 lease/save-back session test."""
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    _rotate_auth(ch, "ROTATED-INSESSION")
    time.sleep(0.05)
    _log("STATUS: 10% rotate scenario alive")
    time.sleep(0.05)
    _log("DONE: rotate scenario completed")
    time.sleep(30.0)
```

- [ ] **Step 2: Write the failing test**

Create `packages/optio-codex/tests/test_session_lease.py`:

```python
"""Pooled-lease + save-back lifecycle test for optio-codex (Stage 4).

A fresh seeded session whose ``seed_id`` is a lease-holding ``SeedProvider``:

* the provider leases a seed from the pool (holder = process_id);
* the fake codex rotates its refresh_token mid-session (``seed_rotate``);
* teardown saves the rotated auth.json back into the seed and releases the
  lease (release AFTER save-back — the deliberate ordering ported from
  grok/opencode).

Asserts the seed's stored auth.json carries the rotated token and the lease
is free again afterwards.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import tarfile

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_codex import CodexTaskConfig
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
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
        process_oid=proc["_id"], process_id=process_id, root_oid=proc["_id"],
        depth=0, params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


def _cfg(shim_install_dir: pathlib.Path, **kw) -> CodexTaskConfig:
    return CodexTaskConfig(
        consumer_instructions="do the thing",
        codex_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        **kw,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    """Extract ``.codex/auth.json`` from the seed blob for assertions."""
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".codex/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def _plant_seed(mongo_db, tmp_path) -> str:
    """Capture a seed carrying a codex auth.json + config.toml via a
    scratch host."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "seedsrc"})
    ctx = ProcessContext(
        process_oid=oid, process_id="seedsrc", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": "fake-id", "access_token": "fake-access",
                "refresh_token": "ORIGINAL",
            },
            "last_refresh": "2026-07-02T00:00:00Z",
        }))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )


async def test_seeded_session_saves_back_and_releases_lease(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "seed_rotate")

    seed_id = await _plant_seed(mongo_db, tmp_path)
    await seeds.assign_to_pool(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        seed_id=seed_id, poolKey="pool1",
    )

    holders: list[str] = []

    async def provider(holder: str) -> str:
        got = await seeds.acquire(
            mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
            poolKey="pool1", holder=holder,
        )
        assert got is not None, "provider could not lease a seed"
        holders.append(holder)
        return got

    ctx = await _make_ctx(mongo_db, "codex_lease")
    await run_codex_session(ctx, _cfg(shim_install_dir, seed_id=provider))

    # The provider was invoked with the task's process_id as the lease holder.
    assert holders == ["codex_lease"], holders

    # Save-back fired: the seed's stored auth.json carries the rotated token.
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["refresh_token"] == "ROTATED-INSESSION", auth

    # Lease released: a fresh holder can immediately re-acquire the same
    # seed (a still-held 60s TTL lease would return None).
    regot = await seeds.acquire(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX,
        poolKey="pool1", holder="other",
    )
    assert regot == seed_id
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_lease.py -q`
Expected: FAIL on the save-back assertion (`refresh_token == "ROTATED-INSESSION"`) — the seed still carries `"ORIGINAL"` because teardown has no backstop yet — or on the re-acquire (lease never released).

- [ ] **Step 4: Implement — session body + teardown**

In `packages/optio-codex/src/optio_codex/session.py`:

1. In `_codex_body`, extend the `nonlocal` list with `cred_watch_task`, and add right after the `ctx.report_progress(None, "Codex is live")` line (before the liveness poll loop):

```python
        # Start the in-session credential watcher for a seeded session: it
        # saves back the rotated auth.json, and (when the seed is leased)
        # renews the lease and aborts the session on lease loss.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                    lease_holder=lease_holder,
                )
            )
```

2. In the `finally` block, insert between `teardown_session_tree` and Task 4's seed-capture step — the ORDER of these three blocks is the contract:

```python
        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: codex's
        # refresh already consumed the old refresh token server-side
        # (single-use, openai/codex#15410); a rotation in the last poll
        # window is persisted ONLY here. Runs after codex terminated so
        # auth.json is final.
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering, ported via grok): a new acquirer must never merge the
        # pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=CODEX_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. (The lease test does not depend on the 10s watcher tick — teardown's backstop does the save-back; the watcher spawn is covered by the session running with a seeded config without error, and its behavior unit-tested in Task 3.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_session_lease.py
git commit -m "feat(optio-codex): lease wiring + teardown save-back ordering (Stage 4)

Watcher spawned for seeded sessions; teardown order is the contract:
watcher-cancel (no race on the seed blob) -> backstop save-back
(last-window rotation persisted ONLY here) -> lease release (a new
acquirer must never merge the pre-save-back blob) -> seed capture."
```

---

### Task 6: `verify.py` — engine-free seed verify/refresh + headless probe surface

Port of `optio_grok.verify` + `run_grok_probe`. Engine-free (db-first, no ProcessContext): plant the seed into a throwaway workdir + CODEX_HOME, run one headless challenge probe — `codex exec --json -s read-only --skip-git-repo-check '<prompt>'` — under the per-task isolation env, verdict from **stdout only** (the exit code carries zero verdict bits), write the rotated auth.json back (validity-gated), stamp metadata + pool status. This is what keeps a seed **pool** alive between sessions: codex's 8-day proactive refresh means an unused seed's token ages toward a cliff, and the probe both proves liveness and rotates/persists a fresh token. Call only on a FREE seed or one whose lease the caller holds.

**Files:**
- Modify: `packages/optio-codex/tests/fake_codex.py` (probe mode)
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`_codex_isolation_env`, `run_codex_probe`)
- Create: `packages/optio-codex/src/optio_codex/verify.py`
- Modify: `packages/optio-codex/src/optio_codex/__init__.py`
- Test: `packages/optio-codex/tests/test_verify.py`

**Interfaces:**
- Consumes: `optio_agents.seeds.load_seed` / `plant_seed` / `overwrite_seed_member` / `declare_metadata` / `mark_seed_status`, `optio_host.paths.task_dir`, `host_actions.build_host` / `resolve_codex` / `_isolation_env`.
- Produces: `run_codex_probe(host, *, codex_executable, prompt, timeout_s=180.0) -> tuple[str, int]`; `verify_and_refresh_seed(db, *, prefix, suffix=CODEX_SEED_SUFFIX, seed_id, ssh=None, install_dir=None, encrypt=None, decrypt=None) -> bool`; fake env `FAKE_CODEX_PROBE` ∈ {alive, dead, echo, alive_badexit}.

- [ ] **Step 1: Extend the fake agent — probe mode**

In `packages/optio-codex/tests/fake_codex.py`, add:

```python
def _scenario_probe(prompt: str) -> int:
    """One-shot headless probe (``codex exec --json … '<prompt>'``) for
    verify_and_refresh.

    Mode via ``FAKE_CODEX_PROBE`` (default ``alive``):
      * ``alive`` — rotate the refresh token (as a live codex would: its
        8-day proactive refresh / refresh-on-401 rewrites auth.json in
        place) and print exec-style JSONL carrying the challenge answer;
        exit 0.
      * ``dead``  — print an auth error and exit 1 (no answer token).
      * ``echo``  — echo the prompt back verbatim and exit 1 (proves a
        prompt-echoing error path does not false-positive: the answer token
        is absent from the prompt).
      * ``alive_badexit`` — answer present but exit 3 (stdout-only verdict).
    """
    mode = os.environ.get("FAKE_CODEX_PROBE", "alive").strip()
    if mode == "dead":
        print(json.dumps({"type": "error",
                          "message": "401 Unauthorized (invalid_grant)"}),
              flush=True)
        return 1
    if mode == "echo":
        print(json.dumps({"type": "error",
                          "message": f"cannot process request: {prompt}"}),
              flush=True)
        return 1
    ch = _codex_home()
    ch.mkdir(parents=True, exist_ok=True)
    _rotate_auth(ch, "ROTATED-BY-PROBE")
    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}),
          flush=True)
    print(json.dumps({"type": "item.completed", "item": {
        "type": "agent_message",
        "text": "The capital of France is Paris."}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    return 3 if mode == "alive_badexit" else 0
```

and at the TOP of `main()`, before the scenario argparse (the `exec` subcommand must not trip the option parser):

```python
    # Headless probe mode: `codex exec --json [-s MODE] [--skip-git-repo-check]
    # [-C DIR] '<prompt>'`. Detected before argparse; the prompt is the last
    # positional argument.
    argv = sys.argv[1:]
    if argv and argv[0] == "exec":
        prompt = argv[-1] if len(argv) > 1 and not argv[-1].startswith("-") else ""
        return _scenario_probe(prompt)
```

(Task 5's `_rotate_auth` is already present; if Tasks are executed out of order, pull it in with this step.)

- [ ] **Step 2: Write the failing tests**

Create `packages/optio-codex/tests/test_verify.py`:

```python
"""verify_and_refresh_seed unit tests (fake codex probe, real Mongo).

Engine-free verify: plant a seed into a throwaway workdir + CODEX_HOME, run
one headless ``codex exec --json -s read-only --skip-git-repo-check
'<probe>'`` challenge-answer via the codex shim (fake_codex's probe mode),
take the verdict from stdout only, and write the rotated auth.json back
into the seed. No real codex binary or network.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.verify import verify_and_refresh_seed


async def _make_seed(mongo_db, tmp_path) -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": "fake-id", "access_token": "fake-access",
                "refresh_token": "ORIGINAL",
            },
            "last_refresh": "2026-07-02T00:00:00Z",
        }))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX,
        encrypt=None,
    )


async def _seed_auth(mongo_db, seed_id: str) -> dict:
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".codex/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def test_alive_and_writes_back_rotated_auth(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_PROBE", "alive")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is True

    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["tokens"]["refresh_token"] == "ROTATED-BY-PROBE", auth

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["metadata"]["verify"]["alive"] is True
    assert doc["status"] == "alive"


async def test_dead_on_auth_error(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FAKE_CODEX_PROBE", "dead")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is False

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_prompt_echo_does_not_false_positive(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # An error path that echoes the prompt must NOT count as alive — the
    # challenge answer token ("paris") is absent from the prompt.
    monkeypatch.setenv("FAKE_CODEX_PROBE", "echo")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is False


async def test_exit_code_carries_no_verdict(
    mongo_db, task_root, shim_install_dir, tmp_path, monkeypatch,
):
    # Answer present + non-zero exit -> still alive (stdout-only verdict).
    monkeypatch.setenv("FAKE_CODEX_PROBE", "alive_badexit")
    seed_id = await _make_seed(mongo_db, tmp_path)

    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
        install_dir=str(shim_install_dir),
    )
    assert alive is True


async def test_unknown_seed(mongo_db, task_root, shim_install_dir):
    alive = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
        install_dir=str(shim_install_dir),
    )
    assert alive is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_verify.py -q`
Expected: all FAIL with `ModuleNotFoundError: No module named 'optio_codex.verify'`.

- [ ] **Step 4: Implement — probe surface in `host_actions.py`**

Add after `_isolation_env`:

```python
def _codex_isolation_env(host: "Host") -> dict[str, str]:
    """Per-task isolation env for a headless probe, derived from
    ``host.workdir`` via :func:`_isolation_env` (the single source of truth)
    — so the probe reads the seed's planted ``home/.codex/auth.json`` under
    the same HOME/CODEX_HOME/XDG identity as the launch.

    ``run_command`` replaces (not merges) the child env, so PATH is carried
    explicitly (the worker's PATH plus the per-task ``.local/bin``) or a
    missing interpreter/bash would break the probe."""
    iso = _isolation_env(host.workdir)
    base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {**iso, "PATH": f"{iso['HOME']}/.local/bin:{base_path}"}


async def run_codex_probe(
    host: "Host",
    *,
    codex_executable: str,
    prompt: str,
    timeout_s: float = 180.0,
) -> "tuple[str, int]":
    """Headless one-shot ``codex exec --json -s read-only
    --skip-git-repo-check '<prompt>'`` under the per-task isolation env.
    Returns (stdout, exit_code).

    ``exec`` mode has no approvals (hard approval_policy=never) and
    ``-s read-only`` keeps the probe from touching anything; the JSONL
    events land on stdout. The caller's verdict is a challenge-answer match
    on stdout; the exit code is diagnostics only."""
    argv = [
        codex_executable, "exec", "--json", "-s", "read-only",
        "--skip-git-repo-check", prompt,
    ]
    inner = " ".join(shlex.quote(a) for a in argv)
    cmd = f"cd {shlex.quote(host.workdir.rstrip('/'))} && {inner}"
    # Layer the per-task HOME/CODEX_HOME overrides on top of the ambient
    # env, mirroring the session launch (which inherits, not ``env -i``).
    # run_command replaces the child env, so the merge is explicit here. The
    # caller runs this on a host whose environment carries no provider API
    # keys (see verify_and_refresh_seed).
    env = {**os.environ, **_codex_isolation_env(host)}
    result = await asyncio.wait_for(
        host.run_command(f"bash -lc {shlex.quote(cmd)}", env=env),
        timeout=timeout_s,
    )
    return (result.stdout or "", result.exit_code)
```

- [ ] **Step 5: Implement — `packages/optio-codex/src/optio_codex/verify.py`**

```python
"""Standalone seed verify/refresh for codex seeds.

Engine-free: db-first, no ProcessContext/HookContext. Plants a seed into a
throwaway workdir + CODEX_HOME, runs the codex binary once headless
(``codex exec --json -s read-only --skip-git-repo-check '<probe>'``)
against a challenge-answer prompt, takes the verdict from stdout only, and
writes the refreshed (rotated) auth.json back into the seed.

This stage is MANDATORY for codex, not hygiene: ChatGPT-mode auth carries a
single-use rotating refresh token (openai/codex#15410) with an 8-day
proactive refresh — an unused pooled seed ages toward a dead token, and the
probe both proves liveness and persists the rotation. Direct adaptation of
``optio_grok.verify`` / ``optio_opencode.verify``. Codex has no separate
model gate (the model in config.toml is optional), so the probe always runs.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from optio_host.paths import task_dir

from optio_agents import seeds
from optio_codex import host_actions
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# Challenge-answer probe: the answer token ("paris") must NOT appear in the
# prompt (an error path that echoes the prompt can then never
# false-positive) and must be improbable in error noise (a word, not a
# digit).
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)

_AUTH_RELPATH = "home/.codex/auth.json"
_AUTH_MEMBER = ".codex/auth.json"


async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = CODEX_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> bool:
    """Verify a seed by probing codex with its credentials; refresh + save
    back.

    Returns True iff codex answered the challenge (the seed is alive). Never
    raises for a dead seed. Stamps the verdict as seed metadata and marks
    the seed's pool status (dead seeds are never handed out by
    seeds.acquire).

    Call only on a FREE seed, or one whose lease the caller holds: the probe
    rotates the single-use refresh token, so verifying a seed in use by a
    live session leaves that session's next refresh stranded (and its
    save-back would clobber this one). The caller owns the lease discipline;
    this function does not acquire or check leases.

    Run on a host whose environment carries no OPENAI_API_KEY — an inherited
    key could mask a dead seed.
    """
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return False

    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-codex",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    alive = False
    try:
        await host.setup_workdir()
        codex_exec = await host_actions.resolve_codex(
            host, install_dir=install_dir, install_if_missing=False,
        )
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=CODEX_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )

        stdout, exit_code = await host_actions.run_codex_probe(
            host, codex_executable=codex_exec, prompt=PROBE_PROMPT,
        )
        # Verdict: stdout-only. The exit code carries zero verdict bits
        # (answer present proves the full chain regardless; requiring exit 0
        # would only add a false-dead path) — diagnostics only.
        alive = PROBE_ANSWER_RE.search(stdout) is not None
        if not alive:
            _LOG.info(
                "seed %s: probe dead (exit=%s, stdout[:200]=%r)",
                seed_id, exit_code, stdout[:200],
            )

        # Write back the (possibly rotated) auth.json — valid files only
        # (same validity bar as the watcher's save-back gate: tokens or
        # OPENAI_API_KEY non-null).
        workdir = host.workdir.rstrip("/")
        try:
            auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
            auth = json.loads(auth_raw.decode("utf-8"))
            if isinstance(auth, dict) and (
                auth.get("tokens") is not None
                or auth.get("OPENAI_API_KEY") is not None
            ):
                await seeds.overwrite_seed_member(
                    db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                    member_path=_AUTH_MEMBER, content=auth_raw,
                    encrypt=encrypt, decrypt=decrypt,
                )
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            _LOG.warning(
                "seed %s: no valid auth.json after probe; skipping write-back",
                seed_id,
            )

        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {
                "alive": alive,
                "checkedAt": datetime.now(timezone.utc),
            }},
        )
        await seeds.mark_seed_status(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            status="alive" if alive else "dead",
        )
        return alive
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

In `packages/optio-codex/src/optio_codex/__init__.py`: add `from optio_codex.verify import verify_and_refresh_seed` and `"verify_and_refresh_seed"` to `__all__`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-codex/src/optio_codex/verify.py packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/src/optio_codex/__init__.py packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_verify.py
git commit -m "feat(optio-codex): engine-free seed verify/refresh via codex exec probe (Stage 4)

Throwaway taskdir + plant_seed + one headless 'codex exec --json -s
read-only --skip-git-repo-check' challenge; verdict is stdout-only
(exit code = diagnostics), rotated auth.json written back via
overwrite_seed_member behind the validity gate, verdict stamped via
declare_metadata + mark_seed_status. Mandatory for codex: single-use
rotating refresh tokens (openai/codex#15410; 8-day proactive refresh)."
```

---

## Stage 5 — Binary cache

### Task 7: Cache-backed `ensure_codex_installed` — hit + `cp -L` seed from host

Rework `ensure_codex_installed` around an optio-owned, evictable, worker-side cache (grok `host_actions.py:140-227` pattern): `${OPTIO_CODEX_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin}`, resolved **on the host** via a shell echo (grok `:62-93` — so RemoteHost gets the *remote* location and the cache stays shared + evictable, never under a workdir, never the operator's `~/.codex`). `codex_install_dir` becomes the cache-dir override (grok semantics — the existing shim-dir tests keep passing as cache hits). Plan A's per-task launch path is **preserved**: the returned path stays `<workdir>/home/.local/bin/codex` via `_provision_task_home` — the symlink now points into the cache, so kill-scoping keeps working unchanged. The no-host-binary error is replaced by the real download in Task 8.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py`
- Test: `packages/optio-codex/tests/test_codex_cache.py`

**Interfaces:**
- Consumes: `resolve_codex` (unchanged: host-PATH/dir resolution, also used by verify), `_provision_task_home` (unchanged).
- Produces: `_CODEX_CACHE_DIR_SHELL_DEFAULT`; `async _resolve_codex_cache_dir(host, override) -> str`; `ensure_codex_installed` now: cache hit → per-task symlink to `<cache>/codex`; miss → `cp -L` seed from host codex → per-task symlink; miss + `install_if_missing=False` → raise.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_codex_cache.py`:

```python
"""Stage 5: optio-owned, evictable codex binary cache.

``ensure_codex_installed`` resolves the codex binary through a cache dir
that lives outside the task workdir and never the operator's ``~/.codex``:

* cache HIT — ``<cache>/codex`` already executable → per-task symlink to it.
* cache MISS — the resolved host codex is copied into ``<cache>/codex``
  (``cp -L`` deref: a stable copy, independent of host autoupdates).
* default location — ``OPTIO_CODEX_CACHE_DIR`` /
  ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin``, resolved against the
  worker's real env; never under the workdir.
* the RETURNED path is always Plan A's per-task
  ``<workdir>/home/.local/bin/codex`` symlink (kill-scoping preserved) —
  it now resolves INTO the cache.
"""

from __future__ import annotations

import os
import pathlib

import pytest
from optio_host.host import LocalHost

from optio_codex import host_actions


class _FakeHookCtx:
    """Minimal hook_ctx: a real LocalHost plus a no-op progress reporter."""

    def __init__(self, host: LocalHost) -> None:
        self._host = host

    def report_progress(self, percent, message=None) -> None:  # noqa: ANN001
        pass


def _write_exe(path: pathlib.Path, body: str = "#!/bin/bash\necho fake-codex\n") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


async def _local_ctx(tmp_path: pathlib.Path) -> _FakeHookCtx:
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    return _FakeHookCtx(host)


def _per_task_path(ctx: _FakeHookCtx) -> str:
    return f"{ctx._host.workdir}/home/.local/bin/codex"


@pytest.mark.asyncio
async def test_cache_hit_returns_per_task_symlink_into_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _write_exe(cache / "codex")

    # A cache hit must not consult the host codex at all.
    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("resolve_codex should not be called on a cache hit")

    monkeypatch.setattr(host_actions, "resolve_codex", _boom)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
    # Plan A's kill-scoped per-task launch path is preserved…
    assert result == _per_task_path(ctx)
    # …and now resolves into the optio-owned cache.
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_cache_miss_seeds_from_host_codex(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()  # empty → miss
    source = _write_exe(tmp_path / "hostbin" / "codex")

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        return str(source)

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
    assert result == _per_task_path(ctx)
    assert (cache / "codex").is_file()
    assert os.access(cache / "codex", os.X_OK)
    # Seeded as a real copy (cp -L deref), not a symlink back to the host
    # binary (which the operator may autoupdate under us).
    assert not (cache / "codex").is_symlink()
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_no_install_raises_on_miss(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not seed when install_if_missing=False")

    monkeypatch.setattr(host_actions, "resolve_codex", _boom)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="install_if_missing=False"):
        await host_actions.ensure_codex_installed(
            ctx, install_dir=str(cache), install_if_missing=False,
        )


@pytest.mark.asyncio
async def test_default_cache_dir_from_env(tmp_path, monkeypatch):
    """With no override, OPTIO_CODEX_CACHE_DIR (worker real env) decides the
    cache dir — never the workdir, never the operator's ~/.codex."""
    cache = tmp_path / "oai-cache" / "bin"
    _write_exe(cache / "codex")
    monkeypatch.setenv("OPTIO_CODEX_CACHE_DIR", str(cache))
    ctx = await _local_ctx(tmp_path)

    result = await host_actions.ensure_codex_installed(ctx)  # no install_dir
    assert result == _per_task_path(ctx)
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))


@pytest.mark.asyncio
async def test_cache_miss_no_host_codex_raises(tmp_path, monkeypatch):
    # Task 8 replaces this expectation with the real release download.
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    ctx = await _local_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="no codex binary"):
        await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_codex_cache.py -q`
Expected: FAIL — the current `ensure_codex_installed` calls `resolve_codex` unconditionally (the `_boom` hit-test trips), and there is no cache seeding.

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add near `_DEFAULT_INSTALL_SUBDIR`:

```python
# The optio-owned codex binary cache lives on the WORKER, outside every task
# workdir and never the operator's ``~/.codex``. Default:
# ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin``; ``OPTIO_CODEX_CACHE_DIR``
# overrides. Resolved via a shell echo so RemoteHost gets the remote
# location, and so the cache stays shared + evictable (never snapshotted,
# re-seeded/re-downloaded on a miss).
_CODEX_CACHE_DIR_SHELL_DEFAULT = (
    "${OPTIO_CODEX_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin}"
)


async def _resolve_codex_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the optio-owned codex binary-cache dir as an absolute worker
    path.

    ``override`` (``config.codex_install_dir``) wins. Otherwise the worker's
    real env decides via a shell echo: ``OPTIO_CODEX_CACHE_DIR`` else
    ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-codex/bin`` — resolved on the
    host so RemoteHost gets the remote location. Mirrors grok's
    ``_resolve_grok_cache_dir`` (the ttyd ``_resolve_install_dir`` is a
    separate, home-relative resolver and is intentionally left untouched)."""
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_CODEX_CACHE_DIR_SHELL_DEFAULT}"')
    path = (r.stdout or "").strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve codex cache dir on host "
            f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
        )
    return path.rstrip("/")
```

Replace `ensure_codex_installed` with:

```python
async def ensure_codex_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Provision ``codex`` for this task from the optio-owned binary cache.

    The cache dir (``_resolve_codex_cache_dir``) lives on the worker outside
    any task workdir and never the operator's autoupdating ``~/.codex`` — so
    it stays shared, evictable, and unsnapshotted. Resolution order:

    - **cache hit** — ``<cache>/codex`` is already executable.
    - **cache miss** — seed the cache from the resolved host ``codex``
      (login-shell ``command -v codex`` via :func:`resolve_codex`), copying
      it into ``<cache>/codex`` (``cp -L`` deref + chmod + re-verify).
    - **no host codex** — raise (Task 8 wires the real GitHub-release
      download here).

    Whatever fills the cache, the RETURNED path is always the per-task
    ``<workdir>/home/.local/bin/codex`` symlink (via
    :func:`_provision_task_home`) so teardown's anchored pkill stays scoped
    to this task — the symlink simply points into the cache now.

    Raises when the cache is empty and ``install_if_missing=False``.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, "Locating codex…")

    cache_dir = await _resolve_codex_cache_dir(host, install_dir)
    cached = f"{cache_dir}/codex"

    probe = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" in (probe.stdout or ""):
        _LOG.info("ensure_codex_installed: cache HIT (%s)", cached)
        return await _provision_task_home(host, shared_codex_path=cached)

    if not install_if_missing:
        raise RuntimeError(
            f"codex not present in cache at {cached!r} and "
            f"install_if_missing=False; nothing to do."
        )

    # Cache miss — seed the optio-owned cache from the resolved host codex.
    try:
        source = await resolve_codex(host, install_dir=None, install_if_missing=False)
    except RuntimeError as exc:
        raise RuntimeError(
            f"no codex binary available to seed the optio cache "
            f"(cache_dir={cache_dir!r}); a host codex must be on the worker "
            f"PATH. (The GitHub-release auto-download lands with the next "
            f"task of this plan.)"
        ) from exc

    hook_ctx.report_progress(None, "Seeding codex cache…")
    await _install_into_cache_from_host(host, source=source, cached=cached,
                                        cache_dir=cache_dir)
    return await _provision_task_home(host, shared_codex_path=cached)


async def _install_into_cache_from_host(
    host: "Host", *, source: str, cached: str, cache_dir: str,
) -> None:
    """Copy a resolved host binary into the cache: mkdir + ``cp -L`` (deref:
    a symlinked host codex becomes a real, stable copy independent of the
    operator's autoupdater) + chmod + re-verify."""
    mk = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
    if mk.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {cache_dir!r} failed (exit {mk.exit_code}): "
            f"{(mk.stderr or '').strip()[:200]}"
        )
    cp = await host.run_command(
        f"cp -L {shlex.quote(source)} {shlex.quote(cached)}"
    )
    if cp.exit_code != 0:
        raise RuntimeError(
            f"seeding codex cache (cp {source!r} -> {cached!r}) failed "
            f"(exit {cp.exit_code}): {(cp.stderr or '').strip()[:200]}"
        )
    ch = await host.run_command(f"chmod +x {shlex.quote(cached)}")
    if ch.exit_code != 0:
        raise RuntimeError(
            f"chmod +x {cached!r} failed (exit {ch.exit_code}): "
            f"{(ch.stderr or '').strip()[:200]}"
        )
    verify = await host.run_command(
        f"[ -x {shlex.quote(cached)} ] && echo OK || true"
    )
    if "OK" not in (verify.stdout or ""):
        raise RuntimeError(
            f"codex cache seed completed but {cached!r} is still not "
            f"executable on the host. Check the seed source {source!r}."
        )
    _LOG.info("ensure_codex_installed: cache MISS -> seeded from %s", source)
```

**Consistency check (do not skip):** the existing session/seed/lease tests pass `codex_install_dir=<shim dir>` — with this change that directory is treated as the cache and the shim `codex` symlink is an executable cache hit, so they stay green. `resolve_codex` keeps its `install_dir` branch (used by `verify.py` with the shim dir); do not remove it. Also update the Stage-0 wording inside `resolve_codex`'s final error message (it referenced "Stage 0 has no auto-install"): change that raise to

```python
    raise RuntimeError(
        "codex not found on the worker (looked via 'command -v codex'). "
        "Install codex manually (npm i -g @openai/codex) or rely on the "
        "optio cache auto-download via ensure_codex_installed."
    )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. If Plan A's `test_resolve_codex_missing_names_the_stage_gap` asserts the old "binary cache" wording, update that assertion to match the new message (`match="auto-download"`) — the honesty test tracks the message's truth, and the truth changed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_codex_cache.py packages/optio-codex/tests/test_host_actions.py
git commit -m "feat(optio-codex): optio-owned evictable binary cache (Stage 5)

Cache dir \${OPTIO_CODEX_CACHE_DIR:-\${XDG_CACHE_HOME:-\$HOME/.cache}/optio-codex/bin}
resolved host-side (remote-correct); hit -> use, miss -> cp -L seed from
the host binary. The per-task <workdir>/home/.local/bin/codex launch
symlink (kill-scoping) is preserved and now points into the cache."
```

---

### Task 8: Real release auto-download — `install_if_missing` becomes real

Codex ships a single static musl binary per release: `https://github.com/openai/codex/releases/download/rust-v<ver>/codex-<triple>.tar.gz`. When the cache is empty AND no host binary exists, download the pinned release via `hook_ctx.download_file` (dashboard byte-progress, host-side placement — remote-correct) and extract it into the cache. This closes grok's documented gap ("vendor auto-install is a future refinement") — codex has a clean, stable URL scheme.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py`
- Test: `packages/optio-codex/tests/test_codex_cache.py`

**Interfaces:**
- Consumes: `hook_ctx.download_file(url, dest)` (same primitive the ttyd installer uses), `Host.run_command` (uname probes, tar, mv).
- Produces: `_CODEX_VERSION` (pinned), `_CODEX_RELEASE_BASE`, `async _detect_codex_asset_name(host) -> str`, `async _download_codex_into_cache(hook_ctx, *, cache_dir, cached) -> None`; `ensure_codex_installed`'s no-host-codex branch now downloads.

- [ ] **Step 1: Update/extend the tests (they fail first)**

In `packages/optio-codex/tests/test_codex_cache.py`, **replace** `test_cache_miss_no_host_codex_raises` with the following, and append the rest:

```python
def _fake_release_tarball(member_name: str = "codex-x86_64-unknown-linux-musl") -> bytes:
    """A codex release tar.gz: a single static-binary member."""
    import io
    import tarfile

    body = b"#!/bin/bash\necho downloaded-codex\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(body)
        info.mode = 0o644          # releases ship without +x; install chmods
        tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


class _DownloadingHookCtx(_FakeHookCtx):
    """Fake hook_ctx whose download_file writes a prepared release tarball."""

    def __init__(self, host, payload: bytes) -> None:
        super().__init__(host)
        self.payload = payload
        self.urls: list[str] = []

    async def download_file(self, url: str, dest: str) -> None:
        self.urls.append(url)
        with open(dest, "wb") as fh:
            fh.write(self.payload)


@pytest.mark.asyncio
async def test_cache_miss_no_host_codex_downloads_release(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    ctx = _DownloadingHookCtx(host, _fake_release_tarball())

    result = await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))

    # The pinned release URL for this machine's arch was requested…
    assert len(ctx.urls) == 1
    url = ctx.urls[0]
    assert host_actions._CODEX_VERSION in url
    assert url.startswith(
        "https://github.com/openai/codex/releases/download/rust-v"
    )
    assert url.endswith("-unknown-linux-musl.tar.gz")
    # …the single tarball member landed as <cache>/codex, executable…
    assert (cache / "codex").is_file()
    assert os.access(cache / "codex", os.X_OK)
    assert (cache / "codex").read_bytes().startswith(b"#!/bin/bash")
    # …behind the per-task launch symlink, and no temp litter remains.
    assert result == _per_task_path(ctx)
    assert os.path.realpath(result) == os.path.realpath(str(cache / "codex"))
    leftovers = [p.name for p in cache.iterdir() if p.name != "codex"]
    assert leftovers == [], leftovers


class _UnameResult:
    def __init__(self, stdout: str, exit_code: int) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code


class _UnameHost:
    """Fake Host answering only the two uname probes."""

    def __init__(self, os_name: str, arch: str) -> None:
        self._answers = {"uname -s": os_name, "uname -m": arch}

    async def run_command(self, cmd, **kwargs):
        if cmd in self._answers:
            return _UnameResult(self._answers[cmd], 0)
        return _UnameResult("", 1)


@pytest.mark.asyncio
async def test_detect_codex_asset_name_arch_map():
    assert await host_actions._detect_codex_asset_name(
        _UnameHost("Linux", "x86_64")
    ) == "codex-x86_64-unknown-linux-musl.tar.gz"
    assert await host_actions._detect_codex_asset_name(
        _UnameHost("Linux", "aarch64")
    ) == "codex-aarch64-unknown-linux-musl.tar.gz"
    with pytest.raises(RuntimeError, match="arch"):
        await host_actions._detect_codex_asset_name(_UnameHost("Linux", "armv7l"))
    with pytest.raises(RuntimeError, match="OS"):
        await host_actions._detect_codex_asset_name(_UnameHost("Darwin", "x86_64"))


@pytest.mark.asyncio
async def test_download_multi_member_tarball_rejected(tmp_path, monkeypatch):
    """A tarball that does not contain exactly one file is refused — never
    guess which member is the binary."""
    import io
    import tarfile

    async def _resolve(host, *, install_dir=None, install_if_missing=True):  # noqa: ANN001
        raise RuntimeError("codex not found on the worker")

    monkeypatch.setattr(host_actions, "resolve_codex", _resolve)

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name in ("codex-x86_64-unknown-linux-musl", "README.md"):
            info = tarfile.TarInfo(name=name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))

    cache = tmp_path / "cache"
    cache.mkdir()
    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    ctx = _DownloadingHookCtx(host, out.getvalue())

    with pytest.raises(RuntimeError, match="exactly one"):
        await host_actions.ensure_codex_installed(ctx, install_dir=str(cache))
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_codex_cache.py -q`
Expected: the three new/replaced tests FAIL (`AttributeError: … no attribute '_CODEX_VERSION'` / the old raise fires instead of downloading).

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add near the cache constants:

```python
# Pinned codex release for auto-download. This is the version the design
# doc's live probes ran against (2026-07-02); bump deliberately, re-probing
# the wire facts (exec JSONL vocabulary, app-server surface) on upgrade.
_CODEX_VERSION = "0.142.5"
_CODEX_RELEASE_BASE = (
    f"https://github.com/openai/codex/releases/download/rust-v{_CODEX_VERSION}"
)


async def _detect_codex_asset_name(host: "Host") -> str:
    """Return the upstream release-asset filename for the host's arch/OS.

    Codex publishes single-binary tar.gz assets per target triple; the
    static musl builds are the portable Linux choice. Raises RuntimeError
    on unsupported (OS, arch) combinations (darwin support = pre-install
    codex on the worker or seed the cache manually).
    """
    r_os = await host.run_command("uname -s")
    os_name = (r_os.stdout or "").strip()
    if r_os.exit_code != 0 or os_name != "Linux":
        raise RuntimeError(
            f"unsupported host OS {os_name!r} for codex auto-download "
            f"(Linux musl builds only; on macOS pre-install codex or "
            f"pre-populate the cache)."
        )
    r_arch = await host.run_command("uname -m")
    arch = (r_arch.stdout or "").strip()
    if r_arch.exit_code != 0 or arch not in {"x86_64", "aarch64"}:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for codex auto-download. "
            f"See https://github.com/openai/codex/releases for available "
            f"assets."
        )
    return f"codex-{arch}-unknown-linux-musl.tar.gz"


async def _download_codex_into_cache(
    hook_ctx: "HookContextProtocol", *, cache_dir: str, cached: str,
) -> None:
    """Download the pinned codex release tarball and install its single
    binary member as ``<cache>/codex``.

    The tarball carries exactly one file (the static binary, named by
    triple); extraction goes through a private scratch dir inside the cache
    so a concurrent task never sees a half-written ``codex``, and a tarball
    with any other shape is refused rather than guessed at. Everything runs
    through Host primitives + ``hook_ctx.download_file`` (byte-progress in
    the dashboard), so it is remote-correct.
    """
    host = hook_ctx._host
    asset = await _detect_codex_asset_name(host)
    url = f"{_CODEX_RELEASE_BASE}/{asset}"

    mk = await host.run_command(f"mkdir -p {shlex.quote(cache_dir)}")
    if mk.exit_code != 0:
        raise RuntimeError(
            f"mkdir -p {cache_dir!r} failed (exit {mk.exit_code}): "
            f"{(mk.stderr or '').strip()[:200]}"
        )

    tarball = f"{cache_dir}/.codex-download.tar.gz"
    scratch = f"{cache_dir}/.codex-extract"
    hook_ctx.report_progress(None, f"Downloading codex {_CODEX_VERSION} ({asset})…")
    try:
        await hook_ctx.download_file(url, tarball)

        r = await host.run_command(
            f"rm -rf {shlex.quote(scratch)} && mkdir -p {shlex.quote(scratch)} "
            f"&& tar -xzf {shlex.quote(tarball)} -C {shlex.quote(scratch)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"extracting codex release {asset!r} failed "
                f"(exit {r.exit_code}): {(r.stderr or '').strip()[:200]}"
            )
        listing = await host.run_command(
            f"find {shlex.quote(scratch)} -mindepth 1"
        )
        entries = [l for l in (listing.stdout or "").splitlines() if l.strip()]
        if len(entries) != 1:
            raise RuntimeError(
                f"codex release {asset!r} must contain exactly one file; "
                f"found {len(entries)} entries: {entries[:5]!r}. Refusing to "
                f"guess which member is the binary."
            )
        mv = await host.run_command(
            f"mv {shlex.quote(entries[0])} {shlex.quote(cached)} "
            f"&& chmod +x {shlex.quote(cached)}"
        )
        if mv.exit_code != 0:
            raise RuntimeError(
                f"installing codex into cache failed (exit {mv.exit_code}): "
                f"{(mv.stderr or '').strip()[:200]}"
            )
        verify = await host.run_command(
            f"[ -x {shlex.quote(cached)} ] && echo OK || true"
        )
        if "OK" not in (verify.stdout or ""):
            raise RuntimeError(
                f"codex download completed but {cached!r} is still not "
                f"executable on the host."
            )
        _LOG.info(
            "ensure_codex_installed: cache MISS -> downloaded %s", url,
        )
    finally:
        await host.run_command(
            f"rm -rf {shlex.quote(tarball)} {shlex.quote(scratch)}"
        )
```

In `ensure_codex_installed`, replace the `except RuntimeError as exc: raise RuntimeError("no codex binary available …") from exc` block with:

```python
    try:
        source = await resolve_codex(host, install_dir=None, install_if_missing=False)
    except RuntimeError:
        # No host codex to seed from — REAL auto-download of the pinned
        # release (install_if_missing is genuinely honored from Stage 5 on).
        await _download_codex_into_cache(hook_ctx, cache_dir=cache_dir, cached=cached)
        return await _provision_task_home(host, shared_codex_path=cached)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. (No network is touched: every download in tests goes through the fake hook_ctx.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_codex_cache.py
git commit -m "feat(optio-codex): real GitHub-release auto-download into the binary cache (Stage 5)

Pinned rust-v0.142.5, triple from uname probes
({x86_64,aarch64}-unknown-linux-musl), hook_ctx.download_file +
single-member tar.gz extraction through a scratch dir (refuses
multi-member tarballs; no half-written cache binary). Closes grok's
documented auto-install gap; install_if_missing is now real."
```

---

## Demo trio (Part 5)

### Task 9: Upgrade the codex demo to the seed lifecycle (setup + seed-pinned iframe)

Plan A shipped a single plain iframe demo (`codex-demo-iframe`, API-key passthrough). Replace it with the trio pattern ported from `packages/optio-demo/src/optio_demo/tasks/grok.py`: a static **seed-setup** task (operator logs into codex interactively in the ttyd terminal, stops the task, teardown captures the seed) and one **seed-pinned iframe** task per captured seed that auto-appears via `fw.resync()`, with the full hook walkthrough. The seed-pinned **conversation** demo completes the trio in Plan D (Stage 6) — leave a one-line comment marking that.

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/codex.py` (full rewrite of the Plan A file)
- Test: none automated beyond the import smoke below (demo tasks are exercised in the live dashboard; this mirrors how grok/claudecode/opencode demos ship).

**Interfaces:**
- Consumes: `optio_codex.create_codex_task` / `CodexTaskConfig` / `list_seeds` / `HookContext` / `SSHConfig`, `optio_demo.tasks._feedback.make_feedback_on_deliverable`, services `db` / `prefix` / `optio` (the framework handle: `fw.resync()`, `fw.mongo_store`).
- Produces: tasks `codex-seed-setup` + `codex-demo-seed-<seed_id>`; sidecar collection `{prefix}_demo_codex_seeds` (friendly names only — the real store stays `{prefix}_codex_seeds`); env knobs `OPTIO_CODEX_DEMO_SSH_{HOST,USER,KEY_PATH,PORT}`, `OPTIO_CODEX_DEMO_OPENAI_API_KEY`.

- [ ] **Step 1: Read the conventions**

Read fully before writing: `packages/optio-demo/src/optio_demo/tasks/grok.py` (at the main checkout `/home/csillag/deai/optio/packages/optio-demo/src/optio_demo/tasks/grok.py` — the direct template), the Plan A `codex.py` being replaced, and `packages/optio-demo/src/optio_demo/tasks/_feedback.py` (call shape of `make_feedback_on_deliverable`). Keep Plan A's `CONTEXT_TXT` / prompt / hook helpers where they overlap.

- [ ] **Step 2: Rewrite `packages/optio-demo/src/optio_demo/tasks/codex.py`**

```python
"""Demo tasks for optio-codex — the seed lifecycle.

Exposes a static **"Setup Codex seed"** task plus a seed-pinned iframe run
task per captured seed. The operator launches setup, logs into codex
interactively in the ttyd TUI, then stops the task; on teardown the
environment is captured as a seed and the seed-pinned demo tasks appear
(via in-process ``resync``). Authentication comes from the seed, not an
inherited host identity — codex runs under HOME-isolation
(``HOME=<workdir>/home``), so the host user's ``~/.codex`` is not
inherited; the seed supplies ``auth.json`` / ``config.toml`` instead.
(The seed-pinned CONVERSATION demo completes the trio at Stage 6 / Plan D.)

Login options inside the setup terminal:

- ``codex login --device-auth`` — fully headless (URL + one-time code, done
  in any browser; loopback OAuth is NOT relied on).
- API key: export ``OPTIO_CODEX_DEMO_OPENAI_API_KEY`` before starting the
  demo (surfaced to the session env as ``OPENAI_API_KEY``), then run
  ``printenv OPENAI_API_KEY | codex login --with-api-key`` (codex does not
  honor the env var at runtime; login writes auth.json).

Gating mirrors the wrapper's own seed store: the pinned tasks are driven by
``optio_codex.list_seeds`` over the real ``{prefix}_codex_seeds``
collection. The demo keeps a small ``{prefix}_demo_codex_seeds`` sidecar
(written by ``on_seed_saved``) purely to attach a friendly display name to
each seed; seeds without a name fall back to their seed id.

Defaults to local mode; set ``OPTIO_CODEX_DEMO_SSH_HOST`` to run via SSH on
a remote host. Relevant env vars (all optional except ``_HOST``):
``OPTIO_CODEX_DEMO_SSH_{HOST,USER,KEY_PATH,PORT}``.

Hook walkthrough (mirrors the grok/claudecode/opencode demos), wired on
each seed-pinned task: ``before_execute`` runs ``whoami`` + ships
``context.txt``; ``on_deliverable`` exercises the agent feedback channel
(reject → nudge → accept); ``after_execute`` reads ``./optio.log`` back and
reports a one-line keyword summary.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from optio_codex import (
    CodexTaskConfig,
    HookContext,
    SSHConfig,
    create_codex_task,
    list_seeds,
)
from optio_core.models import TaskInstance

from optio_demo.tasks._feedback import make_feedback_on_deliverable


CONTEXT_TXT = b"""\
Mission code-name: Project Petunia
Authorized color: turquoise
"""


CONSUMER_PROMPT = (
    "First, read the file `./context.txt` in your working directory. It "
    "contains a mission code-name and an authorized color. Ship a "
    "deliverable file at `./deliverables/mission-report.txt` containing "
    "the mission code-name, the authorized color, and the number 42. "
    "Then signal completion by appending a `DONE` line to the "
    "`./optio.log` file (writing `DONE` in the chat has no effect — it "
    "must go into that file)."
)


DEMO_SEED_COLLECTION_SUFFIX = "_demo_codex_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session for a human operator. You do not "
    "need to do anything. The operator will log into Codex directly in "
    "this terminal, then stop this task — their credentials/settings are "
    "then captured as a reusable seed and seed-pinned demo tasks appear "
    "automatically. Do not run setup commands or narrate; if the operator "
    "asks you something, answer briefly, otherwise stay idle."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_CODEX_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_CODEX_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_CODEX_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_CODEX_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_CODEX_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


def _setup_env() -> dict[str, str] | None:
    """Optional API key for `codex login --with-api-key` in the setup TUI."""
    api_key = os.environ.get("OPTIO_CODEX_DEMO_OPENAI_API_KEY")
    return {"OPENAI_API_KEY": api_key} if api_key else None


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"codex will run as {out.strip()}")
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


# Exercises the agent feedback channel: rejects a first delivery that doesn't
# meet the bar, nudges the agent, accepts the corrected re-delivery.
_on_deliverable = make_feedback_on_deliverable("codex-demo")


async def _after_execute(hook_ctx: HookContext) -> None:
    try:
        log = await hook_ctx.read_text_from_host("optio.log")
    except FileNotFoundError:
        hook_ctx.report_progress(None, "session log: not present")
        return
    lines = log.splitlines()
    counts = {"STATUS": 0, "DELIVERABLE": 0, "DONE": 0, "ERROR": 0}
    for line in lines:
        for keyword in counts:
            if line.startswith(keyword):
                counts[keyword] += 1
                break
    summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
    hook_ctx.report_progress(
        None,
        f"session log: {len(lines)} lines ({summary or 'no keywords'})",
    )


def _make_on_seed_saved(db, prefix: str, fw):
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]

    async def _on_seed_saved(seed_id: str, info: str | None = None) -> None:
        # info: account summary from the seeded identity (or None).
        print(f"[codex-demo] seed saved {seed_id}: {info}")
        # Cosmetic numbering; a concurrent-save race may reuse a number —
        # acceptable, the seedId is the real key.
        count = await coll.count_documents({})
        name = f"Config #{count + 1}"
        await coll.insert_one({
            "seedId": seed_id,
            "name": name,
            "createdAt": datetime.now(timezone.utc),
        })
        # Regenerate the task list so seed-pinned demo tasks appear.
        await fw.resync()

    return _on_seed_saved


async def _seed_name_map(db, prefix: str) -> dict[str, str]:
    """seedId -> friendly display name, from the demo sidecar collection."""
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]
    out: dict[str, str] = {}
    async for rec in coll.find({}, projection={"seedId": 1, "name": 1}):
        if rec.get("seedId") and rec.get("name"):
            out[rec["seedId"]] = rec["name"]
    return out


async def get_tasks(services: dict) -> list[TaskInstance]:
    db = services["db"]
    prefix = services["prefix"]
    fw = services["optio"]
    ssh = _resolve_ssh_config()

    tasks: list[TaskInstance] = [
        # The seed setup task: vanilla (no seed_id), on_seed_saved wired.
        create_codex_task(
            process_id="codex-seed-setup",
            name="Setup Codex seed",
            description=(
                "One-time: log into Codex interactively (`codex login "
                "--device-auth`, or `codex login --with-api-key` with "
                "OPTIO_CODEX_DEMO_OPENAI_API_KEY exported), then stop the "
                "task to capture a reusable seed. New seed-pinned demo "
                "tasks appear afterward."
            ),
            config=CodexTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                ssh=ssh,
                env=_setup_env(),
                on_seed_saved=_make_on_seed_saved(db, prefix, fw),
            ),
        ),
    ]

    # One seed-pinned demo task per recorded seed, gated on the real codex
    # seed store (mirrors the wrapper). Friendly names come from the
    # sidecar. (The seed-pinned CONVERSATION demo joins at Stage 6/Plan D.)
    names = await _seed_name_map(db, prefix)
    for rec in await list_seeds(fw.mongo_store):
        seed_id = rec["seedId"]
        name = names.get(seed_id, seed_id)
        tasks.append(
            create_codex_task(
                process_id=f"codex-demo-seed-{seed_id}",
                name=f"Codex demo — {name}",
                description=(
                    "Fresh Codex session started from a captured seed "
                    f"({name}): logged-in and configured, new "
                    "conversation. Reads context.txt, ships a "
                    "deliverable, exercises the feedback channel."
                ),
                config=CodexTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    # Kick the agent off unattended (reads AGENTS.md +
                    # executes) — the CodexTaskConfig default, spelled out.
                    auto_start=True,
                ),
            )
        )

    return tasks
```

**Adaptation notes (verify against reality, adjust the code above accordingly):**
- `make_feedback_on_deliverable("codex-demo")` — copy the exact call shape from `grok.py`/Plan A's file; if Plan A used `make_feedback_on_deliverable("codex")`, keep that spelling.
- If Plan B has landed, add `supports_resume=False` to the seed-setup config and `supports_resume=True` to the seed-pinned config (grok's exact split); if not, omit both (the field doesn't exist yet).
- If the Plan A demo's aggregation line in `optio_demo/tasks/__init__.py` is present, it needs no change (the module still exposes `get_tasks`). If Plan A has not landed its Task 11 yet, add the aggregation exactly as Plan A specifies.
- The seed-setup task deliberately leaves `auto_start` at its default: the SEED_SETUP_PROMPT instructs the agent to stay idle, matching the grok demo's behavior.

- [ ] **Step 3: Import smoke + suite**

Run: `.venv/bin/python -c "from optio_demo.tasks import get_task_definitions; print('ok')"`
Expected: `ok` (if `optio_demo` is not installed in this venv: `.venv/bin/pip install -e packages/optio-demo` first).
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` — all pass.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/codex.py packages/optio-demo/src/optio_demo/tasks/__init__.py
git commit -m "feat(optio-demo): codex seed-setup + seed-pinned iframe demo tasks

Replaces the Stage-0 plain iframe demo with the seed lifecycle: log in
once in the ttyd TUI (device-auth or --with-api-key), stop to capture;
seed-pinned tasks auto-appear via fw.resync() with friendly names from
the {prefix}_demo_codex_seeds sidecar. Conversation leg lands with
Stage 6 (Plan D)."
```

---

### Task 10: README truth-up + final verification sweep

**Files:**
- Modify: `packages/optio-codex/README.md`

- [ ] **Step 1: Update the README**

Update the sections Plan A wrote (keep its structure):
- **Authentication**: replace the Stage-0 story with seeds as the primary mechanism (setup task / `on_seed_saved` capture / `seed_id` consume; workdir pre-trusted automatically), keeping API-key env and interactive login as fallbacks. State the rotation fact: ChatGPT-mode auth carries a single-use rotating refresh token (openai/codex#15410), so seeded sessions run a credential watcher + teardown save-back, pooled seeds take a lease, and `verify_and_refresh_seed` keeps idle seeds alive (8-day proactive refresh window).
- **Binary provisioning**: the optio-owned cache (`OPTIO_CODEX_CACHE_DIR` / `${XDG_CACHE_HOME:-~/.cache}/optio-codex/bin`), seeded from a host binary or auto-downloaded (pinned `rust-v0.142.5`, musl); per-task launch symlink preserved.
- **Status**: move "seeds, pool/leases, credential save-back, seed verify/refresh" and "optio-owned binary cache + auto-install" from *missing* to *shipped*; the missing list keeps conversation mode/frontend parity (Plan D) and fs isolation + release (Plan E). If Plan B has landed, reflect its items too; otherwise leave them as-is.

Write the actual prose against the file's current content (it may carry Plan A or Plan A+B state); do not paste blind.

- [ ] **Step 2: Full suite, fresh run**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: 0 failed/errored. Count: Plan A's suite + ~25 new tests from Tasks 1–8.

- [ ] **Step 3: Cross-package sanity**

Run: `.venv/bin/python -m pytest packages/optio-agents/tests/ -q`
Expected: green (nothing in optio-agents was touched; this guards the import surface).

- [ ] **Step 4: Demo import smoke**

Run: `.venv/bin/python -c "from optio_demo.tasks import get_task_definitions; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Branch state review + commit**

Run: `git log --oneline -15` and `git status`
Expected: one commit per task above; clean tree.

```bash
git add packages/optio-codex/README.md
git commit -m "docs(optio-codex): README truth-up for seeds/leases/verify + binary cache"
```

---

## Self-Review (performed while writing)

1. **Scope coverage against the assignment:**
   - Stage 3: manifests + suffix `_codex_seeds` + `home_subdir="home"` → Task 1; workdir pre-trust as post-merge idempotent host-side edit (explicitly NOT a manifest transform, minimal append-if-absent because codex rewrites config.toml) → Task 2 + wired in Task 4; seed CRUD store-binding wrappers → Task 1; `seed_id`/`SeedProvider`/`on_seed_saved` config fields → Task 4; capture gated on reached-live + `capture_gate_ok` → Task 4 (negative test included); seed-setup + seed-pinned iframe demo with sidecar `{prefix}_demo_codex_seeds` + `fw.resync()` → Task 9 (conversation demo explicitly deferred to Plan D).
   - Stage 4: cred_watcher port with codex path + sha256 fingerprint gated on `tokens`/`OPENAI_API_KEY` non-null, 10s tick, CRED-manifest save-back + `renew_lease`, lease loss → cancellation flag → Task 3; teardown ordering (watcher-cancel → backstop → release) with rationale comments → Task 5; verify.py port (throwaway taskdir, `plant_seed`, `codex exec --json -s read-only --skip-git-repo-check`, CODEX_HOME isolation env, stdout-only verdict, validity-gated `overwrite_seed_member`, `declare_metadata` + `mark_seed_status`) → Task 6; the "Stage 4 is MANDATORY" design note (single-use rotation, openai/codex#15410, 8-day refresh) is pinned in the plan header, cred_watcher + verify docstrings, and two commit messages.
   - Stage 5: host-side cache-dir resolution with the exact `${OPTIO_CODEX_CACHE_DIR:-…}` default (grok :62-93 pattern) → Task 7; resolution order cache-hit → `cp -L` host seed → real download → error → Tasks 7+8; pinned version constant, uname-derived musl triple, `hook_ctx.download_file` + tar.gz extraction → Task 8; per-task `<workdir>/home/.local/bin/codex` symlink launch path preserved (kill-scoping) with tests asserting `realpath` into the cache → Tasks 7+8; cache tests ported from `test_grok_cache.py` + the new download-path test with a fake tarball → Tasks 7+8.
   - Fake agent: probe mode (alive/dead/echo/alive_badexit) → Task 6; seed scenarios modelling CODEX_HOME auth.json rotation (`seed`, `seed_rotate`) → Tasks 4+5; `FAKE_CODEX_RECORD` argv+config log outside the workdir → Task 4 (and it double-serves as the pre-trust launch-time proof).
2. **Placeholder scan:** every code step carries complete code. The three deliberate verify-against-reality notes (Task 2 `write_text` parent-dir behavior; Task 9 `_feedback` call shape + `supports_resume` presence + aggregation state; Task 10 README prose against the live file) each name the file to read and the exact decision to make — none ask the executor to invent behavior.
3. **Type consistency:** `cred_fingerprint`/`capture_gate_ok` take a `Host` and return `str | None`/`bool` — consumed identically in session (Tasks 4/5) and tests; `save_back_if_changed` returns the new baseline and every caller reassigns it; `run_codex_probe` returns `(stdout, exit_code)` consumed by verify only; `ensure_codex_installed` keeps its `(hook_ctx, *, install_if_missing, install_dir) -> str` signature across Tasks 7/8 (only the internal resolution changes), so `session._prepare` needs no edit in Stage 5; `SeedProvider = Callable[[str], Awaitable[str]]` matches the provider used in `test_session_lease.py` and the `callable(config.seed_id)` branch.
4. **Plan A/B coupling handled, not assumed:** Task 0 records which baseline landed; every session diff anchors on stable structures (the `finally` block's `teardown_session_tree` / snapshot / `cleanup_taskdir` markers) rather than line numbers; `resuming` has an explicit Plan-B-absent fallback (`False` + integration comment). The Plan B doc was re-checked at finalization: its config additions (`supports_resume`, `workdir_exclude` — Plan B Task 5) are disjoint from this plan's (`seed_id`, `on_seed_saved`), its `build_auto_start_args(resuming=…)` change does not collide with any Task here, and its snapshot-capture teardown block slots AFTER this plan's seed capture per grok's ordering (Task 4 Step 5 spells the placement out for both baselines).
5. **Test-isolation traps carried over from grok:** the lease/session tests use the `task_root` fixture (short `/tmp/cxtr-*` paths — tmux `sun_path` limit); `FAKE_CODEX_RECORD` lives in `tmp_path`, outside the wiped workdir; the watcher unit tests shrink `CRED_WATCH_INTERVAL_S` via monkeypatch instead of waiting 10s; the lease session test relies on the teardown backstop, not watcher timing, so it is not flaky under load.
6. **SSOT audit:** no engine code copied (all seed/lease ops call `optio_agents.seeds`); the ttyd installer's `_resolve_install_dir` is deliberately untouched (separate resolver, per grok's precedent); `resolve_codex` remains the single host-resolution primitive shared by ensure/verify; `_isolation_env` remains the single identity map (probe env derives from it).






