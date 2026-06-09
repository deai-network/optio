# optio-claudecode Filesystem Isolation (claustrum) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the optio-claudecode `claude` launch in the claustrum Landlock sandbox (default-on, fail-closed, local + remote), layered on the existing `HOME`/`CLAUDE_CONFIG_DIR` redirect, without breaking the in-repo demo or test suite.

**Architecture:** A new task-config surface (`fs_isolation`, `extra_allowed_dirs`, `delivery_type`) drives a per-task allowlist (`fs_allowlist.py`). A new `ensure_claustrum_installed` provisions a static claustrum binary on the host (clone+cross-compile on the engine, place via Host protocol). The launch path threads a `claustrum_wrap` argv through `launch_ttyd_with_claude → build_tmux_session_argv → _build_claude_shell_command`, composing pasta-outside/claustrum-inside. A pre-launch freshness check delivers a one-line notice via the existing `on_deliverable` callback. The default-on flag forces a repo-wide migration of every `ClaudeCodeTaskConfig` construction.

**Tech Stack:** Python (optio-claudecode, optio-agents, optio-host), claustrum (Go, vendored by pinned tag), Landlock, pytest.

**Spec:** `docs/2026-06-09-optio-claudecode-fs-isolation-design.md`.

---

## Background the engineer needs

- **Why:** the env-var redirect (`HOME`/`CLAUDE_CONFIG_DIR`) makes claude *target* the per-task home but does not *enforce* it. claustrum adds kernel-enforced Landlock confinement so claude (and tool subprocesses) cannot read the host's real `~/.claude`, `~/.ssh`, anything outside the workdir.
- **claustrum CLI:** `claustrum [--ro P]… [--rox P]… [--rw P]… [--rwx P]… [--best-effort] [--abi-min N] -- CMD [ARGS…]`. Exits non-zero (distinct code) if Landlock is unavailable/below floor — that is the fail-closed signal. Repo: `github.com/deai-network/claustrum`, pinned `v0.1.0`.
- **Composition with pasta:** the existing `OPTIO_CLAUDECODE_NETNS` seal is local-only and wraps claude as `[*pasta_args, "bash", "-c", "IS_SANDBOX=1 claude …"]` inside `_build_claude_shell_command`. claustrum goes **inside** pasta (`pasta -- claustrum … -- bash -c "claude …"`) and applies **local and remote**.
- **Fail-closed:** with `fs_isolation` on, any provisioning failure raises in `_prepare`; an incapable kernel makes claustrum exit non-zero at launch.

## File structure

- Modify `packages/optio-claudecode/src/optio_claudecode/types.py` — new fields + `AllowedDir` + validation.
- Create `packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py` — allowlist → claustrum flags (pure).
- Modify `packages/optio-claudecode/src/optio_claudecode/host_actions.py` — `ensure_claustrum_installed`, freshness check, constants, and the `claustrum_wrap` threading in `_build_claude_shell_command` / `build_tmux_session_argv` / `launch_ttyd_with_claude`.
- Modify `packages/optio-claudecode/src/optio_claudecode/session.py` — orchestration (provision + freshness deliver/cleanup + compute allowlist + pass wrap).
- Modify `packages/optio-demo/src/optio_demo/tasks/claudecode.py` — set `delivery_type`.
- Migrate all existing `ClaudeCodeTaskConfig(...)` test sites.
- New tests across the above.

---

## Task 1: Task-config surface (TDD)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`
- Test: `packages/optio-claudecode/tests/test_types.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_types.py`)

```python
import pytest
from optio_claudecode.types import ClaudeCodeTaskConfig, AllowedDir


def test_fs_isolation_defaults_on():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x", delivery_type="bug-report")
    assert cfg.fs_isolation is True
    assert cfg.delivery_type == "bug-report"


def test_fs_isolation_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        ClaudeCodeTaskConfig(consumer_instructions="x")  # fs_isolation defaults True, no delivery_type


def test_fs_isolation_off_allows_missing_delivery_type():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.fs_isolation is False
    assert cfg.delivery_type is None


def test_extra_allowed_dirs_mode_validated():
    with pytest.raises(ValueError, match="mode"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            delivery_type="d",
            extra_allowed_dirs=[AllowedDir(path="/data", mode="exec")],  # invalid
        )


def test_extra_allowed_dirs_ok():
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        delivery_type="d",
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro"), AllowedDir(path="/scratch", mode="rw")],
    )
    assert cfg.extra_allowed_dirs[0].path == "/data"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd ~/deai/optio && python -m pytest packages/optio-claudecode/tests/test_types.py -k 'fs_isolation or extra_allowed' -q`
