# Claudecode tmux-persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run claude inside a detached, per-task tmux session that ttyd attaches to, so a claudecode task is a true background process that survives viewer disconnect/reconnect and supports N simultaneous viewers.

**Architecture:** `optio_claudecode/host_actions.py` gains two argv builders (`build_tmux_session_argv`, `build_ttyd_attach_argv`) and a `_require_tmux` check; `launch_ttyd_with_claude` becomes "start detached tmux session running the claude wrapper, then start ttyd attaching to it." `optio_claudecode/session.py` awaits tmux-session liveness instead of the ttyd process and tears down the tmux session in addition to ttyd. Completion detection stays `optio.log`-based and unchanged.

**Tech Stack:** Python 3.13, pytest/pytest-asyncio, tmux 3.x (worker prerequisite), ttyd 1.7.x. Spec: `docs/2026-06-01-claudecode-tmux-persistence-design.md`.

**Execution note:** This is a sequential refactor of two files (`host_actions.py`, `session.py`); tasks are interdependent and must run in order (not a parallel fan-out). All-suite verification is deferred to the final task. venv: use the worktree/repo `.venv` (`../../.venv/bin/python` from a package dir); run tests with `OPTIO_SKIP_PREFLIGHT_TESTS=1` to avoid the unrelated core WS/cancel flake. Do NOT use `npx`.

---

## Task 1: `_require_tmux` host check

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_actions.py`:

```python
import pytest
from optio_claudecode import host_actions


