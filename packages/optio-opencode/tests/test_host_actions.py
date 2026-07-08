"""Tests for optio-opencode host actions: binary-cache resolution +
HOME/XDG isolation env (mirrors optio_claudecode/tests/test_host_actions.py
cache-resolution + prep tests, adapted to opencode's single-binary cache and
the per-task ``_isolation_env``)."""

from __future__ import annotations

import pytest

from optio_host.host import ProcessHandle, RunResult

from optio_opencode import host_actions


class _FakeHost:
    """Minimal Host shim that records run_command calls and returns scripted
    results. Mirrors the claudecode test fake-host shape, adapted to opencode."""

    def __init__(self, scripted_results, *, workdir: str = "/wd") -> None:
        self.commands: list[str] = []
        self._scripted = list(scripted_results)
        self.workdir = workdir
        self.taskdir = workdir.rstrip("/").rsplit("/", 1)[0] or "/"

    async def run_command(self, cmd: str, *, cwd=None, env=None) -> RunResult:
        self.commands.append(cmd)
        nxt = self._scripted.pop(0)
        if callable(nxt):
            return nxt(cmd)
        return nxt

    async def resolve_host_home(self) -> str:
        return "/root"


_CACHE = "/home/u/.cache/optio-opencode/bin"


def _resolve_cache(path: str = _CACHE) -> RunResult:
    return RunResult(stdout=path, stderr="", exit_code=0)


# ---------------------------------------------------------------------------
# Step 1 — _isolation_env unit test.
# ---------------------------------------------------------------------------


def test_isolation_env_returns_xdg_and_home_under_workdir():
    host = _FakeHost([], workdir="/wd")
    env = host_actions._isolation_env(host)
    assert env == {
        "HOME": "/wd/home",
        "XDG_CONFIG_HOME": "/wd/home/.config",
        "XDG_DATA_HOME": "/wd/home/.local/share",
        "XDG_CACHE_HOME": "/wd/home/.cache",
    }


def test_isolation_env_strips_trailing_slash_from_workdir():
    host = _FakeHost([], workdir="/wd/")
    env = host_actions._isolation_env(host)
    assert env["HOME"] == "/wd/home"
    assert env["XDG_DATA_HOME"] == "/wd/home/.local/share"


# ---------------------------------------------------------------------------
# Step 2 — cache resolution test (override wins; default via printf host cmd).
# ---------------------------------------------------------------------------


async def test_resolve_install_dir_override_skips_resolve():
    """An explicit install_dir wins and never runs a host command."""
    host = _FakeHost([])
    path = await host_actions._resolve_install_dir(host, "/opt/opencode-cache/")
    assert path == "/opt/opencode-cache"  # trailing slash stripped
    assert host.commands == []  # override → no resolve command issued


async def test_resolve_install_dir_default_via_printf_host_command():
    """With no override, the default cache dir is resolved on the worker via a
    ``printf %s`` of the OPENCODE_CACHE_DIR/XDG_CACHE_HOME/$HOME shell default."""
    host = _FakeHost([_resolve_cache()])
    path = await host_actions._resolve_install_dir(host, None)
    assert path == _CACHE
    assert len(host.commands) == 1
    cmd = host.commands[0]
    assert cmd.startswith("printf %s ")
    assert "OPENCODE_CACHE_DIR" in cmd
    assert "XDG_CACHE_HOME" in cmd
    assert "optio-opencode/bin" in cmd


async def test_resolve_install_dir_strips_trailing_slash_from_resolved():
    host = _FakeHost([_resolve_cache(_CACHE + "/")])
    path = await host_actions._resolve_install_dir(host, None)
    assert path == _CACHE


async def test_resolve_install_dir_raises_on_empty_resolution():
    host = _FakeHost([RunResult(stdout="", stderr="boom", exit_code=1)])
    import pytest

    with pytest.raises(RuntimeError, match="cache dir"):
        await host_actions._resolve_install_dir(host, None)


# ---------------------------------------------------------------------------
# Step 3 — launch_opencode env test: the four isolation keys + OPENCODE_DB.
# ---------------------------------------------------------------------------


class _RecordingLaunchHost:
    """Fake host that records the launch env and yields a ready URL line so
    ``launch_opencode`` parses a port and returns. Reused from the opencode
    test fake-host pattern (run_command + write_text + launch_subprocess)."""

    def __init__(self, *, workdir: str = "/wd", taskdir: str = "/td") -> None:
        self.workdir = workdir
        self.taskdir = taskdir
        self.launch_env: dict[str, str] | None = None
        self.launch_cmd: str | None = None
        self.commands: list[str] = []

    async def write_text(self, relpath: str, content: str) -> None:
        pass

    async def run_command(self, cmd: str, *, cwd=None, env=None) -> RunResult:
        self.commands.append(cmd)
        return RunResult(stdout="", stderr="", exit_code=0)

    async def launch_subprocess(
        self, command, *, env=None, cwd=None, merge_stderr=True, stdin=False,
        env_remove=None,
    ) -> ProcessHandle:
        self.launch_cmd = command
        self.launch_env = env

        async def _stdout():
            yield b"server listening on http://127.0.0.1:54321\n"

        return ProcessHandle(pid_like=object(), stdout=_stdout())

    async def terminate_subprocess(self, handle, *, aggressive=False) -> None:
        pass