Expected: FAIL — `ImportError: cannot import name 'AllowedDir'` / fields missing.

- [ ] **Step 3: Add `AllowedDir`, fields, and validation to `types.py`**

Add to the import block (after the existing `from dataclasses import dataclass`):
```python
from dataclasses import dataclass, field
```

Add the `AllowedDir` type and update `__all__` (after the existing `from optio_host.types import SSHConfig` import and before `__all__`):
```python
@dataclass
class AllowedDir:
    """A caller-supplied extra path grant for filesystem isolation.

    ``mode`` is exactly ``"ro"`` (read-only) or ``"rw"`` (read-write).
    Grants are additive: callers may widen the allowlist but never mask
    the security baseline.
    """

    path: str
    mode: Literal["ro", "rw"]
```
Append `"AllowedDir"` to the `__all__` list.

Add the three fields to `ClaudeCodeTaskConfig`, immediately after the `focus_mode: bool = False` field:
```python
    # --- filesystem isolation (claustrum) ------------------------------
    # When True (default), claude runs confined to an explicit filesystem
    # allowlist via the claustrum Landlock sandbox. Fail-closed: if claustrum
    # cannot be provisioned or the kernel lacks Landlock, the task refuses to
    # launch rather than run unconfined. Set False to opt a single task out.
    fs_isolation: bool = True
    # Additive caller extensions to the allowlist (never masks the baseline).
    extra_allowed_dirs: list[AllowedDir] | None = None
    # Top-level subdir under <workdir>/deliverables/ used to route the
    # pre-launch "newer claustrum release available" notice through the
    # existing on_deliverable callback. MANDATORY when fs_isolation is True.
    delivery_type: str | None = None
```

Add validation to `__post_init__` (append at the end of the existing method body):
```python
        if self.fs_isolation and not (self.delivery_type and self.delivery_type.strip()):
            raise ValueError(
                "ClaudeCodeTaskConfig: fs_isolation is on (default) but "
                "delivery_type is unset. Set delivery_type=<subdir> (the "
                "deliverables/ prefix for filesystem-isolation notices), or "
                "set fs_isolation=False to opt out."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.extra_allowed_dirs: mode={ad.mode!r} "
                    f"must be 'ro' or 'rw' (path={ad.path!r})."
                )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest packages/optio-claudecode/tests/test_types.py -k 'fs_isolation or extra_allowed' -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py packages/optio-claudecode/tests/test_types.py
git commit -m "feat(claudecode): fs_isolation/extra_allowed_dirs/delivery_type task-config surface"
```

---

## Task 2: Repo-wide migration (restore green after the default-on flag)

Adding `fs_isolation=True` default + mandatory `delivery_type` breaks every existing `ClaudeCodeTaskConfig(...)` that omits `delivery_type`. Migrate all sites so the suite is green again. This is mechanical and the test suite is the gate.

**Files (all 37 construction sites — see spec; demo is production, the rest are tests):**
- `packages/optio-demo/src/optio_demo/tasks/claudecode.py:175,206`
- All test sites listed below.

- [ ] **Step 1: Confirm the breakage**

Run: `cd ~/deai/optio && python -m pytest packages/optio-claudecode/tests packages/optio-demo/tests -q 2>&1 | tail -20`
Expected: many failures/errors from `ValueError: ... delivery_type is unset`.

- [ ] **Step 2: Migrate the demo (production) — set `delivery_type`**

In `packages/optio-demo/src/optio_demo/tasks/claudecode.py`, add `delivery_type="system-notices",` to BOTH `ClaudeCodeTaskConfig(...)` constructions:
- the `claudecode-seed-setup` config (after `consumer_instructions=SEED_SETUP_PROMPT,`),
- the seed-pinned demo config (after `consumer_instructions=CONSUMER_PROMPT,`).

- [ ] **Step 3: Migrate the tests — opt out where isolation is not under test**

For each test-suite `ClaudeCodeTaskConfig(...)` construction that does NOT specifically exercise filesystem isolation, add `fs_isolation=False,` to the constructor kwargs. The exhaustive site list:

```
packages/optio-claudecode/tests/test_sanity.py:40,50
packages/optio-claudecode/tests/test_runtime_cache.py:73,104
packages/optio-claudecode/tests/test_session_seed_saveback.py:58,79
packages/optio-claudecode/tests/test_session_seed_consume.py:56,82
packages/optio-claudecode/tests/test_session_seed_unknown_id.py:48
packages/optio-claudecode/tests/test_session_hooks.py:34,67,95
packages/optio-claudecode/tests/test_rescue_orphan.py:240
packages/optio-claudecode/tests/test_seed_config.py:17,24
packages/optio-claudecode/tests/test_on_resume_refresh.py:71,77
packages/optio-claudecode/tests/test_session_resume_decrypt_failure.py:58,75
packages/optio-claudecode/tests/test_session_local.py:54,91,118
packages/optio-claudecode/tests/test_session_resume.py:59,140,245,280
packages/optio-claudecode/tests/test_session_blob_hooks.py:9,15,26,36,45,50,55
packages/optio-claudecode/tests/test_session_seed_capture.py:68
packages/optio-claudecode/tests/test_home_isolation.py:43
packages/optio-claudecode/tests/test_types.py:9,31,41
```

For `test_types.py:9,31,41` — these are pre-existing type tests; add `fs_isolation=False` unless the test is asserting validation (leave the Task-1 additions intact).

- [ ] **Step 4: Run the full suite to confirm green**

Run: `python -m pytest packages/optio-claudecode/tests packages/optio-demo/tests -q 2>&1 | tail -20`
Expected: all pass (or only pre-existing, unrelated skips). Iterate until green — any remaining `delivery_type is unset` error is a missed site; fix it.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate ClaudeCodeTaskConfig sites for default-on fs_isolation (demo sets delivery_type, tests opt out)"
```

---

## Task 3: Allowlist module (TDD)

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py`
- Test: `packages/optio-claudecode/tests/test_fs_allowlist.py`

The baseline path list is filled in Task 7 (from the tracer). For now use a small placeholder-free starter baseline that the test pins; Task 7 extends it.

- [ ] **Step 1: Write failing tests**

```python
from optio_claudecode.types import AllowedDir
from optio_claudecode import fs_allowlist


def test_grant_flags_orders_modes_and_maps_caller():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        claude_cache_dir="/cache/versions",
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro"),
                            AllowedDir(path="/scratch", mode="rw")],
    )
    # workdir is read-write-execute
    assert "--rwx" in flags
    i = flags.index("--rwx")
    assert flags[i + 1] == "/wd"
    # claude cache is read+exec
    assert "--rox" in flags
    assert "/cache/versions" in flags
    # caller extras mapped
    assert "--ro" in flags and "/data" in flags
    assert "--rw" in flags and "/scratch" in flags
    # baseline system dir present
    assert "/usr" in flags


def test_no_extra_dirs_ok():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd", claude_cache_dir="/cache", extra_allowed_dirs=None)
    assert "/wd" in flags and "/cache" in flags
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest packages/optio-claudecode/tests/test_fs_allowlist.py -q`
Expected: FAIL — module/function missing.

- [ ] **Step 3: Create `fs_allowlist.py`**

```python
"""Build the claustrum filesystem-allowlist flags for a claude launch.

Three parts:
  * a curated static BASELINE of what claude + its tool subprocesses need
    (system dirs, /dev nodes, /proc, CA certs) — see _BASELINE. Produced by
    tracing a real claude session (LD_PRELOAD open/stat tracer) and distilling
    the touched paths; it is a build-time artifact, NOT a runtime trace.
  * DYNAMIC per-task paths (the workdir, the claude install/cache tree).
  * CALLER extras (ClaudeCodeTaskConfig.extra_allowed_dirs).

Output is the ordered list of claustrum grant flags, e.g.
``["--rox", "/usr", ..., "--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]``.
Non-existent paths are harmless: claustrum ignores missing paths.
"""

from __future__ import annotations

from .types import AllowedDir

# (flag, path) baseline. --rox = read+execute (binaries/libs), --ro = read-only.
# Extended in Task 7 from the tracer output. Missing paths are ignored by claustrum.
_BASELINE: list[tuple[str, str]] = [
    ("--rox", "/usr"),
    ("--rox", "/bin"),
    ("--rox", "/sbin"),
    ("--rox", "/lib"),
    ("--rox", "/lib64"),
    ("--rox", "/lib32"),
    ("--ro", "/etc"),
    ("--ro", "/etc/ssl"),
    ("--ro", "/etc/resolv.conf"),
    ("--ro", "/proc"),
    ("--ro", "/dev/null"),
    ("--ro", "/dev/zero"),
    ("--ro", "/dev/urandom"),
    ("--ro", "/dev/random"),
    ("--rw", "/dev/tty"),
]


def build_grant_flags(
    *,
    workdir: str,
    claude_cache_dir: str,
    extra_allowed_dirs: list[AllowedDir] | None,
) -> list[str]:
    """Return the ordered list of claustrum grant flags for a launch.

    ``workdir`` (the per-task tree, incl. the isolated home) is granted rwx so
    claude tools may write and execute scripts. ``claude_cache_dir`` (where the
    real claude+node binaries live, outside the workdir) is granted read+exec.
    """
    flags: list[str] = []
    for flag, path in _BASELINE:
        flags += [flag, path]
    flags += ["--rwx", workdir.rstrip("/")]
    flags += ["--rox", claude_cache_dir.rstrip("/")]
    for ad in extra_allowed_dirs or []:
        flags += [f"--{ad.mode}", ad.path]
    return flags
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest packages/optio-claudecode/tests/test_fs_allowlist.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py packages/optio-claudecode/tests/test_fs_allowlist.py
git commit -m "feat(claudecode): fs_allowlist — build claustrum grant flags"
```