class _FakeResult:
    def __init__(self, exit_code, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _FakeHost:
    def __init__(self, *, tmux_ok):
        self._tmux_ok = tmux_ok
        self.workdir = "/wd"

    async def run_command(self, cmd, **kwargs):
        # _require_tmux runs `bash -lc 'command -v tmux'`
        if "command -v tmux" in cmd:
            return _FakeResult(0, "/usr/bin/tmux\n") if self._tmux_ok else _FakeResult(1, "")
        return _FakeResult(0, "")


async def test_require_tmux_returns_path_when_present():
    path = await host_actions._require_tmux(_FakeHost(tmux_ok=True))
    assert path == "/usr/bin/tmux"


async def test_require_tmux_raises_clear_error_when_missing():
    with pytest.raises(RuntimeError, match="tmux is required"):
        await host_actions._require_tmux(_FakeHost(tmux_ok=False))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k require_tmux`
Expected: FAIL — `AttributeError: module ... has no attribute '_require_tmux'`.

- [ ] **Step 3: Implement `_require_tmux`**

Add to `host_actions.py` (near the other helpers, after `_ttyd_present`):

```python
async def _require_tmux(host: "Host") -> str:
    """Return the absolute path to tmux on the host, or raise a clear error.

    claudecode runs claude inside a detached tmux session (so the agent
    survives viewer disconnects); tmux is a worker prerequisite. Resolved via a
    login shell so PATH additions from the worker profile apply. No
    auto-install: a missing tmux fails fast with an actionable message.
    """
    result = await host.run_command("bash -lc 'command -v tmux'")
    path = (result.stdout or "").strip()
    if result.exit_code != 0 or not path:
        raise RuntimeError(
            "tmux is required on the worker for optio-claudecode (claude runs "
            "inside a detached tmux session). Install tmux (e.g. apt-get install "
            "tmux) or add it to the worker/container image."
        )
    return path
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k require_tmux`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): _require_tmux worker-prerequisite check"
```

---

## Task 2: `build_tmux_session_argv`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_host_actions.py`

This builder produces the argv for `tmux new-session -d` that starts claude
detached. It reuses the env-assignment, netns-seal, and DONE/ERROR-wrapper
logic currently inside `build_ttyd_argv`. tmux runs its command argument via
`/bin/sh -c`, so the env + `bash -c <payload>` is assembled into a single
shell-string element (unlike ttyd, which exec's separate argv elements).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_actions.py`:

```python
def test_build_tmux_session_argv_shape(monkeypatch):
    monkeypatch.delenv("OPTIO_CLAUDECODE_NETNS", raising=False)
    argv = host_actions.build_tmux_session_argv(
        tmux_path="/usr/bin/tmux",
        claude_path="/wd/home/.local/bin/claude",
        workdir="/wd",
        socket_path="/wd/tmux.sock",
        session_name="optio",
        extra_env={"FOO": "bar"},
        claude_flags=["--flag"],
    )
    # tmux invocation on the private socket, detached, named session
    assert argv[:9] == [
        "/usr/bin/tmux", "-S", "/wd/tmux.sock", "new-session", "-d",
        "-s", "optio", "-x", "200",
    ]
    assert argv[9:11] == ["-y", "50"]
    # the command is a SINGLE trailing shell-string element
    cmd = argv[-1]
    assert cmd.startswith("env ")
    assert "HOME=/wd/home" in cmd
    assert "PATH=/wd/home/.local/bin:" in cmd
    assert "FOO=bar" in cmd
    assert "bash -c " in cmd
    # the wrapper still cds + runs claude + appends DONE/ERROR to optio.log
    assert "cd /wd &&" in cmd
    assert "/wd/home/.local/bin/claude --flag" in cmd
    assert "echo DONE >> /wd/optio.log" in cmd
    assert "ERROR: claude exited" in cmd


def test_build_tmux_session_argv_netns_seal(monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_NETNS", "pasta --config-net --")
    argv = host_actions.build_tmux_session_argv(
        tmux_path="/usr/bin/tmux",
        claude_path="/wd/home/.local/bin/claude",
        workdir="/wd",
        socket_path="/wd/tmux.sock",
        session_name="optio",
        extra_env=None,
        claude_flags=[],
    )
    cmd = argv[-1]
    assert "pasta --config-net --" in cmd
    assert "IS_SANDBOX=1" in cmd
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k build_tmux_session`
Expected: FAIL — `AttributeError: ... 'build_tmux_session_argv'`.

- [ ] **Step 3: Extract the shared wrapper + implement the builder**

In `host_actions.py`, add a private helper that builds the env-assignments +
`bash -c <payload>` shell string (factored out of `build_ttyd_argv`'s body),
then the new builder. Add:

```python
def _build_claude_shell_command(
    *,
    claude_path: str,
    workdir: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
) -> tuple[list[str], str]:
    """Return (env_assignments, shell_command).

    ``env_assignments`` is the list of ``KEY=VALUE`` strings (HOME, PATH,
    extras). ``shell_command`` is the full ``env <assignments> bash -c
    <payload>`` string that runs claude under HOME-isolation, applies the
    optional OPTIO_CLAUDECODE_NETNS seal, and appends DONE/ERROR to optio.log
    when claude exits. Shared by build_ttyd_argv (legacy/no longer used for the
    direct child) and build_tmux_session_argv.
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

    netns_wrap = os.environ.get("OPTIO_CLAUDECODE_NETNS", "").strip()
    if netns_wrap:
        inner = "IS_SANDBOX=1 " + " ".join(
            shlex.quote(c) for c in [claude_path, *claude_flags]
        )
        claude_cmd = [*shlex.split(netns_wrap), "bash", "-c", inner]
        _LOG.info(
            "OPTIO_CLAUDECODE_NETNS active — claude wrapped via %r "
            "(bash -c, IS_SANDBOX=1, flags kept)", netns_wrap,
        )
    else:
        claude_cmd = [claude_path, *claude_flags]
        _LOG.info(
            "OPTIO_CLAUDECODE_NETNS not set (value=%r) — no loopback isolation",
            os.environ.get("OPTIO_CLAUDECODE_NETNS"),
        )
    claude_argv = " ".join(shlex.quote(c) for c in claude_cmd)
    log_path = f"{workdir_clean}/optio.log"
    bash_payload = (
        f"cd {shlex.quote(workdir_clean)} && {claude_argv}; rc=$?; "
        f'if [ "$rc" = 0 ]; then echo DONE >> {shlex.quote(log_path)}; '
        f"else printf 'ERROR: claude exited %s\\n' \"$rc\" >> {shlex.quote(log_path)}; fi"
    )
    shell_command = "env " + " ".join(
        shlex.quote(x) for x in [*env_assignments, "bash", "-c", bash_payload]
    )
    return env_assignments, shell_command


def build_tmux_session_argv(
    *,
    tmux_path: str,
    claude_path: str,
    workdir: str,
    socket_path: str,
    session_name: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
) -> list[str]:
    """Argv for the detached ``tmux new-session`` that starts claude.

    tmux runs its command argument via ``/bin/sh -c``, so the env + claude
    wrapper is a single trailing shell-string element. The private socket
    (``-S socket_path``) isolates this task's tmux server. ``-x/-y`` give the
    detached pane a sane initial size before any viewer attaches.
    """
    _, shell_command = _build_claude_shell_command(
        claude_path=claude_path,
        workdir=workdir,
        extra_env=extra_env,
        claude_flags=claude_flags,
    )
    return [
        tmux_path, "-S", socket_path, "new-session", "-d",
        "-s", session_name, "-x", "200", "-y", "50",
        shell_command,
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k build_tmux_session`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): build_tmux_session_argv (detached claude session)"
```

---

## Task 3: `build_ttyd_attach_argv`

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_host_actions.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_actions.py`:

```python
def test_build_ttyd_attach_argv_shape():
    argv = host_actions.build_ttyd_attach_argv(
        ttyd_path="/bin/ttyd",
        tmux_path="/usr/bin/tmux",
        socket_path="/wd/tmux.sock",
        session_name="optio",
        bind_iface="127.0.0.1",
        port=0,
    )
    assert argv == [
        "/bin/ttyd", "-W", "-i", "127.0.0.1", "-p", "0",
        "-T", "xterm-256color", "--",
        "/usr/bin/tmux", "-S", "/wd/tmux.sock", "attach", "-t", "optio",
    ]
    # single-viewer cap is gone (N observers)
    assert "-m" not in argv
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k build_ttyd_attach`
Expected: FAIL — `AttributeError: ... 'build_ttyd_attach_argv'`.

- [ ] **Step 3: Implement the builder**

Add to `host_actions.py`:

```python
def build_ttyd_attach_argv(
    *,
    ttyd_path: str,
    tmux_path: str,
    socket_path: str,
    session_name: str,
    bind_iface: str,
    port: int,
) -> list[str]:
    """Argv for ttyd attaching viewers to the live tmux session.

    ttyd no longer runs claude — it runs ``tmux attach``. ``-m 1`` is dropped so
    multiple viewers can attach to the same session simultaneously (the agent's
    life is owned by the tmux session, not by any connection).
    """
    return [
        ttyd_path, "-W",
        "-i", bind_iface,
        "-p", str(port),
        "-T", "xterm-256color",
        "--",
        tmux_path, "-S", socket_path, "attach", "-t", session_name,
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k build_ttyd_attach`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): build_ttyd_attach_argv (ttyd attaches, no -m 1)"
```

---

## Task 4: Rewire `launch_ttyd_with_claude` to tmux + ttyd-attach

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
- Test: `packages/optio-claudecode/tests/test_host_actions.py` (signature/return shape)

The launch now: (1) `_require_tmux`, (2) start the detached tmux session
(applying the env scrub so claude's env is scrubbed), (3) start ttyd attaching,
(4) parse ttyd's port. It returns `(ttyd_handle, port, socket_path,
session_name)` so the session can await tmux liveness and tear both down.

**Pre-check (do first, it determines step 3 code):** confirm whether
`host.run_command` accepts an `env_remove` kwarg (it was added alongside
`launch_subprocess`'s in commit `41e4148`):

Run: `grep -n "def run_command" packages/optio-host/src/optio_host/host.py`
then read the signature. If `run_command` has `env_remove`, start the tmux
session with `host.run_command(cmd, env_remove=env_remove)`. If it does NOT,
start it with `host.launch_subprocess(cmd, env_remove=env_remove)` and
immediately await the handle's exit (tmux `new-session -d` returns at once).
The code below uses `run_command`; adjust per the pre-check.

- [ ] **Step 1: Write the failing test (return shape, fake host)**

Add to `tests/test_host_actions.py`:

```python
class _LaunchFakeHost:
    """Records the tmux-start command, serves a fake ttyd ready banner."""
    def __init__(self):
        self.workdir = "/wd"
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        if "command -v tmux" in cmd:
            return _FakeResult(0, "/usr/bin/tmux\n")
        return _FakeResult(0, "")

    async def launch_subprocess(self, command, **kwargs):
        self.commands.append(command)
        return _FakeTtydHandle()


class _FakeTtydHandle:
    @property
    def stdout(self):
        async def _gen():
            yield b"http://127.0.0.1:45999/\n"
        return _gen()


async def test_launch_returns_handle_port_socket_session(monkeypatch):
    monkeypatch.delenv("OPTIO_CLAUDECODE_NETNS", raising=False)
    host = _LaunchFakeHost()
    handle, port, socket_path, session = await host_actions.launch_ttyd_with_claude(
        host,
        ttyd_path="/bin/ttyd",
        claude_path="/wd/home/.local/bin/claude",
        bind_iface="127.0.0.1",
        extra_env={},
        claude_flags=[],
        ready_timeout_s=5.0,
        env_remove=None,
    )
    assert port == 45999
    assert socket_path == "/wd/tmux.sock"
    assert session == "optio"
    # a detached tmux new-session was started before ttyd
    assert any("new-session -d" in c or "new-session" in c for c in host.commands)
```

Adjust the `_TTYD_READY_RE` banner string in the fake if the real regex needs a
different shape — check `grep -n "_TTYD_READY_RE" host_actions.py` and match it.

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k launch_returns`
Expected: FAIL — current `launch_ttyd_with_claude` returns a 2-tuple, not 4.

- [ ] **Step 3: Rewire `launch_ttyd_with_claude`**

Replace the body of `launch_ttyd_with_claude` with:

```python
async def launch_ttyd_with_claude(
    host: "Host",
    *,
    ttyd_path: str,
    claude_path: str,
    bind_iface: str,
    extra_env: dict[str, str] | None,
    claude_flags: list[str],
    ready_timeout_s: float = 30.0,
    env_remove: list[str] | None = None,
    session_name: str = "optio",
) -> "tuple[ProcessHandle, int, str, str]":
    """Start claude in a detached tmux session, then ttyd attaching to it.

    Returns ``(ttyd_handle, port, socket_path, session_name)``. claude runs in
    the tmux session independent of ttyd; the caller awaits tmux-session
    liveness for completion and tears down BOTH the tmux session and ttyd.
    """
    tmux_path = await _require_tmux(host)
    socket_path = f"{host.workdir.rstrip('/')}/tmux.sock"

    # 1) Start claude detached in tmux. The env scrub (env_remove) must apply
    #    here so the tmux server — which holds claude — does not inherit
    #    scrubbed vars. new-session -d returns immediately.
    session_argv = build_tmux_session_argv(
        tmux_path=tmux_path,
        claude_path=claude_path,
        workdir=host.workdir,
        socket_path=socket_path,
        session_name=session_name,
        extra_env=extra_env,
        claude_flags=claude_flags,
    )
    session_cmd = " ".join(shlex.quote(a) for a in session_argv)
    _LOG.info("tmux session start command: %s", session_cmd)
    start = await host.run_command(session_cmd, env_remove=env_remove)
    if start.exit_code != 0:
        raise RuntimeError(
            f"tmux new-session failed (exit {start.exit_code}): "
            f"{(start.stderr or '').strip()[:200]}"
        )

    # 2) Start ttyd attaching to the live session.
    ttyd_argv = build_ttyd_attach_argv(
        ttyd_path=ttyd_path,
        tmux_path=tmux_path,
        socket_path=socket_path,
        session_name=session_name,
        bind_iface=bind_iface,
        port=0,
    )
    command = " ".join(shlex.quote(a) for a in ttyd_argv)
    _LOG.info("launch ttyd attach command: %s", command)
    handle = await host.launch_subprocess(command)

    async def _read_port() -> int:
        async for raw in handle.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip() if isinstance(raw, bytes) else str(raw).rstrip()
            m = _TTYD_READY_RE.search(line)
            if m:
                port_str = m.group(1) or m.group(2)
                return int(port_str)
        raise RuntimeError("ttyd exited before printing a listening URL")

    try:
        port = await asyncio.wait_for(_read_port(), timeout=ready_timeout_s)
    except asyncio.TimeoutError:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise TimeoutError(
            f"ttyd did not print a listening URL within {ready_timeout_s}s"
        )
    except BaseException:
        await host.terminate_subprocess(handle, aggressive=True)
        await _kill_tmux_session(host, tmux_path, socket_path, session_name)
        raise
    return handle, port, socket_path, session_name


async def _kill_tmux_session(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> None:
    """Best-effort kill of the per-task tmux session (stops claude)."""
    try:
        await host.run_command(
            f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
            f"kill-session -t {shlex.quote(session_name)}"
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("tmux kill-session failed (socket=%s)", socket_path)


async def tmux_session_alive(
    host: "Host", tmux_path: str, socket_path: str, session_name: str,
) -> bool:
    """True while the claude-bearing tmux session exists."""
    r = await host.run_command(
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"has-session -t {shlex.quote(session_name)}"
    )
    return r.exit_code == 0
```

Note: `_require_tmux` is called inside launch, so the separately-callable check
in the session flow is optional — but keep launch self-sufficient. Also keep the
now-unused `build_ttyd_argv` for one transition commit; Task 7 removes it.

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q -k launch_returns`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/host_actions.py packages/optio-claudecode/tests/test_host_actions.py
git commit -m "feat(optio-claudecode): launch starts detached tmux session + ttyd attach"
```

---

## Task 5: Rewire session body await + teardown

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`

The body currently does `handle, ttyd_port = await launch_ttyd_with_claude(...)`
then `proc = launched_handle.pid_like; await proc.wait()`. Now launch returns a
4-tuple; the body must await **tmux-session liveness** (claude), not the ttyd
process, and the `finally` must kill the tmux session in addition to ttyd.

- [ ] **Step 1: Capture the new launch return + store tmux identifiers**

In `session.py`, near `launched_handle: ProcessHandle | None = None` (line ~86),
add nonlocal-tracked identifiers:

```python
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
```

and add them to the `nonlocal` declaration in the body (line ~156):

```python
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session
```

- [ ] **Step 2: Update the launch call + the await**

Replace the launch call (line ~235) and the `await proc.wait()` (line ~257-258):

```python
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_claude(
            host,
            ttyd_path=ttyd_path,
            claude_path=claude_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            claude_flags=claude_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
        )
        launched_handle = handle
        tmux_path = await host_actions._require_tmux(host)
```

and replace the await-on-ttyd with a tmux-liveness poll:

```python
        # Await the claude process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session is.
        # The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if claude exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)
```

(`asyncio` is already imported in `session.py`.)

- [ ] **Step 3: Update the teardown `finally` to kill the tmux session**

In the `finally` (after the existing `terminate_subprocess(launched_handle)`
block, line ~275-278), add:

```python
        if tmux_path is not None and tmux_socket is not None and tmux_session is not None:
            try:
                await host_actions._kill_tmux_session(
                    host, tmux_path, tmux_socket, tmux_session,
                )
            except Exception:
                _LOG.exception("tmux session teardown failed")
```

- [ ] **Step 4: Verify the package still imports + unit tests pass**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_host_actions.py -q`
Expected: PASS (existing build_ttyd_argv tests may still pass since it's retained; new builders pass).
Also: `../../.venv/bin/python -c "import optio_claudecode.session"` → no error.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py
git commit -m "feat(optio-claudecode): await tmux-session liveness; teardown kills session"
```

---

## Task 6: Integration tests (real tmux + fake claude)

**Files:**
- Test: `packages/optio-claudecode/tests/test_tmux_persistence.py` (create)

Use the real tmux binary with a trivial fake-claude command (a shell snippet) to
exercise the lifecycle directly against `launch_ttyd_with_claude` /
`_kill_tmux_session` / `tmux_session_alive`, using a `LocalHost`-backed tmp
workdir. ttyd is launched for real but we do not drive a WS client; we assert the
decoupling by terminating the ttyd handle and checking the tmux session survives.

- [ ] **Step 1: Write the integration tests**

Create `tests/test_tmux_persistence.py`:

```python
"""Live lifecycle: claude-in-detached-tmux survives viewer (ttyd) teardown.

Uses the real tmux binary (worker prerequisite). The "claude" command is a
shell snippet that records a pidfile then sleeps, so we can assert it runs
detached, survives a ttyd kill, and is reaped by teardown.
"""
import asyncio
import os
import shutil

import pytest

from optio_host.host import LocalHost
from optio_claudecode import host_actions

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed on the worker"
)


def _local_host(tmp_path):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)
    os.makedirs(f"{host.workdir}/home/.local/bin", exist_ok=True)
    return host


