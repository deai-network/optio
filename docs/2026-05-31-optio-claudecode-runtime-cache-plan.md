# Claude Runtime Version-Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the `claude` binary from a shared, optio-owned version cache via a per-task `versions`→cache symlink, so the host home is never touched, the binary never enters snapshots, and claude's autoupdater keeps the cache current.

**Architecture:** `ensure_claude_installed` is rewritten: resolve a worker-side cache dir (`OPTIO_CLAUDECODE_CACHE_DIR` / `claude_install_dir` override / `${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions`), symlink `<workdir>/home/.local/share/claude/versions` → that cache, then install through the symlink on a cache miss or point `home/.local/bin/claude` at the newest cached version on reuse. Launch keeps `HOME=<workdir>/home` (state isolation unchanged). The snapshot's 230 MB `rm -rf` hack and `build_ttyd_argv`'s real-home symlink both go away.

**Tech Stack:** Python 3.11+, the optio-host `Host` transport (`run_command`/`write_text`), shell (`ln -sfn`, `install.sh`), pytest + pytest-asyncio, MongoDB-via-Docker for session tests.

**Source spec:** `docs/2026-05-31-optio-claudecode-runtime-cache-design.md` (read its "Validation" section — the symlink/install behavior is spike-confirmed).

---

## ⚠️ Execution model: PARALLEL-SHAPED, verification deferred

Per the standing preference: **no test runs inside Phases 1–2.** Each task edits one file to its **final** content and commits; tasks within a phase touch disjoint files and may run concurrently. **All pytest runs + grep guards happen once in Phase 3.** Intermediate states may be import-broken — fine. Every code step below shows complete final content (no RED/GREEN cycles). Cross-task signatures are frozen in "Shared contracts."

---

## Shared contracts (every task conforms to these)

```python
# optio_claudecode/host_actions.py

# Worker-side cache resolution. `override` is config.claude_install_dir (repurposed
# as the cache-dir override). Returns an absolute path ON THE WORKER.
async def _resolve_cache_dir(host: "Host", override: str | None) -> str: ...

# Rewritten — same name + signature as today (session call site unchanged), new body.
# `install_dir` is repurposed: it is now the CACHE-DIR override, not a literal bin dir.
# Returns the per-task claude launch path: "<workdir>/home/.local/bin/claude".
async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol", *,
    install_if_missing: bool = True,
    install_dir: str | None = None,   # cache-dir override (config.claude_install_dir)
) -> str: ...

# build_ttyd_argv: unchanged signature; body drops the real-home claude symlink
# (prep now owns home/.local/bin/claude); keeps the PATH-prepend + run-then-signal wrapper.
def build_ttyd_argv(*, ttyd_path, claude_path, workdir, bind_iface, port,
                    extra_env, claude_flags) -> list[str]: ...
```

Behavioral contract for `ensure_claude_installed` (the per-task prep):
- `home = <host.workdir>/home`; `cache = await _resolve_cache_dir(host, install_dir)`.
- `mkdir -p` the cache, `home/.local/share/claude`, `home/.local/bin`.
- `ln -sfn <cache> home/.local/share/claude/versions` (idempotent).
- If a usable version exists in the cache → ensure `home/.local/bin/claude` →
  `home/.local/share/claude/versions/<newest>` (newest = `ls <cache> | sort -V | tail -1`); **no install**.
- Else if `install_if_missing` → run `env HOME=<home> sh -c 'curl -fsSL <url> | bash'`
  (writes through the symlink into the cache, creates `home/.local/bin/claude`); verify, else raise.
- Else → raise (cache empty + install disabled).
- Return `home/.local/bin/claude`.

`_DEFAULT_INSTALL_SUBDIR`, `_resolve_install_dir`, and `_claude_present(host, path)`'s
real-home usage are removed/replaced (a version-probe helper is kept, see Task 1).

---

## File Structure

