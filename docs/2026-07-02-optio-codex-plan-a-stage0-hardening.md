# optio-codex Plan A — Stage-0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every confirmed finding of the Stage-0 review (`docs/2026-07-02-optio-codex-stage0-review.md`) so optio-codex is a *sound* Stage 0: correct isolation, safe teardown, honest config, reference-grade tests, and the guide-required iframe demo task.

**Architecture:** No new subsystems. Surgical fixes to `packages/optio-codex` (host_actions, session, prompt, types, `__init__`), test-suite expansion to reference grade, root-Makefile test wiring, and one new demo-task module in `packages/optio-demo`. Later plans (B–E) add Stages 1–8; nothing here should anticipate them beyond honest error messages.

**Tech Stack:** Python ≥3.11, pytest + pytest-asyncio (asyncio_mode=auto), optio-core/host/agents driver stack, tmux + ttyd, MongoDB via the existing test fixtures (Docker mongod on localhost:27017).

## Global Constraints

- Worktree: `/home/csillag/deai/optio/.worktrees/csillag/optio-codex` — branch `csillag/optio-codex`. All paths below are relative to this worktree root unless absolute.
- Python env: use the worktree venv **only**: `.venv/bin/python` / `.venv/bin/pip`. NEVER `pip install` against the global interpreter. If `import optio_codex` fails at baseline, install editable: `.venv/bin/pip install -e packages/optio-codex`.
- Test command shape (run from worktree root): `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` (requires local MongoDB on `localhost:27017`; the suite already passed 9/9 in this environment — if Mongo is down: `cd packages/optio-demo && make deps-up`).
- Commit style: conventional commits (`fix(optio-codex): …`), one commit per task step marked "Commit". **NO `Co-Authored-By` lines** (user rule).
- SSOT rule (user's never-duplicate rule): protocol documentation and shared prompt framing must be *imported* from `optio-agents`, never copied. The one sanctioned per-wrapper copy is the 2-line `_SYSTEM_PREFIX_EXPLAINER` (both references carry their own; see Task 6).
- Reference implementations are the source of truth for patterns: `packages/optio-claudecode`, `packages/optio-opencode`. When this plan deliberately diverges from a reference, the task says so; do not "fix back" to reference behavior.
- Requires-python `>=3.11`; match the existing package's style (module-level `_LOG`, `shlex.quote` for every host-command interpolation, `from __future__ import annotations` where already present).
- Every task must leave the whole codex suite green before its commit.

**Verified non-issues (do NOT change):**
- `before_execute` firing at the end of `_prepare` matches claudecode (its `_plant_session_content`, which ends with `config.before_execute`, runs inside its `_prepare`, `packages/optio-claudecode/src/optio_claudecode/session.py:305,983`). opencode fires it inside its body instead. Codex keeps the claudecode placement; Task 2 adds a comment saying so.
- The shell-appended `DONE`/`ERROR`-on-exit semantics replicate claudecode (`packages/optio-claudecode/src/optio_claudecode/host_actions.py:661` area) — by design; Task 9 adds the missing *tests* for it, not a behavior change.

---

### Task 1: Baseline — environment sanity and green suite

**Files:**
- No source changes. Verification only.

**Interfaces:**
- Consumes: existing worktree venv `.venv/`.
- Produces: a recorded green baseline every later task diffs against.

- [ ] **Step 1: Verify the venv resolves the editable package**

Run: `.venv/bin/python -c "import optio_codex, optio_agents, optio_claudecode; print(optio_codex.__file__)"`
Expected: prints a path inside this worktree's `packages/optio-codex/src/`. If `ModuleNotFoundError` or the path points OUTSIDE this worktree, run `.venv/bin/pip install -e packages/optio-codex -e packages/optio-claudecode -e packages/optio-opencode` and re-check.

- [ ] **Step 2: Run the baseline suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: `9 passed`. If Mongo connection errors appear: `cd packages/optio-demo && make deps-up`, retry. Do not proceed on a red baseline.

*(No commit — nothing changed.)*

---

### Task 2: C1 + C2 — provision the per-task home tree and a per-task codex path

The isolated `<workdir>/home` is never created (codex launches into a nonexistent `$HOME` — critical C1), and teardown pkills the *shared* codex binary path, killing every codex on the host (critical C2). Both are fixed by one new provisioning step modeled on claudecode's cache/bin layout (`packages/optio-claudecode/src/optio_claudecode/host_actions.py:289-384`): create the home tree, then symlink the resolved codex binary to `<workdir>/home/.local/bin/codex` and launch/kill via that per-task path — the same precondition that makes claudecode's anchored pkill safe (its kill-target path is per-task, see its docstrings at `host_actions.py:904-918,946-957`).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (function `ensure_codex_installed`, ~line 74)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (`_prepare`, lines 47–67)
- Test: `packages/optio-codex/tests/test_host_actions.py`, `packages/optio-codex/tests/test_session_local.py`

**Interfaces:**
- Consumes: `resolve_codex(host, *, install_dir, install_if_missing) -> str` (unchanged), `_isolation_env(workdir) -> dict[str,str]` (unchanged), `HookContextProtocol._host`, `Host.run_command`.
- Produces: `ensure_codex_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` now returns the **per-task** path `<workdir>/home/.local/bin/codex` (previously the shared path). Later tasks (5, 9) rely on kill-scoping working against this path. New private helper `_provision_task_home(host) -> str` returning the per-task bin path.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
import re

import pytest

from optio_codex.host_actions import _codex_pgrep_pattern


class _RecordingHost:
    """Fake Host: records run_command calls, returns success."""

    def __init__(self, workdir="/w/task/workdir", stdout=""):
        self.workdir = workdir
        self.commands: list[str] = []
        self._stdout = stdout

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)

        class _R:
            stdout = self._stdout
            stderr = ""
            exit_code = 0

        return _R()


@pytest.mark.asyncio
async def test_provision_task_home_creates_tree_and_symlink():
    from optio_codex.host_actions import _provision_task_home

    host = _RecordingHost(workdir="/w/task/workdir")
    per_task = await _provision_task_home(host, shared_codex_path="/usr/local/bin/codex")
    assert per_task == "/w/task/workdir/home/.local/bin/codex"
    joined = " && ".join(host.commands)
    # Home tree: HOME itself, CODEX_HOME, bin dir, and the XDG dirs.
    for d in (
        "/w/task/workdir/home/.codex",
        "/w/task/workdir/home/.local/bin",
        "/w/task/workdir/home/.config",
        "/w/task/workdir/home/.local/share",
        "/w/task/workdir/home/.cache",
    ):
        assert d in joined
    assert "mkdir -p" in joined
    # Per-task launch path is a symlink to the shared binary (C2 precondition).
    assert "ln -sfn /usr/local/bin/codex /w/task/workdir/home/.local/bin/codex" in joined


