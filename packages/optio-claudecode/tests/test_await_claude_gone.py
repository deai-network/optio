import asyncio
import os

import pytest

import optio_claudecode.host_actions as H
from optio_host.host import LocalHost


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
    host = _Host(["12345\n", "12345\n", ""])  # found, found, gone
    ok = await H.await_claude_gone(
        host, "/w/home/.local/bin/claude", poll_s=1.0, timeout_s=10.0,
    )
    assert ok is True
    assert len(_no_real_sleep) == 2  # polled twice before "gone"
    # scoped to the per-task path, and uses the [c]laude self-match guard
    assert all("/w/home/.local/bin/[c]laude" in c for c in host.commands)


@pytest.mark.asyncio
async def test_returns_false_on_timeout(_no_real_sleep):
    host = _Host(["999\n"])  # never gone
    ok = await H.await_claude_gone(
        host, "/w/home/.local/bin/claude", poll_s=1.0, timeout_s=3.0,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_returns_true_immediately_when_already_gone(_no_real_sleep):
    host = _Host([""])
    ok = await H.await_claude_gone(host, "/w/home/.local/bin/claude")
    assert ok is True
    assert len(_no_real_sleep) == 0
    assert len(host.commands) == 1


# --- real-process tests (the mock host above can't exercise pgrep semantics) --
# The false-match bug: the tmux server, pasta, and the bash wrapper all carry
# the claude PATH in their cmdline (as a non-leading argument), so an unanchored
# `pgrep -f <path>` matched them and await_claude_gone false-waited the full
# timeout under netns. Only the real claude execs with the path as argv[0].


@pytest.mark.asyncio
async def test_wrapper_carrying_claude_path_is_not_matched(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    os.makedirs(host.workdir, exist_ok=True)  # run_command's default cwd
    claude_path = str(tmp_path / "workdir" / "home" / ".local" / "bin" / "claude")
    # Stand in for the tmux server / pasta / bash wrapper: a live process whose
    # cmdline embeds the claude path only as a NON-leading argument. It must not
    # be mistaken for a live claude. (python3 holds its argv verbatim — unlike
    # `bash -c <cmd>` which exec-optimizes the trailing args away.)
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", "import time; time.sleep(30)", "holder", claude_path,
    )
    try:
        ok = await H.await_claude_gone(host, claude_path, timeout_s=2.0, poll_s=0.5)
        assert ok is True
    finally:
        proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_real_claude_argv0_is_matched(tmp_path):
    # Regression guard: a process that execs with the path as argv[0] (the real
    # claude) MUST still be detected as live.
    host = LocalHost(taskdir=str(tmp_path))
    bindir = tmp_path / "workdir" / "home" / ".local" / "bin"
    bindir.mkdir(parents=True)
    claude_path = str(bindir / "claude")
    os.symlink("/bin/sleep", claude_path)
    proc = await asyncio.create_subprocess_exec(claude_path, "10")
    try:
        ok = await H.await_claude_gone(host, claude_path, timeout_s=1.0, poll_s=0.5)
        assert ok is False
    finally:
        proc.kill()
        await proc.wait()


# --- kill_claude_processes: the netns claude is orphaned (terminate hits only
# --- ttyd; kill-session only SIGHUPs tmux), so teardown must actively kill it.


@pytest.mark.asyncio
async def test_kill_claude_processes_kills_real_claude(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    os.makedirs(host.workdir, exist_ok=True)
    bindir = tmp_path / "workdir" / "home" / ".local" / "bin"
    bindir.mkdir(parents=True)
    claude_path = str(bindir / "claude")
    os.symlink("/bin/sleep", claude_path)  # execs with the path as argv[0]
    proc = await asyncio.create_subprocess_exec(claude_path, "30")
    try:
        await H.kill_claude_processes(host, claude_path)
        ok = await H.await_claude_gone(host, claude_path, timeout_s=2.0, poll_s=0.2)
        assert ok is True
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_kill_claude_processes_spares_wrapper(tmp_path):
    # A wrapper carrying the path only as a non-leading arg (tmux server / pasta)
    # must NOT be killed — same argv[0] anchoring as await_claude_gone.
    host = LocalHost(taskdir=str(tmp_path))
    os.makedirs(host.workdir, exist_ok=True)
    claude_path = str(tmp_path / "workdir" / "home" / ".local" / "bin" / "claude")
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", "import time; time.sleep(30)", "holder", claude_path,
    )
    try:
        await H.kill_claude_processes(host, claude_path)
        await asyncio.sleep(0.3)
        assert proc.returncode is None  # wrapper survived
    finally:
        proc.kill()
        await proc.wait()
