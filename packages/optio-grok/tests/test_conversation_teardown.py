"""Regression: the fs-isolation controlling-tty wrapper must not orphan grok.

grok's fail-closed sandbox needs a controlling /dev/tty, which the conversation
launch supplies via a python helper that opens a pty and TIOCSCTTYs it. That
helper must NOT create a new session (setsid) from a FORKED child, or grok escapes
into its own process group and optio's killpg teardown (which targets the launched
sh's pgid) never reaches it — leaving grok orphaned on cancel.

The fix: launch under ``exec`` so /bin/sh replaces itself with the wrapper→grok
chain, making grok THE session leader in the launched pgid. These tests prove the
wrapped process is reaped WITH ``exec`` and (as a control) orphaned WITHOUT it — so
``exec`` is load-bearing, not cosmetic.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
from pathlib import Path

import pytest

from optio_host.host import LocalHost
from optio_grok.host_actions import build_conversation_argv, _isolation_env


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _launch_fake(tmp_path: Path, *, use_exec: bool):
    """Launch a fake grok (writes its final pid, then sleeps) through the real
    fs-isolation conversation launch path. Returns (host, handle, final_pid)."""
    fake = tmp_path / "grok"
    pidfile = tmp_path / "final.pid"
    # `exec sleep` so the sleeping process keeps the shell's pid — i.e. the pid the
    # tty-wrapper execs into is exactly what we record.
    fake.write_text(f'#!/bin/bash\necho $$ > {shlex.quote(str(pidfile))}\nexec sleep 120\n')
    fake.chmod(0o755)

    host = LocalHost(taskdir=str(tmp_path / "task"))
    await host.setup_workdir()
    argv = build_conversation_argv(str(fake), no_leader=True, always_approve=True, fs_isolation=True)
    inner = " ".join(shlex.quote(a) for a in argv)
    cmd = ("exec " + inner) if use_exec else inner
    handle = await host.launch_subprocess(
        cmd, env=_isolation_env(host.workdir), cwd=host.workdir, stdin=True, merge_stderr=False,
    )
    # wait for the wrapper to exec through to the fake and record its pid
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    final_pid = int(pidfile.read_text().strip())
    return host, handle, final_pid


@pytest.mark.asyncio
async def test_exec_launch_is_reaped_on_teardown(tmp_path: Path):
    host, handle, final_pid = await _launch_fake(tmp_path, use_exec=True)
    # With exec, the wrapper→grok chain collapses onto the launched session leader.
    assert final_pid == handle.pid_like.pid, "exec should make grok the launched pid"
    assert _alive(final_pid)
    await host.terminate_subprocess(handle, aggressive=True)
    await asyncio.sleep(0.4)
    assert not _alive(final_pid), "grok must be reaped by killpg teardown"


@pytest.mark.asyncio
async def test_without_exec_grok_orphans(tmp_path: Path):
    """Control: without exec the tty-wrapper's setsid escapes the launched pgid,
    so killpg leaves grok orphaned. Guards against silently dropping ``exec``."""
    host, handle, final_pid = await _launch_fake(tmp_path, use_exec=False)
    try:
        assert final_pid != handle.pid_like.pid, "without exec grok escapes into its own pgid"
        await host.terminate_subprocess(handle, aggressive=True)
        await asyncio.sleep(0.4)
        assert _alive(final_pid), "control: escaped grok survives killpg (the bug exec fixes)"
    finally:
        try:
            os.kill(final_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
