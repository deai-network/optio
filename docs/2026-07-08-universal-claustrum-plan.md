# Universal Claustrum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make claustrum (Landlock, fail-closed) the filesystem-isolation guarantee on all 7 agent wrappers, hoist the claustrum config triad + wrap/notice plumbing into shared `optio_agents`, and remove the untrusted vendor-native fs sandboxes.

**Architecture:** A shared `ClaustrumConfigMixin` (frozen dataclass) supplies the `fs_isolation`/`extra_allowed_dirs`/`delivery_type` triad + validation to every `TaskConfig` via inheritance (flat API, zero caller churn). Shared `optio_agents.claustrum` gains `build_claustrum_wrap` (argv-prefix builder) and `emit_claustrum_update_notice`; shared `optio_agents.fs_grants` holds the common baseline grant set + `build_grant_flags`. Each engine session calls the shared helpers; grok's custom Landlock profile is deleted, codex's native sandbox is demoted to network-only, and opencode is wrapped for the first time.

**Tech Stack:** Python 3.11+ frozen dataclasses, `optio_agents.claustrum` (Landlock via the `claustrum` binary), pytest + pytest-xdist (two-phase `make test`).

## Global Constraints

- **`delivery_type` is MANDATORY when `fs_isolation` is on** — raise `ValueError` at config construction. Security feature; a new claustrum release may patch a vulnerability, so the operator must be notified ASAP. Applies to all 7 engines verbatim.
- **Claustrum is fail-closed:** if the kernel cannot apply Landlock, the task refuses to launch — never run unconfined.
- **Vendor native sandbox is NOT trusted for fs.** Retained only for a non-fs capability Landlock cannot provide (codex `network_access`).
- **Zero caller churn:** the triad fields stay top-level on every config (inherited, not nested). `fs_isolation=` / `delivery_type=` keep working verbatim.
- **`.venv` inside the worktree/branch**; never `pip install -e` against global Python ([[venv_in_worktree]]). pytest-xdist must be installed (`make test` phase 1 needs `-n`).
- **Real-binary verification** for the 3 newly-wrapped engines (grok/codex/opencode) — build/verify against the real installed binaries, never fakes.
- **Two-phase tests:** `make test` runs `-m "not serial"` in parallel then `-m serial`. Serial tests exist — always run the full `make test` (or both phases), not just the parallel phase.

---

## Phase 1 — Shared foundation (`optio_agents`)

### Task 1: Shared baseline grant set + `build_grant_flags`

The system-dir baseline (`/usr`, `/bin`, `/etc`, …) and the grant-flag builder are
duplicated in every engine's `fs_allowlist.py`. Lift them into a new shared module.

**Files:**
- Create: `packages/optio-agents/src/optio_agents/fs_grants.py`
- Test: `packages/optio-agents/tests/test_fs_grants.py`

**Interfaces:**
- Produces:
  ```python
  BASELINE: tuple[tuple[str, str], ...]   # ordered (flag, path) system grants
  def build_grant_flags(
      *, workdir: str, engine_cache_dir: str,
      extra_allowed_dirs: "list[AllowedDir] | None" = None,
      host_home: str | None = None,
      extra_baseline: "list[tuple[str, str]] | None" = None,
  ) -> list[str]: ...
  ```
  Order: `BASELINE` (+ `extra_baseline`) → `--rwx <workdir>` → `--rox <engine_cache_dir>` → per `extra_allowed_dirs` `--<mode> <path>` (with `~`/`~/` expanded against `host_home`). `AllowedDir.mode` maps `ro/rw/rox/rwx` → `--ro/--rw/--rox/--rwx`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents/tests/test_fs_grants.py
from optio_agents.config_types import AllowedDir
from optio_agents import fs_grants


def test_baseline_then_workdir_then_cache_then_extras():
    flags = fs_grants.build_grant_flags(
        workdir="/wd/", engine_cache_dir="/cache/",
        extra_allowed_dirs=[AllowedDir("/data", "ro")],
    )
    # system baseline present
    assert "--rox" in flags and "/usr" in flags
    # workdir rwx, cache rox, extra ro — in order, trailing
    assert flags[-6:] == ["--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]


def test_home_tilde_expands_against_host_home():
    flags = fs_grants.build_grant_flags(
        workdir="/wd", engine_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir("~/x", "rw")], host_home="/home/u",
    )
    assert flags[-2:] == ["--rw", "/home/u/x"]