async def _start_session(host, marker, flags=None):
    """Start a detached tmux session whose 'claude' writes `marker` then sleeps."""
    # fake claude = a script that records its pid then sleeps 60s
    fake = f"{host.workdir}/home/.local/bin/claude"
    with open(fake, "w") as f:
        f.write(f"#!/bin/bash\necho $$ > {marker}\nexec sleep 60\n")
    os.chmod(fake, 0o755)
    tmux_path = await host_actions._require_tmux(host)
    socket = f"{host.workdir.rstrip('/')}/tmux.sock"
    argv = host_actions.build_tmux_session_argv(
        tmux_path=tmux_path, claude_path=fake, workdir=host.workdir,
        socket_path=socket, session_name="optio",
        extra_env=None, claude_flags=flags or [],
    )
    import shlex
    cmd = " ".join(shlex.quote(a) for a in argv)
    r = await host.run_command(cmd)
    assert r.exit_code == 0, r.stderr
    return tmux_path, socket


async def test_claude_starts_detached_before_any_viewer(tmp_path):
    host = _local_host(tmp_path)
    marker = f"{host.workdir}/claude.pid"
    tmux_path, socket = await _start_session(host, marker)
    try:
        # session is alive with NO ttyd / viewer involved
        assert await host_actions.tmux_session_alive(host, tmux_path, socket, "optio")
        # the fake claude actually ran (wrote its pid)
        for _ in range(20):
            if os.path.exists(marker):
                break
            await asyncio.sleep(0.1)
        assert os.path.exists(marker)
    finally:
        await host_actions._kill_tmux_session(host, tmux_path, socket, "optio")