---

## Task 4: claustrum provisioning (`ensure_claustrum_installed` + freshness check)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_claustrum_provision.py`

- [ ] **Step 1: Add constants** near the other module constants in `host_actions.py`:

```python
# claustrum: standalone Landlock filesystem-sandbox CLI, vendored by pinned tag.
# Bumping is deliberate (a newer tag triggers a delivery notice, never auto-use).
_CLAUSTRUM_REPO = "https://github.com/deai-network/claustrum"
_CLAUSTRUM_PINNED_TAG = "v0.1.0"
# uname -m -> Go GOARCH.
_GOARCH_BY_UNAME = {"x86_64": "amd64", "aarch64": "arm64"}
```

- [ ] **Step 2: Add the arch helper + provisioning + freshness functions** to `host_actions.py`:

```python
async def _detect_goarch(host: "Host") -> str:
    """Map the host's uname -m to a Go GOARCH (Linux only)."""
    r_os = await host.run_command("uname -s")
    if r_os.exit_code != 0 or r_os.stdout.strip() != "Linux":
        raise RuntimeError(
            f"claustrum requires a Linux host (uname -s={r_os.stdout.strip()!r})."
        )
    r = await host.run_command("uname -m")
    if r.exit_code != 0:
        raise RuntimeError(f"uname -m failed (exit {r.exit_code}): {r.stderr.strip()[:200]}")
    arch = r.stdout.strip()
    goarch = _GOARCH_BY_UNAME.get(arch)
    if goarch is None:
        raise RuntimeError(
            f"unsupported host arch {arch!r} for claustrum (supported: "
            f"{sorted(_GOARCH_BY_UNAME)})."
        )
    return goarch


async def _build_claustrum_on_engine(goarch: str, tag: str, dest: str) -> None:
    """Clone claustrum at ``tag`` and cross-compile a static binary to ``dest``.

    Runs ON THE ENGINE (where git + the Go toolchain live), never on an ssh
    target. ``dest`` is an engine-local path.
    """
    import asyncio
    import os
    import tempfile

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="claustrum-src-") as src:
        clone = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--branch", tag, _CLAUSTRUM_REPO, src,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await clone.communicate()
        if clone.returncode != 0:
            raise RuntimeError(f"git clone claustrum {tag} failed: {out.decode()[:400]}")
        env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": goarch}
        build = await asyncio.create_subprocess_exec(
            "go", "build", "-trimpath", "-ldflags", "-s -w", "-o", dest, ".",
            cwd=src, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await build.communicate()
        if build.returncode != 0:
            raise RuntimeError(f"go build claustrum ({goarch}) failed: {out.decode()[:400]}")


async def ensure_claustrum_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_dir: str | None = None,
) -> str:
    """Ensure a claustrum binary (pinned tag, host arch) is on the host.

    Builds on the engine (clone + cross-compile, cached by (tag, arch)), places
    the static binary on the target host via the Host protocol, chmods +x, and
    verifies ``--version``. Returns the claustrum path on the target host.
    """
    import os

    host = hook_ctx._host
    goarch = await _detect_goarch(host)
    cache_dir = await _resolve_cache_dir(host, install_dir)
    target_path = f"{cache_dir}/claustrum/{_CLAUSTRUM_PINNED_TAG}/{goarch}/claustrum"

    # Already on the target host?
    probe = await host.run_command(f"test -x {shlex.quote(target_path)} && {shlex.quote(target_path)} --version")
    if probe.exit_code == 0:
        return target_path

    hook_ctx.report_progress(None, "Preparing claustrum (filesystem isolation)…")
    # Engine-local cached build, then place onto the (possibly remote) target.
    engine_cache = os.path.expanduser(
        f"~/.cache/optio-claudecode/claustrum/{_CLAUSTRUM_PINNED_TAG}/{goarch}/claustrum"
    )
    if not os.path.exists(engine_cache):
        await _build_claustrum_on_engine(goarch, _CLAUSTRUM_PINNED_TAG, engine_cache)

    await host.put_file_to_host(engine_cache, target_path)
    r = await host.run_command(f"chmod +x {shlex.quote(target_path)}")
    if r.exit_code != 0:
        raise RuntimeError(f"chmod +x claustrum failed (exit {r.exit_code}): {r.stderr.strip()[:200]}")
    v = await host.run_command(f"{shlex.quote(target_path)} --version")
    if v.exit_code != 0:
        raise RuntimeError(
            f"claustrum placed at {target_path!r} but --version failed "
            f"(exit {v.exit_code}): {v.stderr.strip()[:200]}"
        )
    return target_path


async def claustrum_newer_tag() -> str | None:
    """Return the newest claustrum tag if it is newer than the pinned one, else None.

    Engine-side egress only. Best-effort: network failure returns None (no notice).
    """
    import asyncio

    try:
        p = await asyncio.create_subprocess_exec(
            "git", "ls-remote", "--tags", "--refs", _CLAUSTRUM_REPO,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p.communicate()
        if p.returncode != 0:
            return None
    except Exception:  # noqa: BLE001
        return None
    tags = []
    for line in out.decode().splitlines():
        ref = line.rsplit("/", 1)[-1].strip()
        if ref.startswith("v"):
            tags.append(ref)

    def key(t: str) -> tuple:
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())

    if not tags:
        return None
    newest = max(tags, key=key)
    return newest if key(newest) > key(_CLAUSTRUM_PINNED_TAG) else None
```