def test_extra_baseline_appended_to_system_baseline():
    flags = fs_grants.build_grant_flags(
        workdir="/wd", engine_cache_dir="/cache",
        extra_baseline=[("--ro", "/opt/opencode")],
    )
    assert "/opt/opencode" in flags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_fs_grants.py -q`
Expected: FAIL (`ModuleNotFoundError: optio_agents.fs_grants`).

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-agents/src/optio_agents/fs_grants.py
"""Shared claustrum filesystem-allowlist flags for every wrapper launch.

Lifted from the (previously duplicated) per-wrapper fs_allowlist.py. The system
baseline is engine-neutral; the workdir + engine binary cache + caller extras are
appended per launch."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config_types import AllowedDir

# Ordered (flag, path) system baseline. --rox = read+execute (binaries/libs),
# --ro = read-only, --rw = read-write.
BASELINE: tuple[tuple[str, str], ...] = (
    ("--rox", "/usr"), ("--rox", "/bin"), ("--rox", "/sbin"),
    ("--rox", "/lib"), ("--rox", "/lib64"), ("--rox", "/lib32"),
    ("--ro", "/etc"), ("--ro", "/etc/ssl"), ("--ro", "/etc/resolv.conf"),
    ("--ro", "/proc"),
    ("--rw", "/dev/null"), ("--rw", "/dev/zero"),
    ("--ro", "/dev/urandom"), ("--ro", "/dev/random"),
    ("--rw", "/dev/tty"), ("--rw", "/dev/pts"), ("--rw", "/dev/ptmx"),
)

_MODE_FLAG = {"ro": "--ro", "rw": "--rw", "rox": "--rox", "rwx": "--rwx"}


def build_grant_flags(
    *,
    workdir: str,
    engine_cache_dir: str,
    extra_allowed_dirs: "list[AllowedDir] | None" = None,
    host_home: str | None = None,
    extra_baseline: "list[tuple[str, str]] | None" = None,
) -> list[str]:
    """Return the ordered claustrum grant flags for a launch.

    ``workdir`` (the per-task tree incl. the isolated home) is granted rwx.
    ``engine_cache_dir`` (where the real agent binary lives, outside the workdir)
    is granted read+exec. ``extra_baseline`` lets an engine add its own always-on
    dirs (e.g. opencode's config tree). Caller ``~``/``~/`` extras expand against
    ``host_home`` (the REAL host home)."""
    flags: list[str] = []
    for flag, path in (*BASELINE, *(extra_baseline or [])):
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", engine_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        path = ad.path
        if host_home and (path == "~" or path.startswith("~/")):
            path = host_home.rstrip("/") + path[1:]
        flags += [_MODE_FLAG[ad.mode], path.rstrip("/")]
    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_fs_grants.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/fs_grants.py packages/optio-agents/tests/test_fs_grants.py
git commit -m "feat(optio-agents): shared claustrum baseline grant set + build_grant_flags"
```

### Task 2: `ClaustrumConfigMixin` + shared validation

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/config_types.py`
- Test: `packages/optio-agents/tests/test_claustrum_mixin.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ClaustrumConfigMixin:
      fs_isolation: bool = True
      extra_allowed_dirs: list[AllowedDir] | None = None
      delivery_type: str | None = None
      def _validate_claustrum(self) -> None: ...   # raises when fs_isolation and not delivery_type
  ```
- Consumes: `AllowedDir` (same module).
- Every engine `TaskConfig` will inherit this (Phase 2) and call `self._validate_claustrum()` from its `__post_init__`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents/tests/test_claustrum_mixin.py
import dataclasses
import pytest
from optio_agents.config_types import ClaustrumConfigMixin, AllowedDir


@dataclasses.dataclass(frozen=True)
class _Cfg(ClaustrumConfigMixin):
    name: str = "x"

    def __post_init__(self):
        self._validate_claustrum()


def test_triad_fields_present_and_defaulted():
    c = _Cfg()
    assert c.fs_isolation is True
    assert c.extra_allowed_dirs is None
    assert c.delivery_type is None  # default; but _Cfg validates -> see below


def test_fs_isolation_on_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        _Cfg(fs_isolation=True, delivery_type=None)


def test_fs_isolation_off_allows_missing_delivery_type():
    c = _Cfg(fs_isolation=False)
    assert c.delivery_type is None


def test_delivery_type_satisfies_the_rule():
    c = _Cfg(fs_isolation=True, delivery_type="audit")
    assert c.delivery_type == "audit"
```