async def test_session_survives_ttyd_teardown_then_killed_on_teardown(tmp_path):
    host = _local_host(tmp_path)
    marker = f"{host.workdir}/claude.pid"
    tmux_path, socket = await _start_session(host, marker)
    try:
        for _ in range(20):
            if os.path.exists(marker):
                break
            await asyncio.sleep(0.1)
        child_pid = int(open(marker).read().strip())

        # REGRESSION: a viewer disconnecting (ttyd handle gone) must NOT kill claude.
        # We have no ttyd here; killing/absence of any viewer is the strongest form.
        assert await host_actions.tmux_session_alive(host, tmux_path, socket, "optio")
        os.kill(child_pid, 0)  # raises if dead — proves it survives sans viewer

        # teardown kills the session
        await host_actions._kill_tmux_session(host, tmux_path, socket, "optio")
        await asyncio.sleep(0.5)
        assert not await host_actions.tmux_session_alive(host, tmux_path, socket, "optio")
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        await host_actions._kill_tmux_session(host, tmux_path, socket, "optio")
```

- [ ] **Step 2: Run to verify they pass**

Run: `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest tests/test_tmux_persistence.py -q`
Expected: PASS (2 passed) — or SKIPPED if tmux absent (it is present per the spec).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-claudecode/tests/test_tmux_persistence.py
git commit -m "test(optio-claudecode): tmux session runs detached + survives viewer teardown"
```