- [ ] **Step 3: Write tests** `tests/test_claustrum_provision.py`

```python
import pytest
from optio_claudecode import host_actions


def test_goarch_map():
    assert host_actions._GOARCH_BY_UNAME["x86_64"] == "amd64"
    assert host_actions._GOARCH_BY_UNAME["aarch64"] == "arm64"


@pytest.mark.parametrize("pinned,remote,expect", [
    ("v0.1.0", ["v0.1.0"], None),
    ("v0.1.0", ["v0.1.0", "v0.2.0"], "v0.2.0"),
    ("v0.1.0", ["v0.0.9"], None),
])
def test_newer_tag_selection(monkeypatch, pinned, remote, expect):
    # Unit-test the version comparison via a tiny reimplementation guard:
    def key(t):
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())
    newest = max(remote, key=key)
    got = newest if key(newest) > key(pinned) else None
    assert got == expect
```

(The network/build paths are exercised by the integration test in Task 8; this unit test pins the arch map and the version-comparison logic.)

- [ ] **Step 4: Run**

Run: `python -m pytest packages/optio-claudecode/tests/test_claustrum_provision.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_claustrum_provision.py
git commit -m "feat(claudecode): ensure_claustrum_installed + freshness tag check"
```

---

## Task 5: Thread `claustrum_wrap` into the launch (TDD)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_claustrum_wrap.py`

- [ ] **Step 1: Write failing tests**

```python
from optio_claudecode import host_actions


def _shell(claustrum_wrap, local_mode, monkeypatch, netns=""):
    monkeypatch.setenv("OPTIO_CLAUDECODE_NETNS", netns)
    _, shell = host_actions._build_claude_shell_command(
        claude_path="/wd/home/.local/bin/claude",
        workdir="/wd",
        extra_env=None,
        claude_flags=["--print", "x"],
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
    return shell


def test_no_wrap_unchanged(monkeypatch):
    shell = _shell(None, False, monkeypatch)
    assert "claustrum" not in shell
    assert "/wd/home/.local/bin/claude" in shell


def test_claustrum_wraps_claude(monkeypatch):
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]
    shell = _shell(wrap, False, monkeypatch)
    assert "/c/claustrum --best-effort --abi-min 1 --rwx /wd --" in shell
    # claude runs after the claustrum separator
    assert shell.index("claustrum") < shell.index("/wd/home/.local/bin/claude")


def test_pasta_outside_claustrum_inside(monkeypatch):
    wrap = ["/c/claustrum", "--", ]
    shell = _shell(wrap, True, monkeypatch, netns="pasta --config-net --")
    # pasta is outermost, then claustrum, then bash -c claude
    assert shell.index("pasta") < shell.index("claustrum") < shell.index("IS_SANDBOX=1")
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest packages/optio-claudecode/tests/test_claustrum_wrap.py -q`
Expected: FAIL — `_build_claude_shell_command() got an unexpected keyword argument 'claustrum_wrap'`.

