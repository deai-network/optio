"""RemoteHost.launch_subprocess stdin support over a real SSH channel.

Docker-gated integration test (mirrors optio-opencode's sshd harness, on a
different host port so the two suites can run side by side). This is the
remote twin of test_local_host_stdin.py — it covers the one remote-specific
risk of stdin piping: asyncssh's SSHWriter write/drain/EOF semantics on a
live exec channel, which the conversation-mode consumers (optio-claudecode)
rely on for their bidirectional NDJSON sessions.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from optio_host.host import RemoteHost
from optio_host.types import SSHConfig


from optio_host.testing import have_docker, sshd_container

HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"

pytestmark = pytest.mark.skipif(not have_docker(), reason="Docker not available")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sshd():
    """Isolation-safe sshd container (per-worker project + ephemeral port)."""
    async with sshd_container(COMPOSE, "optio-host") as info:
        yield info


@pytest_asyncio.fixture
async def remote_host(sshd):
    h = RemoteHost(
        ssh_config=SSHConfig(
            host=sshd["host"],
            user=sshd["user"],
            key_path=sshd["key_path"],
            port=sshd["port"],
        ),
        taskdir=f"/tmp/optio-host-stdin-{uuid.uuid4().hex[:12]}",
    )
    await h.connect()
    await h.setup_workdir()
    try:
        yield h
    finally:
        try:
            await h.cleanup_taskdir(aggressive=True)
        except Exception:
            pass
        await h.disconnect()


async def _drain(it) -> bytes:
    out = b""
    async for chunk in it:
        out += chunk if isinstance(chunk, bytes) else chunk.encode()
    return out


async def test_remote_stdin_default_none(remote_host):
    handle = await remote_host.launch_subprocess("echo hello")
    assert handle.stdin is None
    out = await _drain(handle.stdout)
    assert b"hello" in out


async def test_remote_stdin_pipes_input_and_eof(remote_host):
    """cat over SSH: bytes written to ProcessHandle.stdin come back on
    stdout; write_eof/close lets the process finish naturally."""
    handle = await remote_host.launch_subprocess("cat", stdin=True)
    assert handle.stdin is not None

    handle.stdin.write(b"line one\n")
    handle.stdin.write(b"line two\n")
    await handle.stdin.drain()
    handle.stdin.write_eof()

    out = await _drain(handle.stdout)
    assert b"line one" in out
    assert b"line two" in out


async def test_remote_stdin_interleaved_request_response(remote_host):
    """The conversation-mode shape: write one line, read the reply, write the
    next line on the SAME channel — proves the write side stays usable while
    the read side is being consumed (no EOF needed between turns)."""
    handle = await remote_host.launch_subprocess(
        "while read line; do echo got:$line; done", stdin=True,
    )

    async def read_line() -> bytes:
        it = handle.stdout.__aiter__()
        chunk = await asyncio.wait_for(it.__anext__(), timeout=10)
        return chunk if isinstance(chunk, bytes) else chunk.encode()

    handle.stdin.write(b"first\n")
    await handle.stdin.drain()
    assert b"got:first" in await read_line()

    handle.stdin.write(b"second\n")
    await handle.stdin.drain()
    assert b"got:second" in await read_line()

    handle.stdin.write_eof()
    await _drain(handle.stdout)