> Note: `test_triad_fields_present_and_defaulted` constructs `_Cfg()` which
> defaults `fs_isolation=True`, `delivery_type=None` → the validator raises.
> Replace that first test body with `_Cfg(fs_isolation=False)` when writing, or
> drop it; the remaining three cover the contract. (Fix inline during Step 1.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_claustrum_mixin.py -q`
Expected: FAIL (`ImportError: cannot import name 'ClaustrumConfigMixin'`).

- [ ] **Step 3: Write minimal implementation**

Add to `config_types.py` (after `AllowedDir`):

```python
@dataclass(frozen=True)
class ClaustrumConfigMixin:
    """The claustrum filesystem-isolation triad, shared by every engine
    TaskConfig via inheritance. Fields stay top-level on each config (no nesting)
    so callers write ``fs_isolation=`` / ``delivery_type=`` verbatim.

    Claustrum (Landlock, fail-closed) is the trusted fs-isolation layer on every
    engine. ``delivery_type`` names a subdir under ``<workdir>/deliverables/``
    used to route the "a newer claustrum release is available" notice through
    ``on_deliverable`` — MANDATORY when ``fs_isolation`` is on, because a new
    release may patch a vulnerability the operator must hear about immediately."""
    fs_isolation: bool = True
    extra_allowed_dirs: list[AllowedDir] | None = None
    delivery_type: str | None = None

    def _validate_claustrum(self) -> None:
        """Raise if the claustrum triad is inconsistent. Call from each engine
        config's ``__post_init__``."""
        if self.fs_isolation and not (self.delivery_type and self.delivery_type.strip()):
            raise ValueError(
                f"{type(self).__name__}: fs_isolation is on (default) but "
                "delivery_type is unset. Set delivery_type=<subdir> (routes the "
                "'newer claustrum available' security notice via on_deliverable), "
                "or set fs_isolation=False to opt out."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw", "rox", "rwx"):
                raise ValueError(
                    f"{type(self).__name__}.extra_allowed_dirs: mode={ad.mode!r} "
                    "must be ro/rw/rox/rwx."
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_claustrum_mixin.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/config_types.py packages/optio-agents/tests/test_claustrum_mixin.py
git commit -m "feat(optio-agents): ClaustrumConfigMixin (fs-isolation triad + shared validation)"
```

### Task 3: Shared `build_claustrum_wrap` + `emit_claustrum_update_notice`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/claustrum.py`
- Test: `packages/optio-agents/tests/test_claustrum_wrap_notice.py`

**Interfaces:**
- Produces:
  ```python
  def build_claustrum_wrap(claustrum_path: str, grants: list[str]) -> list[str]:
      # -> [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]
  async def emit_claustrum_update_notice(
      host, hook_ctx, *, delivery_type: str, on_deliverable, newer: str, pinned: str,
  ) -> None: ...
  ```
- Consumes: `fs_grants.build_grant_flags` output (the `grants` list); `host.write_text`, `host.run_command`, `host.workdir`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents/tests/test_claustrum_wrap_notice.py
import pytest
from optio_agents import claustrum


def test_build_claustrum_wrap_shape():
    argv = claustrum.build_claustrum_wrap("/c/claustrum", ["--rwx", "/wd"])
    assert argv == ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"
        self.written = []
        self.ran = []
    async def write_text(self, rel, text):
        self.written.append((rel, text))
    async def run_command(self, cmd):
        self.ran.append(cmd)
        class R:  # minimal
            exit_code = 0
            stdout = ""
            stderr = ""
        return R()


@pytest.mark.asyncio
async def test_emit_notice_writes_calls_and_cleans_up():
    host = _FakeHost()
    seen = {}
    async def on_deliverable(ctx, rel, text):
        seen["rel"] = rel
        seen["text"] = text
    await claustrum.emit_claustrum_update_notice(
        host, object(), delivery_type="audit",
        on_deliverable=on_deliverable, newer="2.0.0", pinned="1.0.0",
    )
    assert seen["rel"] == "audit/claustrum-update-2.0.0.md"
    assert "2.0.0" in seen["text"] and "1.0.0" in seen["text"]
    assert host.written and host.written[0][0] == "deliverables/audit/claustrum-update-2.0.0.md"
    # cleanup removed the notice file
    assert any("rm -f" in c and "audit/claustrum-update-2.0.0.md" in c for c in host.ran)


@pytest.mark.asyncio
async def test_emit_notice_noop_without_callback():
    host = _FakeHost()
    await claustrum.emit_claustrum_update_notice(
        host, object(), delivery_type="audit",
        on_deliverable=None, newer="2.0.0", pinned="1.0.0",
    )
    assert not host.written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_claustrum_wrap_notice.py -q`
Expected: FAIL (`AttributeError: module 'optio_agents.claustrum' has no attribute 'build_claustrum_wrap'`).

- [ ] **Step 3: Write minimal implementation**

Append to `claustrum.py`:

```python
import shlex


def build_claustrum_wrap(claustrum_path: str, grants: list[str]) -> list[str]:
    """The claustrum argv prefix that Landlock-confines the launched process
    tree. ``grants`` come from :func:`optio_agents.fs_grants.build_grant_flags`."""
    return [claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--"]


async def emit_claustrum_update_notice(
    host, hook_ctx, *, delivery_type: str, on_deliverable, newer: str, pinned: str,
) -> None:
    """Route the 'a newer claustrum release is available' notice through
    ``on_deliverable``, then remove the notice file (clean slate for the real
    agent). No-op when ``on_deliverable`` is None or ``newer`` is falsy."""
    if on_deliverable is None or not newer:
        return
    rel = f"{delivery_type}/claustrum-update-{newer}.md"
    text = (
        f"A newer claustrum release ({newer}) is available; the pinned version "
        f"is {pinned}. Audit it and consider bumping the pin."
    )
    await host.write_text(f"deliverables/{rel}", text)
    try:
        await on_deliverable(hook_ctx, rel, text)
    finally:
        await host.run_command(
            f"rm -f {shlex.quote(host.workdir.rstrip('/') + '/deliverables/' + rel)}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest tests/test_claustrum_wrap_notice.py -q`
Expected: PASS. (If `pytest.mark.asyncio` needs a plugin, match the repo's existing async-test convention — check a neighboring `optio-agents` async test for the marker/anyio setup.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/claustrum.py packages/optio-agents/tests/test_claustrum_wrap_notice.py
git commit -m "feat(optio-agents): shared build_claustrum_wrap + emit_claustrum_update_notice"
```

---

## Phase 2 — Per-engine adoption

Every engine task shares a **common config change** (call it the *mixin swap*):

> **Mixin swap (identical shape, per engine — substitute the class name):**
> 1. In `types.py`, import the mixin: `from optio_agents.config_types import ClaustrumConfigMixin` (extend the existing `optio_agents` import).
> 2. Change the class decl to inherit it: `class <Engine>TaskConfig(ClaustrumConfigMixin):` (frozen dataclass — the base is also `@dataclass(frozen=True)`).
> 3. **Delete** the local `fs_isolation` / `extra_allowed_dirs` field declarations (now inherited). **Delete** any local `delivery_type` (claudecode only). **Delete** the local `extra_allowed_dirs` mode-check loop in `__post_init__` (now in `_validate_claustrum`).
> 4. Add `self._validate_claustrum()` as the FIRST line of `__post_init__` (before other checks, so a missing `delivery_type` fails fast).
> 5. Reword the fs-isolation field docstrings native→claustrum where they described a vendor sandbox.

Because the base fields default and all engine fields default, inheritance is safe. Callers keep writing `fs_isolation=` / `delivery_type=` verbatim.

### Task 4: claudecode onto the shared helpers (reference adoption)

Proves the Phase-1 helpers against the engine they were extracted from.

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py` (mixin swap)
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py:112-132` (`_build_claustrum_wrap` → shared), `:317-333` (notice → shared)
- Modify: `packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py` (drop `_BASELINE` + `build_grant_flags`; re-export from shared or delete + update import)
- Test: existing `packages/optio-claudecode/tests/` claustrum tests

**Interfaces:**
- Consumes: `optio_agents.fs_grants.build_grant_flags`, `optio_agents.claustrum.build_claustrum_wrap`, `optio_agents.claustrum.emit_claustrum_update_notice`.

- [ ] **Step 1: Write/adjust the failing test.** In the claudecode test that asserts the wrap argv, keep asserting the shape `["<claustrum>", "--best-effort", "--abi-min", "1", *grants, "--"]`. Add/keep a test that the update notice routes through `on_deliverable` with rel `"<delivery_type>/claustrum-update-<newer>.md"`. Run to confirm current local impl passes (baseline), then refactor.

- [ ] **Step 2: Mixin swap** in `types.py` per the shared pattern above. The existing claudecode `delivery_type` field + its mandatory-when-`fs_isolation` raise (`types.py:368-375`) are **removed** from claudecode and now come from `_validate_claustrum`.

- [ ] **Step 3: Replace `_build_claustrum_wrap`** (`session.py:112-132`) body with:

```python
async def _build_claustrum_wrap(
    host: Host, config: ClaudeCodeTaskConfig, claustrum_path: str | None,
) -> list[str] | None:
    if not config.fs_isolation:
        return None
    from optio_agents import fs_grants
    cache_dir = await host_actions._resolve_cache_dir(host, config.install_dir)
    host_home = await host.resolve_host_home() if config.extra_allowed_dirs else None
    grants = fs_grants.build_grant_flags(
        workdir=host.workdir, engine_cache_dir=cache_dir,
        extra_allowed_dirs=config.extra_allowed_dirs, host_home=host_home,
    )
    return host_actions.claustrum.build_claustrum_wrap(claustrum_path, grants)
```

- [ ] **Step 4: Replace the notice block** (`session.py:317-333`) with:

```python
        if config.fs_isolation and claustrum_newer:
            await host_actions.claustrum.emit_claustrum_update_notice(
                host, hook_ctx,
                delivery_type=config.delivery_type,
                on_deliverable=config.on_deliverable,
                newer=claustrum_newer,
                pinned=host_actions.claustrum.CLAUSTRUM_PINNED_TAG,
            )
```

- [ ] **Step 5: Drop the duplicated grant machinery** in `fs_allowlist.py` — delete `_BASELINE` + `build_grant_flags` (now in `optio_agents.fs_grants`). If anything else in the module remains useful keep it; otherwise delete the file and remove its import (`session.py:119`, now `from optio_agents import fs_grants`).

- [ ] **Step 6: Run claudecode suite**

Run: `cd packages/optio-claudecode && ../../.venv/bin/python -m pytest -q`
Expected: PASS (all existing claustrum + conversation tests green).

- [ ] **Step 7: Commit**

```bash
git add packages/optio-claudecode
git commit -m "refactor(optio-claudecode): adopt shared claustrum mixin + wrap + notice"
```

### Task 5: cursor — shared helpers + ADD update notice

cursor already wraps in claustrum but **never surfaces the update notice**.

**Files:**
- Modify: `packages/optio-cursor/src/optio_cursor/types.py` (mixin swap; drop the `extra_allowed_dirs` loop at `types.py:315-318`)
- Modify: `packages/optio-cursor/src/optio_cursor/session.py:91-117` (`_build_claustrum_wrap` → shared `fs_grants` + `claustrum.build_claustrum_wrap`), and its provisioning block (`session.py:271-276`) — add `claustrum_newer` capture + call `claustrum.emit_claustrum_update_notice` in the body (cursor has NO notice today; add it beside the launch, mirroring Task 4 Step 4).
- Modify: `packages/optio-cursor/src/optio_cursor/fs_allowlist.py` (drop `_BASELINE` + `build_grant_flags`)

- [ ] **Step 1:** Mixin swap in `types.py`. Add `delivery_type` awareness — cursor gains the mandatory-`delivery_type` rule via `_validate_claustrum`. Update cursor demo/tests that build a `CursorTaskConfig` with `fs_isolation=True` to also pass `delivery_type=` (else they now raise).
- [ ] **Step 2:** Rewrite `_build_claustrum_wrap` to call `fs_grants.build_grant_flags(workdir=…, engine_cache_dir=<cursor cache>, extra_allowed_dirs=…, host_home=…)` then `claustrum.build_claustrum_wrap(...)`.
- [ ] **Step 3:** Capture `claustrum_newer` where cursor provisions claustrum (add `claustrum_newer = await host_actions.claustrum_newer_tag()` beside `ensure_claustrum_installed`; add the shim if cursor lacks `claustrum_newer_tag`), then call `emit_claustrum_update_notice` in the session body when `fs_isolation and claustrum_newer`.
- [ ] **Step 4:** Delete `fs_allowlist.py` `_BASELINE`/`build_grant_flags`.
- [ ] **Step 5:** `cd packages/optio-cursor && ../../.venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 6:** Commit `refactor(optio-cursor): shared claustrum mixin + wrap + add update notice`.

### Task 6: kimicode — shared helpers + ADD update notice

Same shape as Task 5. kimicode's `_build_claustrum_wrap` lives in `host_actions.py:784`; its `build_wrapped_exec_cmd` (`host_actions.py:741-750`) already threads `claustrum_wrap`. kimicode has NO `ttyd_install_dir` (irrelevant). No update notice today — add it.

**Files:**
- Modify: `packages/optio-kimicode/src/optio_kimicode/types.py` (mixin swap)
- Modify: `packages/optio-kimicode/src/optio_kimicode/host_actions.py:784` (`_build_claustrum_wrap` → shared grants+wrap) and the provisioning site; add `claustrum.emit_claustrum_update_notice` call in `session.py`
- Modify: `packages/optio-kimicode/src/optio_kimicode/fs_allowlist.py` (drop duplicated grant machinery)

- [ ] **Step 1:** Mixin swap; update kimicode demo/tests to pass `delivery_type=` when `fs_isolation=True`.
- [ ] **Step 2:** Shared grants + `claustrum.build_claustrum_wrap` in `_build_claustrum_wrap`.
- [ ] **Step 3:** Add `claustrum_newer` capture + `emit_claustrum_update_notice` call.
- [ ] **Step 4:** Drop `fs_allowlist.py` grant dup.
- [ ] **Step 5:** `cd packages/optio-kimicode && ../../.venv/bin/python -m pytest -q` → PASS (the cred-watcher xdist flake is known; re-run isolated if it appears).
- [ ] **Step 6:** Commit `refactor(optio-kimicode): shared claustrum mixin + wrap + add update notice`.

### Task 7: antigravity — shared helpers + ADD update notice

antigravity wraps via `host_actions._build_claustrum_wrap` (`session.py:313, 427`). No notice today. Same shape.

**Files:**
- Modify: `packages/optio-antigravity/src/optio_antigravity/types.py` (mixin swap)
- Modify: `packages/optio-antigravity/src/optio_antigravity/host_actions.py` (`_build_claustrum_wrap` → shared) + `session.py` (add notice at the two launch paths' shared prelude)
- Modify: `packages/optio-antigravity/src/optio_antigravity/fs_allowlist.py` (drop grant dup)

- [ ] **Step 1:** Mixin swap; update antigravity demo/tests to pass `delivery_type=`.
- [ ] **Step 2:** Shared grants + wrap.
- [ ] **Step 3:** Add `claustrum_newer` capture + `emit_claustrum_update_notice`.
- [ ] **Step 4:** Drop `fs_allowlist.py` grant dup.
- [ ] **Step 5:** `cd packages/optio-antigravity && ../../.venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 6:** Commit `refactor(optio-antigravity): shared claustrum mixin + wrap + add update notice`.

### Task 8: grok — RIP native custom profile, wire claustrum

grok today uses a native `--sandbox optio` custom Landlock profile. Replace with claustrum.

**Files:**
- **Delete:** `packages/optio-grok/src/optio_grok/fs_allowlist.py` (the whole native-profile builder: `_BASELINE_READ_WRITE`, `_expand_home`, `_toml_str_array`, `build_sandbox_toml`).
- Modify: `packages/optio-grok/src/optio_grok/session.py` — remove import (`:40`) + the profile-plant block (`:250-266`); add `from optio_agents import claustrum` provisioning in `_prepare` + a `_build_claustrum_wrap`; thread the wrap into both launch paths (iframe: `launch_ttyd_with_grok`/`_build_grok_shell_command`; conversation: `build_conversation_argv`).
- Modify: `packages/optio-grok/src/optio_grok/host_actions.py` — add `ensure_claustrum_installed` + `claustrum_newer_tag` shims; **remove** `SANDBOX_PROFILE_NAME` (`:681`), the `--sandbox` appends (`build_grok_flags:726-727`, `build_conversation_argv:836-837`), the `fs_isolation` bool params on those builders, and the ctty-wrap machinery `_CTTY_WRAP_PYTHON`/`_CTTY_WRAP_HELPER` (`:767-797`, `:848-849`) — claustrum does not open `/dev/tty`. Keep `write_grok_config`, `_isolation_env`, `_resolve_grok_cache_dir`.
- Modify: `packages/optio-grok/src/optio_grok/types.py` — mixin swap (grok GAINS `delivery_type`; reword the native-sandbox docstring at `:242-254`).

**Interfaces:**
- grok baseline grants: `--rwx <workdir>` (covers the whole `<workdir>/home` GROK_HOME/XDG tree), `--rox <grok_cache_dir>` (the binary cache the `<workdir>/home/.local/bin/grok` symlink targets — grok can't exec itself without it), plus system baseline + extras.

- [ ] **Step 1: Write the failing test.** Add `packages/optio-grok/tests/test_claustrum.py` asserting: (a) `_build_claustrum_wrap` returns the shared shape with a `--rwx <workdir>` and `--rox <grok cache>` grant; (b) `build_conversation_argv` no longer emits `--sandbox` and no ctty wrap; (c) `GrokTaskConfig(fs_isolation=True)` raises without `delivery_type`. Run → FAIL.
- [ ] **Step 2:** Mixin swap in `types.py`.
- [ ] **Step 3:** Add the `ensure_claustrum_installed` + `claustrum_newer_tag` shims to `host_actions.py` (mirror cursor `host_actions.py:124-140`).
- [ ] **Step 4:** Add `_build_claustrum_wrap(host, config, claustrum_path)` to `session.py` using `fs_grants.build_grant_flags(workdir=host.workdir, engine_cache_dir=<grok cache>, extra_allowed_dirs=…, host_home=…)` + `claustrum.build_claustrum_wrap`. Provision `claustrum_path`/`claustrum_newer` in `_prepare` (replacing the deleted plant block).
- [ ] **Step 5:** Thread the wrap: change `_build_grok_shell_command` to splice `[*claustrum_wrap, grok_path, *grok_flags]`; change `build_conversation_argv` to prepend the wrap (drop the ctty wrap + `--sandbox`). Remove the now-unused `fs_isolation` bool params from the builders.
- [ ] **Step 6:** Add the `emit_claustrum_update_notice` call in the body when `fs_isolation and claustrum_newer`.
- [ ] **Step 7:** Delete `fs_allowlist.py`; remove `SANDBOX_PROFILE_NAME` + ctty machinery. Update grok demo/tests to pass `delivery_type=`.
- [ ] **Step 8:** `cd packages/optio-grok && ../../.venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 9:** Commit `feat(optio-grok): replace native --sandbox profile with claustrum fs-isolation`.

### Task 9: codex — ADD claustrum, demote native sandbox to network-only

codex today relies on its native bubblewrap/Landlock sandbox for fs. Add claustrum as the fs guarantee; keep native `workspace-write` only for `network_access`.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/types.py` — mixin swap + **decouple `effective_sandbox_mode` from `fs_isolation`** + rework the `__post_init__` validators.
- Modify: `packages/optio-codex/src/optio_codex/fs_allowlist.py` — add claustrum `build_grant_flags` + `_BASELINE` (mirroring cursor) ALONGSIDE the existing native `SandboxSettings` SSOT (both coexist: claustrum for fs, native for network). Keep `resolve_sandbox_settings`/`build_sandbox_cli_args`/`build_sandbox_config_overrides` — they now serve network only.
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` — add `ensure_claustrum_installed` + `claustrum_newer_tag` shims.
- Modify: `packages/optio-codex/src/optio_codex/session.py` — provision claustrum in `_prepare`; build the wrap; inject at BOTH seams: iframe `host_actions.py:544` (`[codex_path, *codex_flags]` → `[*wrap, codex_path, *codex_flags]`) and conversation `session.py:377-380` (`argv = [*wrap, codex_path, "app-server", …]`); add the update-notice call.

**New codex config semantics (replace `effective_sandbox_mode` + validators):**

```python
    @property
    def effective_sandbox_mode(self) -> SandboxMode:
        # Claustrum (not the native sandbox) owns fs isolation now. The native
        # mode exists ONLY to carry the network knob: workspace-write keeps
        # network confined per `network_access`; danger-full-access frees it.
        if self.sandbox is not None:
            return self.sandbox
        return "workspace-write"
```

```python
        # fs_isolation drives CLAUSTRUM (see session.py), decoupled from the
        # native mode. Only the network coupling remains native:
        if self.network_access and self.effective_sandbox_mode == "read-only":
            raise ValueError(
                "CodexTaskConfig: network_access=True is a "
                "[sandbox_workspace_write] knob and cannot apply under "
                "sandbox='read-only'."
            )
```

Delete the two `fs_isolation`⇄`danger-full-access` / `read-only` validators (`types.py:343-356`) — fs is no longer the native mode's job. Keep the `rw_extras under read-only` check (extra_allowed_dirs rw grants still feed codex `writable_roots` so the redundant native layer does not block a write claustrum permits). Reword the field docstrings (`types.py:127-146`).

- [ ] **Step 1: Write the failing test.** `packages/optio-codex/tests/test_claustrum.py`: (a) `CodexTaskConfig(fs_isolation=True)` raises without `delivery_type`; (b) `effective_sandbox_mode` is `workspace-write` regardless of `fs_isolation`; (c) `CodexTaskConfig(fs_isolation=False, sandbox="danger-full-access")` NO LONGER raises (was an error); (d) the claustrum wrap builder returns `--rwx <workdir>` + `--rox <codex cache>`. Run → FAIL.
- [ ] **Step 2:** Mixin swap + new `effective_sandbox_mode` + validator rework in `types.py`.
- [ ] **Step 3:** Add claustrum `build_grant_flags` + `_BASELINE` to `fs_allowlist.py`; add the `ensure_claustrum_installed`/`claustrum_newer_tag` shims to `host_actions.py`.
- [ ] **Step 4:** Provision + build wrap in `session.py`; inject at both seams; keep `build_sandbox_cli_args`/overrides for the native mode (network).
- [ ] **Step 5:** Add the `emit_claustrum_update_notice` call.
- [ ] **Step 6:** Update codex demo/tests to pass `delivery_type=` when `fs_isolation=True`; fix any test asserting the old `danger-full-access` incompatibility.
- [ ] **Step 7:** `cd packages/optio-codex && ../../.venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 8:** Commit `feat(optio-codex): claustrum fs-isolation; native sandbox demoted to network-only`.

### Task 10: opencode — wire claustrum for the first time

opencode has NO sandbox today (inert warning). Wrap the `opencode web` server tree.

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/fs_allowlist.py` (a `build_grant_flags` thin over `optio_agents.fs_grants`, adding opencode's `extra_baseline`).
- Modify: `packages/optio-opencode/src/optio_opencode/host_actions.py` — add `ensure_claustrum_installed` + `claustrum_newer_tag` shims; thread a `claustrum_wrap: list[str] | None` param into `launch_opencode` (`:431`) and splice it into the `cmd` string (`:478-482`) between `env …` and `{opencode_executable}` via `shlex.join(wrap)`.
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` — DELETE `_warn_if_fs_isolation_unenforced` (`:109-118`) and its call (`:395`); provision `claustrum_path`/`claustrum_newer` in `_prepare` (beside `ensure_opencode_installed`, `:199`); build the wrap; pass it to `launch_opencode`; add the update-notice call.
- Modify: `packages/optio-opencode/src/optio_opencode/types.py` — mixin swap; rewrite the INERT docstrings (`:197-208`) native→claustrum.

**CRITICAL grant set (agent-verified):** opencode's home/config/data/cache all live under `<workdir>/home` (covered by `--rwx <workdir>`), BUT:
- **`OPENCODE_DB` = `<taskdir>/opencode.db` is ONE LEVEL ABOVE the workdir** (`taskdir/workdir` layout) → grant `--rwx <taskdir>` (or at least the db path). Under-granting here breaks opencode's live DB.
- **opencode binary cache** (`_resolve_install_dir`, outside every workdir) → `--rox <opencode_cache_dir>`.
- plus system `_BASELINE` (tool subprocesses) + `extra_allowed_dirs`.

Implement via `fs_grants.build_grant_flags(workdir=host.workdir, engine_cache_dir=<opencode cache>, extra_allowed_dirs=…, host_home=…, extra_baseline=[("--rwx", host.taskdir.rstrip("/"))])`.

- [ ] **Step 1: Write the failing test.** `packages/optio-opencode/tests/test_claustrum.py`: (a) `OpencodeTaskConfig(fs_isolation=True)` raises without `delivery_type`; (b) the built grant flags include `--rwx <taskdir>` AND `--rox <opencode cache>` AND `--rwx <workdir>`; (c) `launch_opencode`'s command string places the claustrum wrap immediately before the opencode executable and AFTER the `env …`/password assignment (localhost bind + stdout URL scrape must survive). Run → FAIL.
- [ ] **Step 2:** Mixin swap in `types.py`; rewrite INERT docstrings.
- [ ] **Step 3:** Create `fs_allowlist.py` `build_grant_flags` (delegating to `fs_grants` with the taskdir `extra_baseline`).
- [ ] **Step 4:** Add shims to `host_actions.py`; thread `claustrum_wrap` into `launch_opencode` + splice into `cmd`.
- [ ] **Step 5:** In `session.py`: delete the warn shim + call; provision claustrum in `_prepare`; build wrap; pass to `launch_opencode`; add `emit_claustrum_update_notice`.
- [ ] **Step 6:** Update opencode demo/tests to pass `delivery_type=` when `fs_isolation=True`.
- [ ] **Step 7:** `cd packages/optio-opencode && ../../.venv/bin/python -m pytest -q` → PASS. (The fastify WS / probe flakes are known — `OPTIO_SKIP_PREFLIGHT_TESTS` context; re-run isolated if they appear.)
- [ ] **Step 8:** Commit `feat(optio-opencode): wire claustrum fs-isolation (was inert)`.

---

## Phase 3 — Cross-engine verification

### Task 11: Extend the config-parity guard

**Files:**
- Modify: `packages/optio-demo/tests/test_config_parity.py`

- [ ] **Step 1: Write the failing test.** Add to `test_config_parity.py`:

```python
import pytest

CLAUSTRUM_TRIAD = {"fs_isolation", "extra_allowed_dirs", "delivery_type"}


def test_every_engine_has_the_claustrum_triad():
    for cls in CONFIGS:
        missing = CLAUSTRUM_TRIAD - set(_fields(cls))
        assert not missing, f"{cls.__name__} missing claustrum triad: {sorted(missing)}"


def test_every_engine_requires_delivery_type_when_fs_isolation_on():
    for cls in CONFIGS:
        with pytest.raises(ValueError, match="delivery_type"):
            _construct_minimal(cls, fs_isolation=True, delivery_type=None)
```

Add a `_construct_minimal(cls, **overrides)` helper that builds each config with only the required/overridden kwargs (each engine's other required fields default, so `cls(**overrides)` should suffice; if an engine needs extra required kwargs, supply them). Run → FAIL (engines not yet migrated raise `TypeError`/no-raise).

- [ ] **Step 2:** After Tasks 4-10, run → PASS. Also add the triad names to the existing `CORE` set (they are now universal) if desired, or keep the separate `CLAUSTRUM_TRIAD` test.
- [ ] **Step 3:** `cd packages/optio-demo && ../../.venv/bin/python -m pytest tests/test_config_parity.py -q` → PASS.
- [ ] **Step 4:** Commit `test(optio-demo): parity guard for the claustrum triad + mandatory delivery_type`.

### Task 12: Live fail-closed verification on the 3 newly-wrapped engines

Real binaries, never fakes ([[feedback_real_binary_capability_data]]). Not an automated pytest — a documented manual/scripted acceptance run recorded in the PR/commit message.

- [ ] **Step 1: grok** — run a real grok task with `fs_isolation=True, delivery_type="audit"`; confirm (a) grok launches confined (write outside workdir/grants is denied), (b) with Landlock unavailable the launch REFUSES (fail-closed), (c) no `--sandbox optio` in the argv, (d) the update notice fires if a newer claustrum tag exists.
- [ ] **Step 2: codex** — real codex task; confirm (a) claustrum confines fs, (b) `network_access=False` still blocks network (native workspace-write intact), (c) `network_access=True` permits it, (d) fail-closed when Landlock unavailable.
- [ ] **Step 3: opencode** — real opencode task; confirm (a) `opencode web` still binds localhost + the UI loads under the wrap (Landlock ≠ network), (b) the live `opencode.db` at `<taskdir>/opencode.db` is writable (taskdir grant works), (c) fs confinement holds, (d) fail-closed when Landlock unavailable.
- [ ] **Step 4:** Full suite: `make test` (two-phase). Set `OPTIO_SKIP_PREFLIGHT_TESTS=1` only for release; for this verification run the real suites. Record results.

---

## Self-Review

- **Spec coverage:** trust model (Global Constraints) · mixin (Task 2) · shared wrap/notice (Task 3) · shared grants (Task 1) · all 7 engines adopt (Tasks 4-10) · grok profile removal (Task 8) · codex network rework (Task 9) · opencode wiring (Task 10) · parity guard (Task 11) · live real-binary checks (Task 12). All spec sections mapped.
- **`delivery_type` mandatory** enforced once (Task 2 `_validate_claustrum`), inherited by all — no per-engine drift.
- **Type consistency:** `build_grant_flags` signature (Task 1) is the one consumed in Tasks 4-10; `build_claustrum_wrap`/`emit_claustrum_update_notice` (Task 3) signatures match every call site.
- **Known flakes** (kimi cred-watcher, opencode WS/probe, optio-core cancel) are pre-existing and orthogonal — re-run isolated, do not treat as regressions.

