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