def test_pgrep_pattern_scoped_to_per_task_path_only():
    """C2: the anchored pattern from THIS task's per-task path must not match
    a codex launched from the shared path or from ANOTHER task's path."""
    pattern = _codex_pgrep_pattern("/w/taskA/workdir/home/.local/bin/codex")
    # pkill/pgrep -f applies the pattern as a regex over the full cmdline.
    assert re.search(pattern, "/w/taskA/workdir/home/.local/bin/codex --sandbox workspace-write")
    assert not re.search(pattern, "/usr/local/bin/codex --sandbox workspace-write")
    assert not re.search(pattern, "/w/taskB/workdir/home/.local/bin/codex --sandbox workspace-write")
    # Self-match guard intact ([c]odex): the pattern string itself must not
    # contain the literal token 'codex' at the anchored tail.
    assert "[c]odex" in pattern
```

Append to `packages/optio-codex/tests/test_session_local.py` (extend the existing happy-path test — add the two marked lines to `after_execute` and the two assertions at the end):

```python
# In test_local_happy_path_done_in_optio_log, replace the after_execute
# definition and the final assertion block with:

    async def after_execute(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        log_path = workdir / "optio.log"
        observed["optio_log"] = log_path.read_text(encoding="utf-8")
        observed["agents_md"] = (workdir / "AGENTS.md").read_text(encoding="utf-8")
        observed["home_codex_isdir"] = (workdir / "home" / ".codex").is_dir()  # C1
        observed["per_task_codex"] = (
            workdir / "home" / ".local" / "bin" / "codex"
        ).exists()  # C2

    # ... existing task construction and execute unchanged ...

    assert "Hello from the test." in observed["agents_md"]
    assert "STATUS:" in observed["agents_md"]
    assert "DONE" in observed["optio_log"]
    assert observed["home_codex_isdir"] is True
    assert observed["per_task_codex"] is True
    assert captures.widget_upstream
    assert captures.widget_data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py packages/optio-codex/tests/test_session_local.py -q`
Expected: `test_provision_task_home_creates_tree_and_symlink` FAILS with `ImportError: cannot import name '_provision_task_home'`; `test_pgrep_pattern_scoped_to_per_task_path_only` PASSES already (the pattern logic is sound — the bug was the *path fed to it*; keep the test as the contract); `test_local_happy_path_done_in_optio_log` FAILS on `observed["per_task_codex"] is True`.

- [ ] **Step 3: Implement `_provision_task_home` and rewire `ensure_codex_installed`**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add after `_isolation_env` (line ~108):

```python
async def _provision_task_home(host: "Host", *, shared_codex_path: str) -> str:
    """Create the per-task isolation home tree and the per-task codex path.

    C1: codex must never launch into a nonexistent $HOME/$CODEX_HOME — the
    claudecode reference guarantees the tree via its install step
    (optio-claudecode host_actions.py:328-337); codex has no install step at
    Stage 0, so the tree is created explicitly here.

    C2: teardown pkills an anchored pattern on the codex path. That is only
    safe when the path is unique per task (see claudecode's
    _claude_pgrep_pattern docstring). The shared binary is therefore
    symlinked to <workdir>/home/.local/bin/codex and launched via that
    per-task path; the anchored pkill then reaches only this task's process.

    Returns the per-task launch path.
    """
    workdir = host.workdir.rstrip("/")
    home = f"{workdir}/home"
    bin_dir = f"{home}/.local/bin"
    per_task_codex = f"{bin_dir}/codex"
    dirs = [
        f"{home}/.codex",
        bin_dir,
        f"{home}/.config",
        f"{home}/.local/share",
        f"{home}/.cache",
    ]
    quoted = " ".join(shlex.quote(d) for d in dirs)
    r = await host.run_command(f"mkdir -p {quoted}")
    if r.exit_code != 0:
        raise RuntimeError(
            f"per-task home provisioning (mkdir -p) failed "
            f"(exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    r = await host.run_command(
        f"ln -sfn {shlex.quote(shared_codex_path)} {shlex.quote(per_task_codex)}"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"per-task codex symlink failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    return per_task_codex
```

Rewrite `ensure_codex_installed` (currently lines 74–85):

```python
async def ensure_codex_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Return the per-task launch path of the ``codex`` binary.

    Resolves the shared codex binary on the host (raising when absent —
    Stage 0 has no auto-install), provisions the per-task isolation home
    tree, and returns ``<workdir>/home/.local/bin/codex`` — a per-task
    symlink to the shared binary, so teardown's anchored pkill is scoped
    to this task only.
    """
    host = hook_ctx._host
    hook_ctx.report_progress(None, "Locating codex…")
    shared = await resolve_codex(
        host, install_dir=install_dir, install_if_missing=install_if_missing,
    )
    return await _provision_task_home(host, shared_codex_path=shared)
```

In `packages/optio-codex/src/optio_codex/session.py`, inside `_prepare` (lines 47–67) no call change is needed (`codex_path` now receives the per-task path transparently). Add one comment above the `config.before_execute` call (line 66):

```python
        if config.before_execute is not None:
            # End-of-prepare placement matches claudecode (its
            # _plant_session_content ends with before_execute, inside its
            # _prepare); opencode fires it inside the body instead.
            await config.before_execute(hook_ctx)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (11 tests now). Note: the fake-codex shim still works through the symlink chain because `codex-shim.sh` resolves itself with `readlink -f`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_host_actions.py packages/optio-codex/tests/test_session_local.py
git commit -m "fix(optio-codex): provision per-task home tree and kill-scoped codex path

C1: codex launched into a nonexistent \$HOME/\$CODEX_HOME — the isolation
env pointed at <workdir>/home but nothing created it.
C2: teardown pkilled the anchored SHARED codex path, killing every codex
on the host. The binary is now symlinked to <workdir>/home/.local/bin/codex
and launched/killed via that per-task path (the claudecode precondition)."
```

- [ ] **Step 6: Real-process kill-scoping test (C2 end-to-end)**

Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
import asyncio
import shutil
import subprocess


@pytest.mark.asyncio
async def test_kill_codex_processes_spares_other_tasks(tmp_path):
    """Launch two real processes from two per-task paths; killing task A's
    must leave task B's alive. Uses real pkill against unique tmp paths."""
    from optio_codex.host_actions import kill_codex_processes
    from optio_host.host import LocalHost

    sleep_bin = shutil.which("sleep")
    procs = []
    paths = []
    for task in ("a", "b"):
        bin_dir = tmp_path / task / "workdir" / "home" / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        codex = bin_dir / "codex"
        shutil.copy(sleep_bin, codex)
        paths.append(str(codex))
        procs.append(subprocess.Popen([str(codex), "30"]))
    try:
        taskdir_a = str(tmp_path / "a")
        host = LocalHost(taskdir=taskdir_a)
        await kill_codex_processes(host, paths[0])
        await asyncio.sleep(0.5)
        assert procs[0].poll() is not None, "task A's codex should be dead"
        assert procs[1].poll() is None, "task B's codex must survive"
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
```

- [ ] **Step 7: Run it, then the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q`
Expected: PASS. Then full suite: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` — all pass.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-codex/tests/test_host_actions.py
git commit -m "test(optio-codex): real-process proof that teardown kill is task-scoped"
```

---

### Task 3: M3 — make `~`-prefixed install dirs actually work

`types.py:93-98` documents `~/...` as valid, but every consumer `shlex.quote`s the literal tilde, so it can never resolve. Fix: expand against the host home (the host already exposes `resolve_host_home()`, used at `host_actions.py:36`).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`_resolve_install_dir` line 33, `resolve_codex` line 40)
- Test: `packages/optio-codex/tests/test_host_actions.py`

**Interfaces:**
- Consumes: `Host.resolve_host_home() -> str`.
- Produces: `async _expand_user_path(host, path) -> str`; `_resolve_install_dir` and `resolve_codex` accept `~`/`~/...` values.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
class _HomeHost(_RecordingHost):
    async def resolve_host_home(self):
        return "/home/worker"


@pytest.mark.asyncio
async def test_expand_user_path_tilde_forms():
    from optio_codex.host_actions import _expand_user_path

    host = _HomeHost()
    assert await _expand_user_path(host, "~/bin") == "/home/worker/bin"
    assert await _expand_user_path(host, "~") == "/home/worker"
    assert await _expand_user_path(host, "/abs/path") == "/abs/path"
    with pytest.raises(ValueError):
        await _expand_user_path(host, "~otheruser/bin")


@pytest.mark.asyncio
async def test_resolve_codex_expands_tilde_install_dir():
    from optio_codex.host_actions import resolve_codex

    host = _HomeHost(stdout="OK")
    path = await resolve_codex(host, install_dir="~/tools")
    assert path == "/home/worker/tools/codex"
    # The probe command must carry the EXPANDED path, not a quoted literal '~'.
    assert any("/home/worker/tools/codex" in c for c in host.commands)
    assert not any("'~" in c for c in host.commands)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q`
Expected: both new tests FAIL (`ImportError` / literal-tilde probe).

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add above `_resolve_install_dir`:

```python
async def _expand_user_path(host: "Host", path: str) -> str:
    """Expand a leading ``~``/``~/`` against the HOST's home directory.

    Downstream consumers shlex-quote every path, which defeats shell tilde
    expansion — so a documented-valid ``~/bin`` override must be expanded
    here, against the worker's home (never the engine's). ``~user`` forms
    are rejected: resolving another user's home host-side is not supported.
    """
    if path == "~" or path.startswith("~/"):
        home = (await host.resolve_host_home()).rstrip("/")
        return home if path == "~" else f"{home}/{path[2:]}"
    if path.startswith("~"):
        raise ValueError(
            f"install dir {path!r}: '~user' paths are not supported; use an "
            f"absolute path or plain '~/'."
        )
    return path
```

Change `_resolve_install_dir` to:

```python
async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    if install_dir is not None:
        return await _expand_user_path(host, install_dir)
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"
```

In `resolve_codex`, change the `install_dir is not None` branch's first line from
`candidate = f"{install_dir.rstrip('/')}/codex"` to:

```python
        install_dir = await _expand_user_path(host, install_dir)
        candidate = f"{install_dir.rstrip('/')}/codex"
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_host_actions.py
git commit -m "fix(optio-codex): expand ~-prefixed install dirs against the host home

The config validator documents '~/...' as valid, but every consumer
shlex-quotes the path, so the shell never expanded it — a tilde
install_dir could never resolve codex and made the ttyd installer
mkdir a literal '~' directory."
```

---

### Task 4: Engine-PATH bug — compose PATH on the host, not the engine

`_build_codex_shell_command` (host_actions.py:124-126) bakes the *engine's* `os.environ["PATH"]` into the host command. claudecode has the same latent pattern (`optio-claudecode/host_actions.py:597-598`) — this is a **deliberate divergence** fixed codex-side (upstream candidate for claudecode later; do not change claudecode in this plan). PATH is now composed inside the bash payload on the host, so remote hosts (Stage 1) inherit *their own* PATH.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`_build_codex_shell_command`, lines 111–147)
- Test: `packages/optio-codex/tests/test_host_actions.py` (existing tests updated)

**Interfaces:**
- Consumes/Produces: `_build_codex_shell_command(*, codex_path, workdir, extra_env, codex_flags) -> tuple[list[str], str]` — signature unchanged; `env_assignments` no longer contains `PATH=` (callers only join them into the command; `build_tmux_session_argv` consumes only the command string — verified, no other consumer of the first element).

- [ ] **Step 1: Update the tests to the new contract (they will fail first)**

In `packages/optio-codex/tests/test_host_actions.py`, replace `test_build_shell_command_uses_isolation_env` with:

```python
def test_build_shell_command_composes_path_on_host():
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task", extra_env=None,
        codex_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    # PATH must NOT be baked in from the engine environment…
    assert not any(a.startswith("PATH=") for a in env)
    # …it is composed on the HOST inside the bash payload instead.
    assert 'export PATH=/w/task/home/.local/bin:"$PATH"' in cmd


def test_build_shell_command_honors_extra_env_path_override():
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task",
        extra_env={"PATH": "/custom/bin"}, codex_flags=[],
    )
    assert "export PATH=/w/task/home/.local/bin:/custom/bin" in cmd
    assert not any(a.startswith("PATH=") for a in env)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_host_actions.py -q`
Expected: both FAIL (engine PATH still baked in).

- [ ] **Step 3: Implement**

Replace the body of `_build_codex_shell_command` (host_actions.py:111-147) with:

```python
def _build_codex_shell_command(
    *,
    codex_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    codex_flags: list[str],
) -> tuple[list[str], str]:
    workdir_clean = workdir.rstrip("/")
    iso = _isolation_env(workdir_clean)
    home_dir = iso["HOME"]
    home_local_bin = f"{home_dir}/.local/bin"

    extra = dict(extra_env or {})
    # PATH is composed on the HOST inside the bash payload (below), never
    # baked in from the engine's os.environ — the command may run on a
    # remote worker whose PATH differs. Deliberate divergence from the
    # claudecode template, which still bakes the engine PATH in.
    path_override = extra.pop("PATH", None)
    env_assignments: list[str] = [f"{k}={v}" for k, v in iso.items()]
    for k, v in extra.items():
        env_assignments.append(f"{k}={v}")

    codex_argv = " ".join(shlex.quote(c) for c in [codex_path, *codex_flags])
    log_path = f"{workdir_clean}/optio.log"

    if path_override is not None:
        path_expr = f"export PATH={home_local_bin}:{path_override}; "
    else:
        path_expr = f'export PATH={home_local_bin}:"$PATH"; '
    bash_payload = (
        f"{path_expr}"
        f"cd {shlex.quote(workdir_clean)} && {codex_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: codex exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (integration tests still green — locally `"$PATH"` resolves to the same engine PATH, so shim resolution is unaffected).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_host_actions.py
git commit -m "fix(optio-codex): compose launch PATH on the host, not the engine

The tmux launch command baked the controlling engine's os.environ PATH
into a command that may run on a remote worker. PATH is now exported
inside the bash payload host-side (deliberate divergence from the
claudecode template, which carries the same latent issue)."
```

---

### Task 5: Launch/teardown robustness — return `tmux_path`, orphan-ttyd reap

Two fixes: (a) `launch_ttyd_with_codex` already resolves tmux; return it so `session.py` stops re-resolving *after* launch (`session.py:101`) and the teardown guard covers more partial-launch states. (b) Port claudecode's orphan-ttyd reap (`_socket_pkill_pattern` + `_kill_ttyd_by_socket`, `optio-claudecode/host_actions.py:921-943`) and its `else` branch in `teardown_session_tree` (`:1014-1023`), which codex dropped.

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`launch_ttyd_with_codex` line 333, `teardown_session_tree` line 442)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (`_codex_body` lines 90–101, finally-guard lines 136–142)
- Test: new `packages/optio-codex/tests/test_teardown_session_tree.py`, new `packages/optio-codex/tests/test_await_codex_gone.py`, new `packages/optio-codex/tests/test_kill_ttyd_by_socket.py`

**Interfaces:**
- Consumes: existing `_kill_tmux_session`, `kill_codex_processes`, `await_codex_gone`, `Host.terminate_subprocess`.
- Produces: `launch_ttyd_with_codex(...) -> tuple[ProcessHandle, str, int, str, str]` — now `(handle, tmux_path, port, socket, session)`; `_socket_pkill_pattern(socket_path) -> str`; `_kill_ttyd_by_socket(host, socket_path) -> None`; `teardown_session_tree` gains the orphan branch (signature unchanged). Task 9's cancellation test relies on teardown working through this path.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-codex/tests/test_teardown_session_tree.py` (adapted from `packages/optio-claudecode/tests/test_teardown_session_tree.py`):

```python
import pytest

import optio_codex.host_actions as H


@pytest.fixture
def calls(monkeypatch):
    rec = []

    async def _kill_ttyd(host, socket):
        rec.append(("ttyd_socket", socket))

    async def _kill_session(host, tmux_path, socket, session):
        rec.append(("kill_session", session))

    async def _kill_codex(host, codex_path, **kw):
        rec.append(("kill_codex", codex_path))

    async def _await_gone(host, codex_path, **kw):
        rec.append(("await_gone", codex_path))
        return True

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _kill_ttyd)
    monkeypatch.setattr(H, "_kill_tmux_session", _kill_session)
    monkeypatch.setattr(H, "kill_codex_processes", _kill_codex)
    monkeypatch.setattr(H, "await_codex_gone", _await_gone)
    return rec


class _FakeHandle:
    pass


class _Host:
    def __init__(self):
        self.terminated = []

    async def terminate_subprocess(self, handle, *, aggressive):
        self.terminated.append((handle, aggressive))


@pytest.mark.asyncio
async def test_orphan_branch_uses_kill_ttyd_by_socket(calls):
    host = _Host()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=None, aggressive=True,
    )
    assert [c[0] for c in calls] == [
        "ttyd_socket", "kill_session", "kill_codex", "await_gone",
    ]
    assert host.terminated == []


@pytest.mark.asyncio
async def test_handle_branch_uses_terminate_subprocess(calls):
    host = _Host()
    handle = _FakeHandle()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=handle, aggressive=False,
    )
    assert host.terminated == [(handle, False)]
    assert [c[0] for c in calls] == ["kill_session", "kill_codex", "await_gone"]


@pytest.mark.asyncio
async def test_steps_are_best_effort(calls, monkeypatch):
    async def _boom(host, socket):
        raise RuntimeError("ttyd kill blew up")

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _boom)
    host = _Host()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=None, aggressive=True,
    )
    assert [c[0] for c in calls] == ["kill_session", "kill_codex", "await_gone"]
```

Create `packages/optio-codex/tests/test_kill_ttyd_by_socket.py`:

```python
import pytest

import optio_codex.host_actions as H


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    def __init__(self):
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        return _Result()


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_anchored_pkill():
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cx-deadbeef0badcafe.sock")
    assert len(host.commands) == 1
    cmd = host.commands[0]
    assert "pkill" in cmd
    assert "/tmp/optio-cx-deadbeef0badcafe.sock" in cmd
    assert "|| true" in cmd


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_does_not_self_match():
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cx-abc123.sock")
    cmd = host.commands[0]
    assert "[t]tyd" in cmd
```

Create `packages/optio-codex/tests/test_await_codex_gone.py` (adapted from `packages/optio-claudecode/tests/test_await_claude_gone.py`):

```python
import pytest

import optio_codex.host_actions as H


class _Result:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    """Returns successive pgrep outputs from ``seq`` (last value repeats)."""

    def __init__(self, seq):
        self.seq = seq
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        i = min(len(self.commands), len(self.seq) - 1)
        self.commands.append(cmd)
        return _Result(self.seq[i])


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    sleeps = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(H.asyncio, "sleep", _fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_waits_until_gone_then_returns_true(_no_real_sleep):
    host = _Host(["12345\n", "12345\n", ""])
    ok = await H.await_codex_gone(
        host, "/w/home/.local/bin/codex", poll_s=1.0, timeout_s=10.0,
    )
    assert ok is True
    assert len(_no_real_sleep) == 2
    assert all("/w/home/.local/bin/[c]odex" in c for c in host.commands)


@pytest.mark.asyncio
async def test_returns_false_on_timeout(_no_real_sleep):
    host = _Host(["999\n"])
    ok = await H.await_codex_gone(
        host, "/w/home/.local/bin/codex", poll_s=1.0, timeout_s=3.0,
    )
    assert ok is False
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_teardown_session_tree.py packages/optio-codex/tests/test_kill_ttyd_by_socket.py packages/optio-codex/tests/test_await_codex_gone.py -q`
Expected: teardown orphan test FAILS (`AttributeError: … has no attribute '_kill_ttyd_by_socket'`); kill-ttyd tests FAIL (missing function); await_codex_gone tests PASS (already implemented — they are pattern-regression coverage; keep them).

- [ ] **Step 3: Implement**

In `packages/optio-codex/src/optio_codex/host_actions.py`, add after `_kill_tmux_session` (line ~407) — ported from `optio-claudecode/host_actions.py:921-943`:

```python
def _socket_pkill_pattern(socket_path: str) -> str:
    """Anchored pkill -f pattern matching the orphan ttyd carrying
    ``socket_path`` in its cmdline (``ttyd … -- tmux -S <socket> attach``).
    ``[t]tyd`` keeps pkill's own argv from self-matching; the verbatim
    socket path scopes the match to this task's private socket."""
    if not socket_path:
        return socket_path
    return f"[t]tyd.*{socket_path}"


async def _kill_ttyd_by_socket(host: "Host", socket_path: str) -> None:
    """Reap a detached orphan ttyd that has no tracked launch handle.

    Normal teardown kills ttyd via ``terminate_subprocess(handle)``; a crash
    orphan's ttyd is re-parented to init with no handle, so it is reaped
    host-side by an anchored ``pkill -f`` on its private socket path.
    Best-effort: pkill exits non-zero when nothing matches."""
    pattern = _socket_pkill_pattern(socket_path)
    await host.run_command(f"pkill -KILL -f {shlex.quote(pattern)} || true")
```

In `teardown_session_tree` (line ~442), replace the ttyd block:

```python
    if ttyd_handle is not None:
        try:
            await host.terminate_subprocess(ttyd_handle, aggressive=aggressive)
        except Exception:
            _LOG.exception("terminate_subprocess (ttyd) failed")
    else:
        try:
            await _kill_ttyd_by_socket(host, tmux_socket)
        except Exception:
            _LOG.exception("orphan ttyd reap failed (socket=%s)", tmux_socket)
```

Change `launch_ttyd_with_codex`'s return type annotation to `"tuple[ProcessHandle, str, int, str, str]"`, and its final line to:

```python
    return handle, tmux_path, port, socket_path, session_name
```

(also update its docstring: `Returns (ttyd_handle, tmux_path, port, socket_path, session_name)`).

In `packages/optio-codex/src/optio_codex/session.py`:
- `_codex_body`: change the unpack (line 90) to
  `handle, tmux_path_local, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_codex(…)`,
  then set `tmux_path = tmux_path_local` on the next line, extend the `nonlocal` on line 70 to include `tmux_path`, and DELETE line 101 (`tmux_path = await host_actions._require_tmux(host)`).
- Finally-guard (lines 136–142): drop the `launched_handle is not None` condition (teardown now handles `ttyd_handle=None` via the orphan reap):

```python
        if (
            tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and codex_path
        ):
```

(pass `ttyd_handle=launched_handle` as before — it may legitimately be `None`.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_teardown_session_tree.py packages/optio-codex/tests/test_kill_ttyd_by_socket.py packages/optio-codex/tests/test_await_codex_gone.py
git commit -m "fix(optio-codex): orphan-ttyd reap + tmux_path from launch for wider teardown coverage

Ports claudecode's kill-ttyd-by-socket orphan branch (dropped in the
original copy) and returns tmux_path from launch_ttyd_with_codex so the
teardown guard no longer depends on a post-launch re-resolve."
```

---

### Task 6: Prompt SSOT — import the shared composer, fix `host_protocol=False`

`prompt.py` duplicates `BASE_PROMPT_POST`/intro from `optio_agents.prompt` (never-duplicate violation) and rebuilds `ProtocolFeatures` independently of the session's protocol (drift risk). Rewire like the references: session passes `protocol.documentation` (claudecode `session.py:955-957`, opencode `session.py:234-236`); prompt forwards to `optio_agents.prompt.compose_agents_md` (see claudecode `prompt.py:127-196`). `host_protocol=False` gains the `System:` explainer (per-wrapper copy is the established pattern: claudecode `prompt.py:29-32`, opencode `prompt.py:24`).

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/prompt.py` (full rewrite)
- Modify: `packages/optio-codex/src/optio_codex/session.py` (`_prepare`'s `compose_agents_md` call, lines 59–65)
- Test: `packages/optio-codex/tests/test_prompt.py`

**Interfaces:**
- Consumes: `optio_agents.prompt.compose_agents_md(consumer_instructions, *, documentation, resume_section=None) -> str`; `Protocol.documentation` (from `get_protocol(...)`).
- Produces: `compose_agents_md(consumer_instructions, *, documentation: str | None = None, host_protocol: bool = True) -> str` (new `documentation` kwarg; existing kwargs preserved).

- [ ] **Step 1: Write the failing tests**

Replace `packages/optio-codex/tests/test_prompt.py` with:

```python
from optio_agents import get_protocol
from optio_agents.prompt import BASE_PROMPT_POST

from optio_codex.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md


def test_documentation_threads_from_session_protocol():
    """SSOT: the session's protocol documentation is the one that lands in
    AGENTS.md — and the standalone default must render identically, so the
    two construction sites cannot drift."""
    protocol = get_protocol(browser="suppress")
    threaded = compose_agents_md("X", documentation=protocol.documentation)
    defaulted = compose_agents_md("X")
    assert threaded == defaulted
    assert protocol.documentation in threaded


def test_shared_framing_is_imported_not_copied():
    md = compose_agents_md("X")
    assert BASE_PROMPT_POST in md  # the optio-agents SSOT copy, verbatim


def test_host_protocol_false_adds_system_explainer():
    md = compose_agents_md("X", host_protocol=False)
    assert "STATUS:" not in md
    assert "System:" in md  # the explainer replaces the protocol docs
    assert "X" in md
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_prompt.py -q`
Expected: FAIL — `compose_agents_md` has no `documentation` kwarg; explainer missing.

- [ ] **Step 3: Rewrite `packages/optio-codex/src/optio_codex/prompt.py`**

```python
"""AGENTS.md composition for optio-codex.

Codex reads an ``AGENTS.md`` file in its workdir. The shared framing and
the keyword-protocol documentation are owned by ``optio-agents`` (the
prompt SSOT); this module only threads codex's protocol mode through.
"""

from optio_agents.prompt import compose_agents_md as _compose_agents_md_host
from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt


# Self-contained System: explainer for sessions without the keyword-protocol
# docs (which normally explain the convention). Per-wrapper copy is the
# established pattern (claudecode/opencode each carry their own).
_SYSTEM_PREFIX_EXPLAINER = """\
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""


def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str | None = None,
    host_protocol: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="suppress").documentation``. Defaults (for unit
    tests / standalone callers) to codex's ``suppress`` docs. It must
    always come from the session's ``Protocol`` where one exists — never
    rebuild features at a second site.

    ``host_protocol=False`` omits the keyword-protocol documentation and
    instead includes a self-contained ``System:`` message explainer
    (guide Part 2D). Stage 0's iframe mode always runs with
    ``host_protocol=True`` (validated in ``CodexTaskConfig``); the False
    branch serves conversation mode in a later stage.
    """
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser="suppress")
            )
        return _compose_agents_md_host(
            consumer_instructions, documentation=documentation,
        )
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=None,
        resume_section=_SYSTEM_PREFIX_EXPLAINER,
    )
```

In `packages/optio-codex/src/optio_codex/session.py`, change the `compose_agents_md` call inside `_prepare` (lines 59–65) to:

```python
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
            ),
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass. (The session integration test's `"STATUS:" in agents_md` assertion still holds — the shared composer includes the same protocol docs.)

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/prompt.py packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_prompt.py
git commit -m "refactor(optio-codex): compose AGENTS.md via the optio-agents prompt SSOT

Drops the copied BASE_PROMPT_POST/intro and the second ProtocolFeatures
construction; the session's protocol.documentation now threads through
(matching both references). host_protocol=False gains the System:
explainer required by guide Part 2D."
```

---

### Task 7: Config honesty — exports, `install_if_missing` wording

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/__init__.py`
- Modify: `packages/optio-codex/src/optio_codex/host_actions.py` (`resolve_codex` error text, lines 64–71)
- Test: `packages/optio-codex/tests/test_import.py`, `packages/optio-codex/tests/test_host_actions.py`

**Interfaces:**
- Produces: `optio_codex.IframeMode`, `optio_codex.ApprovalPolicy`, `optio_codex.SandboxMode` importable at package top level.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-codex/tests/test_import.py`:

```python
def test_vocabulary_literals_exported():
    from optio_codex import ApprovalPolicy, IframeMode, SandboxMode  # noqa: F401
```

Append to `packages/optio-codex/tests/test_host_actions.py`:

```python
@pytest.mark.asyncio
async def test_resolve_codex_missing_names_the_stage_gap():
    class _NotFoundHost(_RecordingHost):
        async def run_command(self, cmd, **kwargs):
            self.commands.append(cmd)

            class _R:
                stdout = ""
                stderr = ""
                exit_code = 1

            return _R()

    host = _NotFoundHost()
    with pytest.raises(RuntimeError, match="binary cache"):
        await resolve_codex(host, install_if_missing=True)
```

(add `from optio_codex.host_actions import resolve_codex` to the imports if not present from Task 3.)

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_import.py packages/optio-codex/tests/test_host_actions.py -q`
Expected: both new tests FAIL.

- [ ] **Step 3: Implement**

`packages/optio-codex/src/optio_codex/__init__.py`: extend the `from optio_codex.types import (…)` block with `ApprovalPolicy, IframeMode, SandboxMode` and add the three names to `__all__`.

`resolve_codex` (host_actions.py, lines 64–71): replace the two raise blocks' messages with:

```python
    if not install_if_missing:
        raise RuntimeError(
            "codex not found on host and install_if_missing=False; nothing to do."
        )
    raise RuntimeError(
        "codex not found on the worker (looked via 'command -v codex'). "
        "install_if_missing is accepted but Stage 0 ships no auto-install — "
        "the optio-owned binary cache arrives in a later stage. Install codex "
        "manually (npm i -g @openai/codex) or pass codex_install_dir."
    )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/__init__.py packages/optio-codex/src/optio_codex/host_actions.py packages/optio-codex/tests/test_import.py packages/optio-codex/tests/test_host_actions.py
git commit -m "fix(optio-codex): export vocabulary Literals; honest install_if_missing error"
```

---

### Task 8: Guard the untested `ssh` path behind a clear Stage-0 error

`CodexTaskConfig.ssh` is silently routed to `RemoteHost` while the README declares remote unsupported — and Task 4 aside, the remote path has zero test coverage. Fail fast with an actionable message. **Plan B (Stage 1) removes this guard.**

**Files:**
- Modify: `packages/optio-codex/src/optio_codex/session.py` (top of `run_codex_session`, line 33)
- Test: `packages/optio-codex/tests/test_config.py`

**Interfaces:**
- Produces: `run_codex_session` raises `NotImplementedError` when `config.ssh is not None`.

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-codex/tests/test_config.py`:

```python
@pytest.mark.asyncio
async def test_ssh_config_rejected_at_stage0():
    from optio_codex import SSHConfig
    from optio_codex.session import run_codex_session

    config = CodexTaskConfig(
        consumer_instructions="x",
        ssh=SSHConfig(host="worker.example", user="u", key_path="/k"),
    )
    with pytest.raises(NotImplementedError, match="remote"):
        await run_codex_session(None, config)
```

(If `SSHConfig` requires different constructor fields, check `packages/optio-host/src/optio_host/types.py` and use its actual required fields — keep the test minimal.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_config.py -q`
Expected: FAIL (no exception raised before host build).

- [ ] **Step 3: Implement**

At the top of `run_codex_session` (session.py:33), insert as the first statement:

```python
    if config.ssh is not None:
        raise NotImplementedError(
            "optio-codex Stage 0 supports the local host only; remote (SSH) "
            "sessions arrive with the Stage 1 work. Remove the ssh field or "
            "wait for optio-codex >= 0.2."
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/session.py packages/optio-codex/tests/test_config.py
git commit -m "fix(optio-codex): reject ssh config with a clear Stage-0 error

The RemoteHost path was silently accepted but untested and README-declared
unsupported. Stage 1 removes this guard."
```

---

### Task 9: Behavioral coverage — exit-status DONE/ERROR channel and cancellation

The shell-appended `rc=$? → DONE/ERROR` channel (M5) and the whole cancellation path (M6) have zero behavioral coverage. Add fake-codex scenarios that *exit* (today's scenarios sleep forever) and a cancellation test that flips the fixture's `cancellation_flag`, then assert teardown actually reaped the tree. Also strengthen the deliverable/error assertions (payload + message) to reference level.

**Files:**
- Modify: `packages/optio-codex/tests/fake_codex.py` (new scenarios: `exit_zero`, `exit_nonzero`, `long`)
- Modify: `packages/optio-codex/tests/test_session_local.py`
- Test: same files.

**Interfaces:**
- Consumes: `ctx_and_captures` fixture's third element (`cancellation_flag: asyncio.Event`, `conftest.py:118`); `optio_host.paths.task_dir`; `optio_codex.host_actions._tmux_socket_path`; `LocalHost(taskdir=…)`.
- Produces: `FAKE_CODEX_SCENARIO` values `exit_zero` / `exit_nonzero` / `long`.

- [ ] **Step 1: Extend the fake agent**

In `packages/optio-codex/tests/fake_codex.py`: change `SCENARIOS` to
`("happy", "deliverable", "error", "exit_zero", "exit_nonzero", "long")`, add:

```python
def _scenario_exit_zero() -> None:
    # Exits 0 WITHOUT writing DONE itself: the wrapper's shell payload must
    # append DONE (the exit-status channel, host_actions rc-branch).
    time.sleep(0.05)
    _log("STATUS: 50% about to exit cleanly")


def _scenario_exit_nonzero() -> None:
    # Exits 3 — the shell payload must append 'ERROR: codex exited 3'.
    time.sleep(0.05)
    _log("STATUS: 50% about to crash")
    raise SystemExit(3)


def _scenario_long() -> None:
    # Never finishes — for the cancellation test.
    _log("STATUS: 10% running until cancelled")
    time.sleep(600.0)
```

and register them in `main()`'s dispatch dict:

```python
    {
        "happy": _scenario_happy,
        "deliverable": _scenario_deliverable,
        "error": _scenario_error,
        "exit_zero": _scenario_exit_zero,
        "exit_nonzero": _scenario_exit_nonzero,
        "long": _scenario_long,
    }[scenario]()
```

- [ ] **Step 2: Write the failing tests**

Append to `packages/optio-codex/tests/test_session_local.py`:

```python
import asyncio

from optio_codex.types import CodexTaskConfig as _Cfg  # noqa: F401 (see below)


@pytest.mark.asyncio
async def test_exit_zero_appends_done_via_shell_channel(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    """M5: codex exiting 0 without writing DONE itself → the launch shell's
    rc-branch appends DONE and the session completes clean."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "exit_zero")
    observed = {}

    async def after_execute(hook_ctx):
        log = pathlib.Path(hook_ctx._host.workdir) / "optio.log"
        observed["optio_log"] = log.read_text(encoding="utf-8")

    task = create_codex_task(
        process_id="codex-exit-zero", name="z",
        config=CodexTaskConfig(
            consumer_instructions="exit cleanly",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
            after_execute=after_execute,
        ),
    )
    await task.execute(ctx)
    assert "DONE" in observed["optio_log"]


@pytest.mark.asyncio
async def test_exit_nonzero_appends_error_and_raises(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "exit_nonzero")
    task = create_codex_task(
        process_id="codex-exit-nonzero", name="n",
        config=CodexTaskConfig(
            consumer_instructions="crash",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )
    with pytest.raises(RuntimeError, match="codex exited 3"):
        await task.execute(ctx)


@pytest.mark.asyncio
async def test_cancellation_returns_clean_and_tears_down(
    shim_install_dir, task_root, ctx_and_captures, monkeypatch,
):
    """M6: setting the cancellation flag mid-run returns clean (no raise)
    and the tmux session + codex process are gone afterwards."""
    from optio_codex import host_actions
    from optio_host.host import LocalHost
    from optio_host.paths import task_dir

    ctx, captures, cancellation_flag = ctx_and_captures
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "long")

    process_id = "codex-cancel"
    task = create_codex_task(
        process_id=process_id, name="c",
        config=CodexTaskConfig(
            consumer_instructions="run forever",
            codex_install_dir=str(shim_install_dir),
            ttyd_install_dir=str(shim_install_dir),
        ),
    )

    async def _cancel_when_running():
        for _ in range(200):  # up to 20s
            if any("Codex is live" == m for _, m in captures.progress):
                break
            await asyncio.sleep(0.1)
        cancellation_flag.set()

    canceller = asyncio.create_task(_cancel_when_running())
    await task.execute(ctx)  # must NOT raise: cancellation is a clean return
    await canceller

    # Teardown proof: the per-task tmux session no longer exists.
    taskdir = task_dir(ssh=None, process_id=process_id, consumer_name="optio-codex")
    host = LocalHost(taskdir=taskdir)
    socket_path = host_actions._tmux_socket_path(host)
    tmux = shutil.which("tmux")
    alive = await host_actions.tmux_session_alive(host, tmux, socket_path, "optio")
    assert alive is False
```

(add `import shutil` to the test module's imports.)

Also strengthen the two existing tests in place:
- `test_local_deliverable_callback_fired`: after `assert len(captured) == 1`, add

```python
    path, text = captured[0]
    assert path.endswith("greeting.txt")
    assert text == "hello from fake codex\n"
```

- `test_local_error_raises`: change to

```python
    with pytest.raises(RuntimeError, match="scenario asked for failure"):
        await task.execute(ctx)
```

Remove the stray `from optio_codex.types import CodexTaskConfig as _Cfg` line from the snippet above if unused (it is — do not add it).

- [ ] **Step 3: Run to verify failures/passes**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/test_session_local.py -q`
Expected: the three NEW tests initially fail only if the behavior is broken — they are *characterization* tests of already-implemented behavior, so they may all PASS immediately. That is acceptable here (the review demanded coverage, not behavior change); what must NOT happen is an error/hang. If `test_cancellation_returns_clean_and_tears_down` hangs > 60s, the cancellation path is genuinely broken — debug before proceeding (check that `_watch_cancellation` sees the flag and that teardown's `aggressive=True` path runs).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: all pass (now ~24 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/tests/fake_codex.py packages/optio-codex/tests/test_session_local.py
git commit -m "test(optio-codex): behavioral coverage for exit-status DONE/ERROR and cancellation

Fake scenarios that actually exit (0 / nonzero) exercise the launch
shell's rc-branch; a cancellation test flips the fixture flag and proves
clean return + tmux teardown. Deliverable payload and error message
assertions raised to reference level."
```

---

### Task 10: Wire the suite into the repo — root Makefile `PY_PACKAGES`

**Files:**
- Modify: `Makefile` (line 4: `PY_PACKAGES := optio-core optio-host optio-agents optio-opencode` — note it currently omits optio-claudecode too; add ONLY optio-codex, do not "fix" claudecode's absence here, it may be deliberate — flag it in the commit body instead)

- [ ] **Step 1: Edit**

Change line 4 to:

```make
PY_PACKAGES := optio-core optio-host optio-agents optio-opencode optio-codex
```

- [ ] **Step 2: Verify the test loop picks it up**

Run: `make -n test | grep codex`
Expected: the per-package loop lines include `packages/optio-codex`.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build: run optio-codex tests from the root test target

Note: optio-claudecode is also absent from PY_PACKAGES; left untouched
here (out of scope; likely gated on its heavier fixtures)."
```

---

### Task 11: Stage-0 iframe demo task (guide Part 5, Appendix A #29 — iframe leg)

One static iframe demo task in optio-demo, mirroring the claudecode demo's hook walkthrough (`packages/optio-demo/src/optio_demo/tasks/claudecode.py`) minus everything seed-related (seeds arrive in Plan C; the seed-setup and conversation legs of the demo trio land there / in Plan D). Interim auth: `OPTIO_CODEX_DEMO_OPENAI_API_KEY` env passthrough, else the operator runs `codex login` in the embedded terminal.

**Files:**
- Create: `packages/optio-demo/src/optio_demo/tasks/codex.py`
- Modify: `packages/optio-demo/src/optio_demo/tasks/__init__.py`

**Interfaces:**
- Consumes: `optio_codex.create_codex_task`, `CodexTaskConfig`, `optio_demo.tasks._feedback.make_feedback_on_deliverable` (read its signature in `packages/optio-demo/src/optio_demo/tasks/_feedback.py` before use — the claudecode demo calls it; mirror that call shape exactly).
- Produces: `async def get_tasks(services) -> list[TaskInstance]` aggregated in `get_task_definitions`.

- [ ] **Step 1: Read the conventions**

Read fully: `packages/optio-demo/src/optio_demo/tasks/claudecode.py` (hook walkthrough + task construction) and `packages/optio-demo/src/optio_demo/tasks/_feedback.py`. Mirror naming/prefix conventions.

- [ ] **Step 2: Create `packages/optio-demo/src/optio_demo/tasks/codex.py`**

```python
"""Reference demo task for optio-codex — Stage 0 (iframe/ttyd, local).

One static task that embeds the Codex TUI in the dashboard via ttyd and
exercises the full hook walkthrough (before_execute file ship,
deliverable callback, after_execute log readback).

Authentication (Stage 0 — no seeds yet): codex runs under HOME-isolation
(<workdir>/home), so the host user's ~/.codex is NOT inherited. Either
export ``OPTIO_CODEX_DEMO_OPENAI_API_KEY`` before starting the demo (it
is passed into the session env as ``OPENAI_API_KEY``), or run
``codex login`` interactively inside the embedded terminal after launch.
Seed-based provisioning (log in once, reuse everywhere) arrives with the
optio-codex seeds stage, which also completes the demo trio
(seed-setup + seed-pinned iframe + seed-pinned conversation).
"""

from __future__ import annotations

import os

from optio_codex import CodexTaskConfig, HookContext, create_codex_task
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


async def _before_execute(hook_ctx: HookContext) -> None:
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


async def _after_execute(hook_ctx: HookContext) -> None:
    log = await hook_ctx.read_text_from_host("optio.log")
    lines = [ln for ln in log.splitlines() if ln.strip()]
    hook_ctx.report_progress(None, f"optio.log carried {len(lines)} line(s)")


def _demo_env() -> dict[str, str] | None:
    api_key = os.environ.get("OPTIO_CODEX_DEMO_OPENAI_API_KEY")
    return {"OPENAI_API_KEY": api_key} if api_key else None


async def get_tasks(services) -> list[TaskInstance]:
    return [
        create_codex_task(
            process_id="codex-demo-iframe",
            name="Codex demo — iframe",
            description=(
                "OpenAI Codex TUI embedded via ttyd (Stage 0, local). "
                "Auth: set OPTIO_CODEX_DEMO_OPENAI_API_KEY, or run "
                "`codex login` in the terminal after launch."
            ),
            config=CodexTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_demo_env(),
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=make_feedback_on_deliverable("codex"),
            ),
        ),
    ]
```

**NOTE:** verify each hook helper against reality before committing: `hook_ctx.copy_file` / `read_text_from_host` signatures (`packages/optio-agents/src/optio_agents/context.py`) and `make_feedback_on_deliverable`'s exact signature (the claudecode demo's call is the template — copy its call shape, including any extra args). Adjust the snippet to match what the claudecode demo actually does; the *structure* above is the contract, the helper call shapes come from the references.

- [ ] **Step 3: Aggregate**

In `packages/optio-demo/src/optio_demo/tasks/__init__.py` add the import
`from optio_demo.tasks.codex import get_tasks as codex_tasks`
and add `*await codex_tasks(services),` after the claudecode entry in the returned list.

- [ ] **Step 4: Verify importability + suite**

Run: `.venv/bin/python -c "import asyncio; from optio_demo.tasks import get_task_definitions; print('ok')"`
Expected: `ok` (if `optio_demo` is not installed in this venv: `.venv/bin/pip install -e packages/optio-demo` first — it depends on optio-codex, already editable).
Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/codex.py packages/optio-demo/src/optio_demo/tasks/__init__.py
git commit -m "feat(optio-demo): codex Stage-0 iframe demo task

Guide Part 5's iframe leg (Appendix A #29). Interim auth via
OPTIO_CODEX_DEMO_OPENAI_API_KEY passthrough or interactive codex login
in the embedded terminal; the seed trio completes with the seeds stage."
```

---

### Task 12: README truth-up

**Files:**
- Modify: `packages/optio-codex/README.md`

- [ ] **Step 1: Rewrite the two flagged sections**

Replace the "What it does" paragraph (currently claims "adapts the optio-claudecode iframe machinery" — misdescription flagged in review since browser handling deliberately diverges) and the "Status" section with:

```markdown
## What it does

optio-codex launches `codex` inside a detached tmux session, serves the
TUI over `ttyd`, and coordinates with the host harness through the
`optio.log` keyword channel (STATUS / DELIVERABLE / DONE / ERROR). The
agent reads its task from an `AGENTS.md` file planted in the workdir.
The tmux+ttyd machinery follows the optio-claudecode pattern; browser
handling deliberately differs (`suppress` — codex login is handled via
env/API key or interactively, not via surfaced browser URLs).

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`, created at
prepare time) with `CODEX_HOME` pointing at `<workdir>/home/.codex`, so
the operator's real `~/.codex` identity and config do not leak into the
session. The codex binary is launched via a per-task path
(`<workdir>/home/.local/bin/codex`), so teardown only ever kills this
task's process.

### Authentication (Stage 0)

The isolated home starts empty — codex is NOT logged in. Either pass an
API key into the session env (`CodexTaskConfig(env={"OPENAI_API_KEY": …})`)
or log in interactively (`codex login`) inside the embedded terminal.
Seed-based provisioning (log in once, reuse for every task) arrives with
the seeds stage.

## Status — Stage 0 (hardened MVP)

Shipped:

- iframe/ttyd mode on the local host
- `optio.log` keyword-protocol coordination + exit-status DONE/ERROR channel
- per-task `HOME` / `CODEX_HOME` isolation (tree provisioned at prepare)
- task-scoped teardown (per-task codex path; orphan-ttyd reap)
- `create_codex_task`, `run_codex_session`, `CodexTaskConfig`
- demo task in optio-demo (`Codex demo — iframe`)

Still missing (tracked gaps toward Appendix A parity, staged plans B–E):

- remote SSH host (`ssh` config is rejected until then)
- resume / workdir snapshots; crash-orphan rescue
- seeds, pool/leases, credential save-back, seed verify/refresh
- conversation mode (`codex exec --json` / app-server) + conversation-ui widget
- model switching; file upload/download; tool verbosity
- optio-owned binary cache + auto-install (`install_if_missing` becomes real there)
- filesystem isolation (Landlock / claustrum) reconciled with codex's native sandbox
- demo trio completion (seed-setup + seed-pinned iframe & conversation)
- PyPI release
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-codex/README.md
git commit -m "docs(optio-codex): truthful Stage-0 status, auth story, teardown/isolation description"
```

---

### Task 13: Final verification sweep

**Files:** none (verification only).

- [ ] **Step 1: Full codex suite, fresh run**

Run: `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`
Expected: ~24 passed, 0 failed/errored/skipped.

- [ ] **Step 2: Cross-package sanity (nothing else broken)**

Run: `.venv/bin/python -m pytest packages/optio-agents/tests/ -q` (codex touched nothing there, but the prompt import path must still resolve).
Expected: green (pre-existing flakes per repo notes: re-run once before suspecting a regression).

- [ ] **Step 3: Demo import smoke**

Run: `.venv/bin/python -c "from optio_demo.tasks import get_task_definitions; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Review the branch state**

Run: `git log --oneline main..HEAD` and `git status`
Expected: the three original commits + one commit per task above; clean tree.

---

## Self-Review (performed while writing)

1. **Spec coverage** against the 16 scope items: C1/C2 → Task 2; M3 → Task 3; engine-PATH → Task 4; before_execute → verified non-issue (comment in Task 2); orphan-ttyd + tmux_path → Task 5; prompt SSOT + explainer + wording (BASE_PROMPT_POST now imported, which also resolves the wording-duplication item; the "cooperating human" prose is the SSOT's own and stays — changing optio-agents copy is out of scope) → Task 6; exports + install_if_missing → Task 7; ssh guard → Task 8; M4 teardown tests → Task 5; M5/M6 tests + assertion strengthening → Task 9; Makefile → Task 10; demo → Task 11; README → Task 12. `auto_start=True` default: kept (it is what makes the unattended demo work); its divergence from references is now documented in the README auth/status sections rather than flipped — flagged for the user in the executor's summary.
2. **Placeholder scan:** every code step carries complete code; the two deliberate verify-against-reality notes (Task 11 hook helper signatures, Task 8 SSHConfig fields) instruct the executor to read a named file and mirror a named call site — not to invent.
3. **Type consistency:** `ensure_codex_installed` return semantics change (per-task path) is consumed only by `session._prepare` → `codex_path` → launch/teardown, all updated in Tasks 2/5. `launch_ttyd_with_codex`'s new 5-tuple is unpacked at its single call site (session.py), updated in Task 5. `_provision_task_home(host, *, shared_codex_path)` signature used consistently in Task 2's test and impl.