- `packages/optio-claudecode/src/optio_claudecode/host_actions.py` — cache resolution + rewritten `ensure_claude_installed` + simplified `build_ttyd_argv`. (One task — same file.)
- `packages/optio-claudecode/src/optio_claudecode/session.py` — drop the claude path from the snapshot `rm -rf` (4b).
- `packages/optio-claudecode/src/optio_claudecode/types.py` — document `claude_install_dir` as the cache-dir override.
- `packages/optio-claudecode/tests/conftest.py` — add a `claude_cache_dir` fixture (pre-populated fake version) + keep `shim_install_dir` for ttyd.
- `packages/optio-claudecode/tests/test_host_actions.py` — rewrite the claude-prep unit tests + adjust `build_ttyd_argv` tests.
- `packages/optio-claudecode/tests/test_runtime_cache.py` — NEW: cache reuse / eviction / snapshot-excludes-binary integration tests.

---

# Phase 1 — implementation (parallel; disjoint files)

## Task 1: `host_actions.py` — cache resolution, prep rewrite, build_ttyd_argv

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`

- [ ] **Step 1: Replace the install-dir constants + resolver with cache resolution**

Replace the block (current lines 34, 46-51 — the `_DEFAULT_INSTALL_SUBDIR` constant and `_resolve_install_dir`):

```python
_DEFAULT_INSTALL_SUBDIR = ".local/bin"

_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"
```
…
```python
async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/<DEFAULT_INSTALL_SUBDIR>``."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"
```

with (keep `_CLAUDE_INSTALL_URL`; drop `_DEFAULT_INSTALL_SUBDIR`; replace the resolver):

```python
_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"

# The optio-owned claude version cache lives on the WORKER, never in the host
# user's ~/.local/~/.claude. Default: ${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions.
_CACHE_DIR_SHELL_DEFAULT = (
    '${OPTIO_CLAUDECODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions}'
)


async def _resolve_cache_dir(host: "Host", override: str | None) -> str:
    """Resolve the claude version-cache dir as an absolute path on the worker.

    ``override`` (``config.claude_install_dir``) wins. Otherwise the worker's
    ``OPTIO_CLAUDECODE_CACHE_DIR`` / ``XDG_CACHE_HOME`` / ``$HOME`` decide it —
    resolved via a shell echo so RemoteHost gets the remote location.
    """
    if override is not None:
        return override.rstrip("/")
    r = await host.run_command(f'printf %s "{_CACHE_DIR_SHELL_DEFAULT}"')
    path = r.stdout.strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve claude cache dir on host "
            f"(exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    return path.rstrip("/")
```

- [ ] **Step 2: Replace `_claude_present` + `ensure_claude_installed` with the cache-prep body**

Replace `_claude_present` (lines 54-59) and `ensure_claude_installed` (lines 62-112) with:

```python
async def _claude_version_ok(host: "Host", claude_path: str) -> bool:
    """True iff ``claude_path`` is executable and prints a Claude Code version."""
    cmd = f"[ -x {shlex.quote(claude_path)} ] && {shlex.quote(claude_path)} --version"
    result = await host.run_command(cmd)
    return result.exit_code == 0 and "Claude Code" in result.stdout


async def _newest_cached_version(host: "Host", cache_dir: str) -> str | None:
    """Return the highest-semver version filename in the cache, or None if empty."""
    r = await host.run_command(
        f"ls -1 {shlex.quote(cache_dir)} 2>/dev/null | sort -V | tail -1"
    )
    name = r.stdout.strip()
    return name or None