---

## Task 7: Update existing tests + fake-claude harness; remove dead code; full verification

**Files:**
- Modify: `packages/optio-claudecode/tests/test_host_actions.py` (drop/adapt `build_ttyd_argv` + `-m 1` assertions)
- Modify: `packages/optio-claudecode/tests/fake_claude.py` and any session-flow test that drove ttyd directly (e.g. `test_session_local.py`, `test_session_hooks.py`) so they exercise the tmux-backed launch
- Modify: `packages/optio-claudecode/src/optio_claudecode/host_actions.py` (remove the now-unused `build_ttyd_argv`)

This is the only task that may touch multiple test files; do it last so the
suite is green in one pass.

- [ ] **Step 1: Find all references to the old launch shape**

Run:
```bash
cd packages/optio-claudecode
grep -rn "build_ttyd_argv\|launch_ttyd_with_claude\|-m\", \"1\|\"-m\"\|pid_like\|proc.wait" src tests
```
List each call site. Session-flow tests that monkeypatch `launch_ttyd_with_claude`
must return the new 4-tuple `(handle, port, socket, session)`; tests asserting the
old `build_ttyd_argv` shape or `-m 1` must be removed or rewritten against
`build_tmux_session_argv` / `build_ttyd_attach_argv` (Tasks 2–3 already cover the
new builders, so delete the stale `build_ttyd_argv` assertions).