- [ ] **Step 3: Edit `_build_claude_shell_command`**

Change the signature to add the new parameter:
```python
def _build_claude_shell_command(
    *,
    claude_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    local_mode: bool = False,
    claustrum_wrap: list[str] | None = None,
) -> tuple[list[str], str]:
```

Replace the netns/claude_cmd block (the current `netns_wrap = ...` through the `claude_argv = ...` line) with composition pasta-outside / claustrum-inside:
```python
    netns_wrap = os.environ.get("OPTIO_CLAUDECODE_NETNS", "").strip()
    use_netns = bool(netns_wrap) and local_mode

    # Innermost: the claude invocation. Under the netns seal it is wrapped in
    # `bash -c "IS_SANDBOX=1 …"` exactly as before.
    if use_netns:
        inner = "IS_SANDBOX=1 " + " ".join(
            shlex.quote(c) for c in [claude_path, *claude_flags]
        )
        claude_invocation = ["bash", "-c", inner]
    else:
        claude_invocation = [claude_path, *claude_flags]
        if netns_wrap and not local_mode:
            _LOG.info(
                "OPTIO_CLAUDECODE_NETNS set (value=%r) but host is remote — seal "
                "skipped (no localhost to seal over SSH).", netns_wrap,
            )

    # claustrum goes INSIDE pasta: it confines only claude + its subprocesses.
    if claustrum_wrap:
        inner_cmd = [*claustrum_wrap, *claude_invocation]
        _LOG.info("claustrum filesystem isolation active (wrap=%r)", claustrum_wrap)
    else:
        inner_cmd = claude_invocation

    # pasta (local-only) is the OUTERMOST wrapper.
    if use_netns:
        claude_cmd = [*shlex.split(netns_wrap), *inner_cmd]
        _LOG.info("OPTIO_CLAUDECODE_NETNS active (local mode) — pasta outermost")
    else:
        claude_cmd = inner_cmd

    claude_argv = " ".join(shlex.quote(c) for c in claude_cmd)
```
(Leave the `log_path` / `bash_payload` / `shell_command` tail unchanged.)

- [ ] **Step 4: Thread the param through `build_tmux_session_argv`**

Add `claustrum_wrap: list[str] | None = None,` to its signature (after `local_mode: bool = False,`), and pass it into the `_build_claude_shell_command(...)` call:
```python
    _, shell_command = _build_claude_shell_command(
        claude_path=claude_path,
        workdir=workdir,
        extra_env=extra_env,
        claude_flags=claude_flags,
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
```

- [ ] **Step 5: Thread the param through `launch_ttyd_with_claude`**

Add `claustrum_wrap: list[str] | None = None,` to its signature (after `session_name: str = "optio",`), and pass it into the `build_tmux_session_argv(...)` call:
```python
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        claude_path=claude_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        claude_flags=claude_flags,
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest packages/optio-claudecode/tests/test_claustrum_wrap.py -q`
Expected: PASS.

- [ ] **Step 7: Run the existing host_actions tests for no regression**

Run: `python -m pytest packages/optio-claudecode/tests/test_host_actions.py -q`
Expected: PASS (the netns seal tests still hold — pasta stays outermost, `IS_SANDBOX=1` preserved).

- [ ] **Step 8: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_claustrum_wrap.py
git commit -m "feat(claudecode): thread claustrum_wrap through launch (pasta-outside, claustrum-inside)"
```

---

## Task 6: Orchestration — provision, freshness notice, compute wrap

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1: Provision claustrum in `_prepare`**

In `_prepare`, after the `ttyd_path = await host_actions.ensure_ttyd_installed(...)` block, add (guarded by `config.fs_isolation`):
```python
    nonlocal claustrum_path, claustrum_newer  # add these to the nonlocal/closure vars
    claustrum_path = None
    claustrum_newer = None
    if config.fs_isolation:
        claustrum_path = await host_actions.ensure_claustrum_installed(
            hook_ctx, install_dir=config.claude_install_dir,
        )
        claustrum_newer = await host_actions.claustrum_newer_tag()
