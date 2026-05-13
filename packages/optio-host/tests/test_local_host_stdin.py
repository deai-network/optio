"""Tests for LocalHost.launch_subprocess stdin support."""

import asyncio
import pytest

from optio_host.host import LocalHost, ProcessHandle


@pytest.fixture
def localhost(tmp_path):
    taskdir = tmp_path / "task"
    taskdir.mkdir()
    workdir = taskdir / "workdir"
    workdir.mkdir()
    return LocalHost(taskdir=str(taskdir))


async def _drain(it) -> bytes:
    out = b""
    async for chunk in it:
        out += chunk
    return out


async def test_process_handle_stdin_field_default_none(localhost):
    """ProcessHandle exposes a `stdin` field that defaults to None when
    stdin=False (the default) at launch."""
    handle = await localhost.launch_subprocess("echo hello")
    assert hasattr(handle, "stdin")
    assert handle.stdin is None


async def test_launch_subprocess_stdin_true_pipes_input_to_subprocess(localhost):
    """When stdin=True, ProcessHandle.stdin accepts bytes; the subprocess
    sees them on its stdin. cat echoes stdin → stdout."""
    handle = await localhost.launch_subprocess("cat", stdin=True)
    assert handle.stdin is not None

    handle.stdin.write(b"line one\n")
    handle.stdin.write(b"line two\n")
    await handle.stdin.drain()
    handle.stdin.close()
    await handle.stdin.wait_closed()

    out = await _drain(handle.stdout)
    assert b"line one" in out
    assert b"line two" in out


async def test_launch_subprocess_stdin_true_close_signals_eof(localhost):
    """Closing stdin causes the subprocess (which reads-until-EOF) to
    finish naturally."""
    handle = await localhost.launch_subprocess(
        "while read line; do echo got: $line; done",
        stdin=True,
    )
    handle.stdin.write(b"hello\n")
    handle.stdin.write(b"world\n")
    await handle.stdin.drain()
    handle.stdin.close()
    await handle.stdin.wait_closed()

    out = await _drain(handle.stdout)
    assert b"got: hello" in out
    assert b"got: world" in out

    proc = handle.pid_like
    await asyncio.wait_for(proc.wait(), timeout=2.0)
    assert proc.returncode == 0


async def test_launch_subprocess_stdin_false_leaves_stdin_none(localhost):
    """Default stdin=False: ProcessHandle.stdin stays None; subprocess
    inherits parent stdin (we just don't expose a writer)."""
    handle = await localhost.launch_subprocess("echo hello", stdin=False)
    assert handle.stdin is None