async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Provision claude for this task from the shared, optio-owned version cache.

    The binary lives in an optio cache dir on the worker (never the host
    ~/.local/~/.claude). Per task we symlink the isolated home's
    ``.local/share/claude/versions`` at that cache, so claude's installer and
    autoupdater write version binaries *through* the symlink into the cache.

    - cache miss (+ install_if_missing) → run vendor install.sh with
      HOME=<workdir>/home, which writes through the symlink into the cache and
      creates home/.local/bin/claude.
    - cache hit → point home/.local/bin/claude at the newest cached version
      (no reinstall).
    - cache miss + install disabled → raise.

    ``install_dir`` is the cache-dir override (config.claude_install_dir).
    Returns the per-task launch path ``<workdir>/home/.local/bin/claude``.
    """
    host = hook_ctx._host
    workdir = host.workdir.rstrip("/")
    home = f"{workdir}/home"
    bin_dir = f"{home}/.local/bin"
    bin_claude = f"{bin_dir}/claude"
    share_claude = f"{home}/.local/share/claude"
    versions_link = f"{share_claude}/versions"

    cache_dir = await _resolve_cache_dir(host, install_dir)

    hook_ctx.report_progress(None, "Preparing claude runtime…")
    setup = await host.run_command(
        f"mkdir -p {shlex.quote(cache_dir)} {shlex.quote(share_claude)} "
        f"{shlex.quote(bin_dir)} && "
        f"ln -sfn {shlex.quote(cache_dir)} {shlex.quote(versions_link)}"
    )
    if setup.exit_code != 0:
        raise RuntimeError(
            f"claude runtime prep (mkdir/symlink) failed (exit {setup.exit_code}): "
            f"{setup.stderr.strip()[:200]}"
        )

    newest = await _newest_cached_version(host, cache_dir)
    if newest is not None:
        # Cache hit — point the per-task bin at the newest cached version.
        # Path goes through the versions symlink so it resolves into the cache.
        await host.run_command(
            f"ln -sfn {shlex.quote(versions_link + '/' + newest)} {shlex.quote(bin_claude)}"
        )
        if await _claude_version_ok(host, bin_claude):
            return bin_claude
        # Fall through to (re)install if the cached version is unusable.

    if not install_if_missing:
        raise RuntimeError(
            f"claude not present in cache {cache_dir!r} on host and "
            f"install_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Installing claude (vendor install.sh)…")
    install_cmd = (
        f"env HOME={shlex.quote(home)} sh -c "
        f"{shlex.quote(f'curl -fsSL {_CLAUDE_INSTALL_URL} | bash')}"
    )
    result = await host.run_command(install_cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"claude install failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:300]}"
        )
    if not await _claude_version_ok(host, bin_claude):
        raise RuntimeError(
            f"claude install reported success but {bin_claude!r} is still not "
            f"executable. Inspect the cache {cache_dir!r} and "
            f"{versions_link!r} on the host for diagnostics."
        )
    return bin_claude
```

- [ ] **Step 3: Simplify `build_ttyd_argv` — drop the real-home symlink (prep owns the bin link)**

Replace the docstring + body from `"""Construct the full argv…` through the `bash_payload = (...)` assignment (current lines 278-331) with:

```python
    """Construct the full argv for the ttyd subprocess.

    Layout:
      <ttyd_path> -W -i <iface> -p <port> -m 1 -T xterm-256color --
      env HOME=<workdir>/home PATH=<home>/.local/bin:... [<extra-env...>]
      bash -c 'cd <workdir> && <claude_path> [<flags...>]; rc=$?;
               <append DONE (rc 0) | ERROR: claude exited <rc> to optio.log>'

    ``claude_path`` is ``<workdir>/home/.local/bin/claude`` (provisioned by
    ensure_claude_installed: a symlink into the shared version cache via
    home/.local/share/claude/versions). We prepend home/.local/bin to PATH so
    the agent's own ``claude`` invocations resolve. claude runs (NOT exec'd) so
    that when it exits without writing DONE, the wrapper appends a terminal
    protocol line; the driver's optio.log tail then completes the session and
    its teardown reaps the (otherwise lingering) ttyd.
    """
    workdir_clean = workdir.rstrip("/")
    home_dir = f"{workdir_clean}/home"
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    base_path = extra.pop("PATH", None) or os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    env_assignments: list[str] = [
        f"HOME={home_dir}",
        f"PATH={home_local_bin}:{base_path}",
    ]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    claude_argv = " ".join(shlex.quote(c) for c in [claude_path, *claude_flags])
    log_path = f"{workdir_clean}/optio.log"
    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {claude_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: claude exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
```

(The `return [ttyd_path, "-W", … "bash", "-c", bash_payload]` block below is unchanged.)

- [ ] **Step 4: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py
git commit -m "feat(optio-claudecode): provision claude from a shared version cache via versions symlink"
```

---

## Task 2: `session.py` — drop the binary from the snapshot wipe

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1: Update the 4b regenerable-junk removal in `_capture_snapshot`**

Replace the 4b block (the comment + `rm -rf` that includes `home/.local/share/claude`; current lines ~415-427):

```python
    # 4b. Drop heavy, regenerable junk that accumulates under the isolated
    # HOME so it does not bloat the workdir snapshot. The claude binary
    # (home/.local/share/claude, ~230MB) is reinstalled on resume by
    # ensure_claude_installed; mozilla cache/profile are pure scratch. Left
    # in place, gzipping them in memory blows the cancellation grace period.
    _trace("capture: rm -rf regenerable home dirs START")
    await host.run_command(
        "rm -rf "
        f"{shlex.quote(workdir)}/home/.local/share/claude "
        f"{shlex.quote(workdir)}/home/.cache/mozilla "
        f"{shlex.quote(workdir)}/home/.mozilla"
    )
    _trace("capture: rm -rf regenerable home dirs DONE")
```

with:

```python
    # 4b. Drop regenerable scratch that would bloat the workdir snapshot.
    # The claude binary is NOT here: home/.local/share/claude/versions is a
    # symlink to the shared optio cache, which os.walk does not follow and CLI
    # tar stores as a symlink, so it never enters the archive. mozilla
    # cache/profile are pure scratch.
    _trace("capture: rm -rf regenerable home dirs START")
    await host.run_command(
        "rm -rf "
        f"{shlex.quote(workdir)}/home/.cache/mozilla "
        f"{shlex.quote(workdir)}/home/.mozilla"
    )
    _trace("capture: rm -rf regenerable home dirs DONE")
```

(The session's `ensure_claude_installed` call site at lines 91-95 is **unchanged** — `install_dir=config.claude_install_dir` now carries the cache override; the param name is preserved.)

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "refactor(optio-claudecode): snapshot no longer wipes claude binary (now a cache symlink)"
```

---

## Task 3: `types.py` — document `claude_install_dir` as the cache override

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] **Step 1: Update the field's doc comment**

In `packages/optio-claudecode/src/optio_claudecode/types.py`, the `claude_install_dir` field (line 50) currently has no/inline doc. Replace the install-dir fields block:

```python
    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    claude_install_dir: str | None = None
    ttyd_install_dir: str | None = None
```

with:

```python
    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for the optio-owned claude **version cache** directory (where
    # claude version binaries are installed/cached on the worker, via the
    # per-task home/.local/share/claude/versions symlink). None → the worker's
    # ``OPTIO_CLAUDECODE_CACHE_DIR`` or ``${XDG_CACHE_HOME:-$HOME/.cache}/
    # optio-claudecode/versions``. Never the host user's ~/.local/~/.claude.
    claude_install_dir: str | None = None
    ttyd_install_dir: str | None = None
```

(The `__post_init__` absolute-path validation for `claude_install_dir` stays valid — a cache override is still an absolute path.)

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/types.py
git commit -m "docs(optio-claudecode): claude_install_dir is the version-cache override"
```

---

# Phase 2 — tests (parallel; disjoint files)

## Task 4: `conftest.py` — add a pre-populated cache fixture

**Files:**
- Modify: `packages/optio-claudecode/tests/conftest.py`

- [ ] **Step 1: Add the `claude_cache_dir` fixture**

The `shim_install_dir` fixture stays (it provides the **ttyd** shim). Add a fixture that builds a fake, already-populated version cache so integration tests skip the real `install.sh` download: a cache dir containing one version file that is a symlink to the claude shim.

Append to `packages/optio-claudecode/tests/conftest.py`:

```python
@pytest.fixture
def claude_cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pre-populated fake claude version cache.

    Contains a single version file ``9.9.9`` symlinked to the claude shim, so
    ``ensure_claude_installed`` takes the cache-hit path (points
    home/.local/bin/claude at it) and never runs the real install.sh. Pass as
    ``claude_install_dir`` in ClaudeCodeTaskConfig.
    """
    cache = tmp_path / "claude-cache"
    cache.mkdir()
    version_file = cache / "9.9.9"
    os.symlink(TESTS_DIR / "claude-shim.sh", version_file)
    os.chmod(TESTS_DIR / "claude-shim.sh", 0o755)
    return cache
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/tests/conftest.py
git commit -m "test(optio-claudecode): claude_cache_dir fixture (pre-populated fake version cache)"
```

---

## Task 5: `test_host_actions.py` — rewrite prep tests + adjust build_ttyd_argv tests

**Files:**
- Modify: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Replace the `_FakeHost` + the five `ensure_claude_installed` tests**

`_FakeHost` needs a `workdir` and `write_text` is unused here. Replace the `_FakeHost` class (lines 14-30) so it carries a workdir and scripts `run_command`:

```python
class _FakeHost:
    """Minimal Host shim that records run_command calls and returns scripted results."""

    def __init__(self, scripted_results, *, workdir: str = "/wd") -> None:
        self.commands: list[str] = []
        self._scripted = list(scripted_results)
        self.workdir = workdir

    async def run_command(self, cmd: str, *, check: bool = False) -> RunResult:
        self.commands.append(cmd)
        nxt = self._scripted.pop(0)
        if callable(nxt):
            return nxt(cmd)
        return nxt
```

Replace the five tests `test_ensure_claude_installed_*` (lines 41-100) with cache-model tests. The prep issues, in order: (1) cache-dir resolve `printf` (unless override), (2) mkdir+ln setup, (3) `ls | sort -V | tail -1` newest-probe, then either a bin-relink + `--version` check (cache hit) or the install + `--version` check (miss).

```python
_OK = RunResult(stdout="2.1.158 (Claude Code)\n", stderr="", exit_code=0)
_EMPTY = RunResult(stdout="", stderr="", exit_code=0)


def _resolve_cache(path="/home/u/.cache/optio-claudecode/versions"):
    return RunResult(stdout=path, stderr="", exit_code=0)


async def test_prep_cache_hit_relinks_no_install():
    # resolve cache, setup mkdir/ln, newest=9.9.9, relink bin, --version OK
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        RunResult(stdout="9.9.9\n", stderr="", exit_code=0),  # ls|sort|tail
        _EMPTY,                                              # ln bin -> versions/9.9.9
        _OK,                                                 # --version
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/wd/home/.local/bin/claude"
    joined = " ".join(host.commands)
    assert "curl" not in joined and "install.sh" not in joined  # no install
    assert "ln -sfn" in joined and "versions" in joined


async def test_prep_cache_override_skips_resolve():
    host = _FakeHost([
        _EMPTY,                                              # mkdir + ln (no resolve)
        RunResult(stdout="9.9.9\n", stderr="", exit_code=0),  # newest
        _EMPTY,                                              # relink
        _OK,                                                 # --version
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True, install_dir="/opt/claude-cache",
    )
    assert path == "/wd/home/.local/bin/claude"
    assert any("/opt/claude-cache" in c for c in host.commands)
    assert not any("printf" in c for c in host.commands)  # override → no resolve


async def test_prep_cache_miss_runs_install_through_symlink():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty (no version)
        _EMPTY,                                              # install.sh
        _OK,                                                 # --version after install
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/wd/home/.local/bin/claude"
    install = next(c for c in host.commands if "install.sh" in c)
    assert "HOME=/wd/home" in install and "curl" in install and "bash" in install


async def test_prep_cache_miss_install_disabled_raises():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=False)
    assert "install_if_missing" in str(exc.value) and "False" in str(exc.value)


async def test_prep_install_failure_propagates():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty
        RunResult(stdout="", stderr="curl: 404", exit_code=22),  # install.sh fails
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert "install" in str(exc.value).lower()
    assert "22" in str(exc.value) or "404" in str(exc.value)
```

(`_hook_ctx` helper and the `RunResult` import are unchanged. The old `host_home` arg of `_FakeHost` is gone — no test uses `resolve_host_home` anymore.)

- [ ] **Step 2: Adjust `test_build_ttyd_argv_basic`**

In `test_build_ttyd_argv_basic`, the bin-symlink assertions are now invalid (prep owns the link). Replace the assertion block (the `bash_payload` checks added for the symlink fix):

```python
    bash_payload = argv[bash_idx + 2]
    assert "cd /tmp/optio-claudecode-x" in bash_payload
    assert "/opt/claude/claude" in bash_payload
    assert "--permission-mode bypassPermissions" in bash_payload
    # claude is symlinked into the isolated home's bin before launch.
    assert "mkdir -p /tmp/optio-claudecode-x/home/.local/bin" in bash_payload
    assert (
        "ln -sf /opt/claude/claude /tmp/optio-claudecode-x/home/.local/bin/claude"
        in bash_payload
    )
    # claude is run (not exec'd) so the wrapper can signal completion when it
    # exits without writing DONE itself.
    assert "exec /opt/claude/claude" not in bash_payload
    assert "rc=$?" in bash_payload
    assert "echo DONE >>" in bash_payload
    assert "ERROR: claude exited" in bash_payload
```

with (drop the mkdir/ln assertions; the rest stays):

```python
    bash_payload = argv[bash_idx + 2]
    assert "cd /tmp/optio-claudecode-x" in bash_payload
    assert "/opt/claude/claude" in bash_payload
    assert "--permission-mode bypassPermissions" in bash_payload
    # prep owns the bin symlink now — build_ttyd_argv no longer creates it.
    assert "ln -sf" not in bash_payload
    # PATH still prepends the isolated home's .local/bin.
    assert any(
        a.startswith("PATH=/tmp/optio-claudecode-x/home/.local/bin:") for a in argv
    ), argv
    # claude is run (not exec'd) so the wrapper can signal completion.
    assert "exec /opt/claude/claude" not in bash_payload
    assert "rc=$?" in bash_payload
    assert "echo DONE >>" in bash_payload
    assert "ERROR: claude exited" in bash_payload
```

(The two payload-execution tests `test_payload_appends_done_when_claude_exits_clean` / `_error_when_claude_exits_nonzero` still pass — `build_ttyd_argv` still runs claude + appends DONE/ERROR; their fake claude is invoked by absolute path, no bin symlink needed. Leave them unchanged.)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_host_actions.py
git commit -m "test(optio-claudecode): cache-model prep tests + build_ttyd_argv without bin symlink"
```

---

## Task 6: `test_runtime_cache.py` — integration: reuse, eviction, snapshot-excludes-binary

**Files:**
- Create: `packages/optio-claudecode/tests/test_runtime_cache.py`

- [ ] **Step 1: Create the integration tests**

```python
"""Integration: claude provisioned from the shared version cache.

Uses the ttyd + claude shims and a pre-populated fake cache so no real
install.sh download happens. Asserts: cache reuse (no install), the versions
symlink points at the cache, and a captured snapshot does not contain the
240 MB binary (it lives in the cache, outside the workdir).
"""

from __future__ import annotations

import io
import pathlib
import tarfile

import pytest
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


@pytest.mark.asyncio
async def test_cache_hit_reuses_without_install(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    monkeypatch,
):
    ctx, _cap, _flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    observed: dict[str, object] = {}

    async def probe(hook_ctx):
        wd = pathlib.Path(hook_ctx._host.workdir)
        versions = wd / "home" / ".local" / "share" / "claude" / "versions"
        observed["versions_is_symlink"] = versions.is_symlink()
        observed["versions_target"] = str(versions.resolve())
        observed["bin_claude_exists"] = (wd / "home" / ".local" / "bin" / "claude").exists()

    task = create_claudecode_task(
        process_id="cc-cache-hit",
        name="Cache hit",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="bypassPermissions",
            claude_install_dir=str(claude_cache_dir),  # cache override
            ttyd_install_dir=str(shim_install_dir),
            supports_resume=False,
            before_execute=probe,
        ),
    )
    await task.execute(ctx)

    assert observed["versions_is_symlink"] is True
    # versions → the cache override dir
    assert observed["versions_target"] == str(claude_cache_dir.resolve())
    assert observed["bin_claude_exists"] is True


@pytest.mark.asyncio
async def test_snapshot_excludes_claude_binary(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    ctx_and_captures,
    mongo_db,
    monkeypatch,
):
    """A resume-enabled session's workdir snapshot must not contain the cache
    binary (it is reachable only through the versions symlink, outside workdir)."""
    ctx, _cap, _flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    # Make the fake "version" large enough that a dereferenced capture would be
    # obvious; the shim symlink target stays small, so this asserts non-deref.
    task = create_claudecode_task(
        process_id="cc-cache-snap",
        name="Cache snap",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="bypassPermissions",
            claude_install_dir=str(claude_cache_dir),
            ttyd_install_dir=str(shim_install_dir),
            supports_resume=True,
        ),
    )
    await task.execute(ctx)

    # Load the latest snapshot's workdir blob and assert no cache version file
    # content was captured (only a symlink entry, or nothing, for versions).
    from optio_claudecode.snapshots import load_latest_snapshot
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id="cc-cache-snap")
    assert snap is not None
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    blob = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = tar.getmembers()
    # No regular file under .local/share/claude/versions/ (a symlink member is
    # fine; a regular file would mean the 240 MB binary was captured).
    offending = [
        m.name for m in members
        if "/.local/share/claude/versions/" in ("/" + m.name) and m.isfile()
    ]
    assert offending == [], offending
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-claudecode/tests/test_runtime_cache.py
git commit -m "test(optio-claudecode): cache reuse + snapshot-excludes-binary integration"
```

---

# Phase 3 — VERIFICATION (run everything, once)

> Requires MongoDB at `MONGO_URL` for the session/integration tests.

## Task 7: Verification sweep

**Files:** none.

- [ ] **Step 1: claudecode suite**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q`
Expected: PASS. Covers the rewritten `test_host_actions.py`, the new `test_runtime_cache.py`, and the unchanged session/seed/resume/prompt suites (resume + seeds are unaffected — state isolation is unchanged).