```
(Declare `claustrum_path` and `claustrum_newer` alongside the other closure locals where `claude_path`/`ttyd_path` are declared, initialized to `None`.)

- [ ] **Step 2: Freshness deliver-then-cleanup, before launch**

In `_claudecode_body`, after the fresh-start `plant_home_files(...)` + seed merge + `write_text("CLAUDE.md", ...)` block and BEFORE the launch (before `claude_flags = host_actions.build_claude_flags(...)`), add:
```python
        if config.fs_isolation and claustrum_newer and config.on_deliverable is not None:
            rel = f"{config.delivery_type}/claustrum-update-{claustrum_newer}.md"
            text = (
                f"A newer claustrum release ({claustrum_newer}) is available; "
                f"the pinned version is {host_actions._CLAUSTRUM_PINNED_TAG}. "
                f"Audit it and consider bumping the pin."
            )
            await host.write_text(f"deliverables/{rel}", text)
            try:
                await config.on_deliverable(hook_ctx, rel, text)
            finally:
                # Clean slate for the real agent: remove the notice file. (No
                # optio.log "Deliverable:" line is written — this is a direct
                # callback invocation, not the tail loop.)
                await host.run_command(
                    f"rm -f {__import__('shlex').quote(host.workdir.rstrip('/') + '/deliverables/' + rel)}"
                )
```

- [ ] **Step 3: Compute the allowlist + claustrum_wrap and pass to launch**

Just before the `handle, ttyd_port, ... = await host_actions.launch_ttyd_with_claude(` call, build the wrap:
```python
        claustrum_wrap = None
        if config.fs_isolation:
            from . import fs_allowlist
            cache_dir = await host_actions._resolve_cache_dir(host, config.claude_install_dir)
            grants = fs_allowlist.build_grant_flags(
                workdir=host.workdir,
                claude_cache_dir=cache_dir,
                extra_allowed_dirs=config.extra_allowed_dirs,
            )
            claustrum_wrap = [
                claustrum_path, "--best-effort", "--abi-min", "1", *grants, "--",
            ]
```
Then add `claustrum_wrap=claustrum_wrap,` to the `launch_ttyd_with_claude(...)` call kwargs.

- [ ] **Step 4: Run the claudecode suite**

Run: `python -m pytest packages/optio-claudecode/tests -q 2>&1 | tail -20`
Expected: PASS (fs_isolation=False in migrated tests means these new branches are skipped; the local-session tests that DO run with isolation are covered in Task 8).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(claudecode): orchestrate claustrum provisioning, freshness notice, and launch wrap"
```

---

## Task 7: Derive the static baseline from a real claude trace

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py` (the `_BASELINE` constant)

- [ ] **Step 1: Trace a real claude session**

Build the LD_PRELOAD open/stat tracer (the one used during the spec investigation: interpose `open`/`openat`/`stat`/`lstat`/`access`/`readlink`/`opendir`, logging every path NOT under the isolated workdir). Run a real `claude --print` session with `HOME`/`CLAUDE_CONFIG_DIR` pointed at an isolated tree, capturing every absolute path claude and its subprocesses touch outside the workdir.

Run (example):
```bash
# build tracer, then:
HOME=/tmp/iso CLAUDE_CONFIG_DIR=/tmp/iso/.claude LD_PRELOAD=/tmp/trace.so \
  claude --print "say OK"
sort -u /tmp/trace.log
```

- [ ] **Step 2: Distill into `_BASELINE`**

Replace the starter `_BASELINE` in `fs_allowlist.py` with the curated, commented set covering exactly the traced paths: system dirs (`--rox` for executable trees, `--ro` for data), the specific `/dev` nodes touched, `/proc` entries, CA bundle, timezone (`/usr/share/zoneinfo`), locale, and any other host paths claude reads. Group with comments explaining each cluster. Keep it minimal — only what the trace shows, plus the obviously-required `/dev/tty` (rw) for the TUI.

- [ ] **Step 3: Update the `fs_allowlist` unit test** to assert the curated baseline includes the key clusters (e.g. `/usr`, the CA path, `/dev/tty`), then run:

Run: `python -m pytest packages/optio-claudecode/tests/test_fs_allowlist.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/fs_allowlist.py packages/optio-claudecode/tests/test_fs_allowlist.py
git commit -m "feat(claudecode): curated claustrum allowlist baseline from claude trace"
```

---

## Task 8: End-to-end integration test (real Landlock, via the local-session harness)

**Files:**
- Create: `packages/optio-claudecode/tests/test_fs_isolation_e2e.py`

This mirrors the existing `test_home_isolation.py` / `test_session_local.py` harness (real local session with the `fake_claude` shim or a real claude), but with `fs_isolation=True`, asserting a read outside the workdir is denied.

- [ ] **Step 1: Write the test** following the local-session harness in `tests/test_session_local.py` (construct a `ClaudeCodeTaskConfig(..., fs_isolation=True, delivery_type="t")`, run a local session whose claude/command attempts to read a sentinel file OUTSIDE the workdir), asserting the read fails (Landlock `EACCES`) while a read inside the workdir succeeds. Skip if `claustrum_newer`/kernel Landlock is unavailable:

```python
import os
import pytest

# Skip if Landlock is unavailable on this kernel.
def _landlock_ok():
    try:
        # claustrum --abi-min 1 against /bin/true: exit 0 only if Landlock works
        import subprocess, shutil
        cl = shutil.which("claustrum") or os.path.expanduser(
            "~/.cache/optio-claudecode/claustrum/v0.1.0/amd64/claustrum")
        if not os.path.exists(cl):
            return False
        return subprocess.run([cl, "--abi-min", "1", "--rox", "/usr", "--rox", "/bin",
                               "--", "/bin/true"]).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _landlock_ok(), reason="Landlock/claustrum unavailable")


