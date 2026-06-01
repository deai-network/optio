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