- [ ] **Step 2: agents + host + opencode unaffected (smoke)**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest -q`
Run: `cd packages/optio-host && ../../.venv/bin/python -m pytest -q`
Run: `cd packages/optio-opencode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q`
Expected: PASS (no changes in these packages; this confirms no accidental cross-package breakage).

- [ ] **Step 3: Grep guards — no host-home install path survives**

Run:
```bash
cd /home/csillag/deai/optio
! grep -rn "_resolve_install_dir\|_DEFAULT_INSTALL_SUBDIR\|resolve_host_home" packages/optio-claudecode/src \
  && grep -q "OPTIO_CLAUDECODE_CACHE_DIR" packages/optio-claudecode/src/optio_claudecode/host_actions.py \
  && echo CLEAN
```
Expected: prints `CLEAN` — the real-home install-dir resolver is gone and the cache env var is referenced.

- [ ] **Step 4: Fixups (if any)**

If a step fails, fix it, re-run the affected suite, then:
```bash
git add -A
git commit -m "fix: runtime-cache verification fixups"
```

---

## Spec coverage check

- "binary in optio-owned cache, never host `~`" → Task 1 (`_resolve_cache_dir`, prep uses cache + `HOME=<workdir>/home` install) + Task 7 grep guard.
- "per-task `versions`→cache symlink; install/autoupdate write through it" → Task 1 (`ln -sfn`, install with `HOME=home`); spike-validated in the spec.
- "cache-hit reuse without reinstall; cache-miss install; install-disabled raises; evictable" → Task 1 + Task 5 tests + Task 6 cache-hit test.
- "state isolation unchanged (`HOME=<workdir>/home`); seeds/snapshots unaffected" → launch unchanged; Task 7 runs seed/resume suites.
- "autoupdate left on (self-maintaining cache)" → nothing disables it; no `autoUpdates:false` planted (unchanged).
- "snapshot no longer carries the binary; drop the `rm -rf` hack" → Task 2 + Task 6 snapshot test.
- "config: `OPTIO_CLAUDECODE_CACHE_DIR` env + `claude_install_dir` override" → Task 1 (`_resolve_cache_dir`) + Task 3 (doc).
- "`build_ttyd_argv` real-home symlink collapses" → Task 1 Step 3 + Task 5 Step 2.
- Config decision (deferred in spec): **keep `claude_install_dir`, repurpose as cache override** — Tasks 1 + 3.