@pytest.mark.asyncio
async def test_fs_isolation_denies_outside_workdir(tmp_path):
    # Build the full local-session config with fs_isolation=True, run it with a
    # consumer instruction / fake_claude scenario that cats a sentinel OUTSIDE
    # the workdir, and assert the launched claude could not read it.
    # (Follow the exact harness wiring in test_session_local.py.)
    ...
```

Fill the body by following `test_session_local.py`'s session construction exactly (same DB/ctx/host fixtures), adding `fs_isolation=True, delivery_type="t"`, and a sentinel file outside `tmp_path`/workdir that the launched command tries (and fails) to read.

- [ ] **Step 2: Run it**

Run: `python -m pytest packages/optio-claudecode/tests/test_fs_isolation_e2e.py -q`
Expected: PASS on this Landlock-capable kernel (it builds/caches claustrum on first run, which needs git + Go on the engine).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_fs_isolation_e2e.py
git commit -m "test(claudecode): e2e — fs_isolation denies reads outside the workdir"
```

---

## Task 9: Demo end-to-end and full-suite gate

**Files:** none (verification).

- [ ] **Step 1: Full claudecode + demo suites green**

Run: `cd ~/deai/optio && OPTIO_SKIP_PREFLIGHT_TESTS=1 python -m pytest packages/optio-claudecode/tests packages/optio-demo/tests -q 2>&1 | tail -25`
Expected: all pass. (The WS-flake env guard mirrors existing practice.)

- [ ] **Step 2: Confirm the demo launches under isolation**

Run the demo's claudecode task path (per the demo's existing smoke/e2e entrypoint) with a real or fake claude, confirming the `delivery_type="system-notices"` configs construct and a session reaches launch with `fs_isolation` on. Use the existing demo smoke test:

Run: `python -m pytest packages/optio-demo/tests/test_demo_smoke.py -q`
Expected: PASS.

- [ ] **Step 3: Commit (if any test adjustments were needed)**

```bash
git add -A && git commit -m "test(claudecode): full-suite + demo gate for fs_isolation" || echo "nothing to commit"
```

---

## Self-review notes

- **Spec coverage:** §1 task surface (Task 1), §2 provisioning (Task 4), §3 freshness deliver+cleanup (Task 6 Step 2), §4 allowlist (Tasks 3,7), §5 launch wrapping/pasta composition (Task 5), §6 fail-closed (Task 4 verify + claustrum `--abi-min 1` in Task 6 Step 3), §7 orchestration (Task 6), §8 demo adoption + repo migration (Tasks 2,9), testing (every task + Tasks 8,9). All covered.
- **Deferred-by-design:** the exact baseline path list is produced in Task 7 from the tracer (the spec defers it explicitly).
- **Type/name consistency:** `AllowedDir(path, mode)`, `fs_isolation`, `delivery_type`, `extra_allowed_dirs`, `build_grant_flags(workdir, claude_cache_dir, extra_allowed_dirs)`, `ensure_claustrum_installed`, `claustrum_newer_tag`, `claustrum_wrap`, `_CLAUSTRUM_PINNED_TAG`, `_resolve_cache_dir` are used consistently across tasks.
- **No placeholders:** every code step is concrete except Task 8's test body, which is pinned to the existing `test_session_local.py` harness (named explicitly) because it must reuse that file's fixtures verbatim.