- [ ] **Step 2: Adapt the fake-claude session tests**

For any test that monkeypatches `host_actions.launch_ttyd_with_claude`, update the
fake to return four values, e.g.:

```python
    async def _fake_launch(host, **kw):
        # ... start nothing real; return a dummy handle + identifiers
        return _DummyHandle(), 4567, f"{host.workdir}/tmux.sock", "optio"
    monkeypatch.setattr(host_actions, "launch_ttyd_with_claude", _fake_launch)
```

For tests that exercise the real launch through `fake_claude.py`, ensure the fake
binary path is `<workdir>/home/.local/bin/claude` and that the session-liveness
poll terminates (the fake claude must exit and write DONE, OR the test cancels the
session via the cancellation flag as today). If a test relied on `await
proc.wait()` returning when the fake ttyd exited, switch it to drive completion via
`optio.log` (write DONE) or the cancel path.

- [ ] **Step 3: Remove the dead `build_ttyd_argv`**

Delete the `build_ttyd_argv` function from `host_actions.py` (its logic now lives
in `_build_claude_shell_command` + `build_tmux_session_argv`). Confirm no remaining
references: `grep -rn build_ttyd_argv src tests` → empty.

- [ ] **Step 4: Full claudecode suite + dependent suites**

Run:
```bash
cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q
```
Expected: all pass (no failures, skips only for tmux-absent if applicable).

Then sanity the consumers that import claudecode launch indirectly:
```bash
cd ../optio-demo && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode
git commit -m "test(optio-claudecode): migrate launch tests to tmux; drop build_ttyd_argv"
```

---

## Final verification (deferred to end)

- [ ] Run `cd packages/optio-claudecode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q` — green.
- [ ] `grep -rn build_ttyd_argv packages/optio-claudecode` — empty (dead code gone).
- [ ] `grep -n '"-m"' packages/optio-claudecode/src/optio_claudecode/host_actions.py` — empty (single-viewer cap gone).
- [ ] Manual/live (operator): launch a seeded claudecode task, open the iframe, **disconnect, confirm the task keeps running**, reconnect, confirm the same session resumes; open a 2nd viewer, confirm both see the live TUI.