async def test_launch_opencode_env_carries_isolation_and_db():
    host = _RecordingLaunchHost(workdir="/wd", taskdir="/td")
    handle, port = await host_actions.launch_opencode(host, "pw")
    assert port == 54321
    env = host.launch_env
    assert env is not None
    # The four isolation keys, all under <workdir>/home.
    assert env["HOME"] == "/wd/home"
    assert env["XDG_CONFIG_HOME"] == "/wd/home/.config"
    assert env["XDG_DATA_HOME"] == "/wd/home/.local/share"
    assert env["XDG_CACHE_HOME"] == "/wd/home/.cache"
    # OPENCODE_DB points at the per-task db under taskdir.
    assert env["OPENCODE_DB"] == "/td/opencode.db"


async def test_launch_opencode_env_disables_in_agent_autoupdate():
    """The launched opencode server must have its in-agent auto-updater
    disabled (OPENCODE_DISABLE_AUTOUPDATE=1) so it never self-downloads a new
    binary mid-session and fights optio's pinned cache. The binary reads this
    exact var (update fn early-returns on it); optio keeps the cache fresh via
    smart-install.sh --check instead."""
    host = _RecordingLaunchHost(workdir="/wd", taskdir="/td")
    await host_actions.launch_opencode(host, "pw")
    env = host.launch_env
    assert env is not None
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"


async def test_launch_opencode_env_merges_extra_env_on_top():
    host = _RecordingLaunchHost(workdir="/wd", taskdir="/td")
    await host_actions.launch_opencode(
        host, "pw", extra_env={"BROWSER": "/wd/bin/open", "PATH": "/wd/bin:/usr/bin"},
    )
    env = host.launch_env
    assert env is not None
    # Isolation + OPENCODE_DB still present alongside the browser-suppress env.
    assert env["HOME"] == "/wd/home"
    assert env["OPENCODE_DB"] == "/td/opencode.db"
    assert env["BROWSER"] == "/wd/bin/open"
    assert env["PATH"] == "/wd/bin:/usr/bin"


class _FailingLaunchHost:
    """Fake host whose launched process emits diagnostic lines on the merged
    stdout stream then exits WITHOUT ever printing a listening URL — the
    process-died-at-launch failure mode."""

    def __init__(self, output_lines: list[bytes], *, workdir="/wd", taskdir="/td"):
        self.workdir = workdir
        self.taskdir = taskdir
        self._output_lines = output_lines
        self.terminated = False

    async def write_text(self, relpath: str, content: str) -> None:
        pass

    async def run_command(self, cmd: str, *, cwd=None, env=None) -> RunResult:
        return RunResult(stdout="", stderr="", exit_code=0)

    async def launch_subprocess(
        self, command, *, env=None, cwd=None, merge_stderr=True, stdin=False,
        env_remove=None,
    ) -> ProcessHandle:
        lines = self._output_lines

        async def _stdout():
            for ln in lines:
                yield ln

        return ProcessHandle(pid_like=object(), stdout=_stdout())

    async def terminate_subprocess(self, handle, *, aggressive=False) -> None:
        self.terminated = True


async def test_launch_opencode_surfaces_reason_when_no_url():
    """When opencode exits before printing a URL, the raised error must carry
    the tail of the server's own output (merged stdout+stderr) so the operator
    sees the REASON — not a bare 'exited before printing a URL'."""
    host = _FailingLaunchHost([
        b"opencode: failed to bind :0\n",
        b"error: operation not permitted (claustrum denial)\n",
    ])
    with pytest.raises(RuntimeError) as ei:
        await host_actions.launch_opencode(host, "pw")
    msg = str(ei.value)
    assert "exited before printing a URL" in msg
    assert "operation not permitted (claustrum denial)" in msg
    assert "failed to bind :0" in msg


async def test_launch_opencode_no_url_no_output_stays_bare():
    """No captured output → no dangling 'last output:' suffix (bounded, clean)."""
    host = _FailingLaunchHost([])
    with pytest.raises(RuntimeError) as ei:
        await host_actions.launch_opencode(host, "pw")
    msg = str(ei.value)
    assert msg.endswith("exited before printing a URL")
    assert "last output" not in msg


# ---------------------------------------------------------------------------
# Step 4 — auto-start POST body-shape SPIKE (NOT an automated assertion).
#
# This is the implementation spike named in Task 4 Step 3 of the parity plan:
# the exact JSON body that ``POST /api/session/<id>/prompt`` accepts must be
# confirmed against a LIVE cached opencode server — it cannot be exercised
# here (no live opencode server reachable from the test sandbox).
#
# Spike procedure (run during validation, document the result):
#   1. Launch the "Setup opencode seed" demo task so a cached opencode binary
#      serves ``opencode web`` with a pre-created session.
#   2. POST to ``http://127.0.0.1:<worker_port>/api/session/<session_id>/prompt``
#      with auth ``Basic base64("opencode:<password>")`` and a candidate body.
#   3. Observe which shape the server accepts and record it.
#
# Candidate body (pending live-spike confirmation; the shape the parity plan
# suggests): {"parts": [{"type": "text", "text": <AUTO_START_PROMPT>}]}.
# Once confirmed, wire the accepted shape into ``session._post_opencode_prompt``
# and replace this note's "pending" wording with the confirmed shape.
#
# No automated assertion: the gating behaviour (fresh POSTs, resume does not)
# is covered by the session integration test (Task 9 Step 3); the wire shape
# itself is a live spike, deliberately not asserted against a mock here.
# ---------------------------------------------------------------------------
