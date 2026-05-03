"""Tests for LocalHost.launch_subprocess merge_stderr semantics."""

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


async def test_launch_subprocess_default_merges_stderr_into_stdout(localhost):
    handle = await localhost.launch_subprocess(
        "echo OUT; echo ERR 1>&2",
    )
    merged = await _drain(handle.stdout)
    assert b"OUT" in merged
    assert b"ERR" in merged
    assert handle.stderr is None


async def test_launch_subprocess_merge_stderr_false_separates_streams(localhost):
    handle = await localhost.launch_subprocess(
        "echo OUT; echo ERR 1>&2",
        merge_stderr=False,
    )
    assert handle.stderr is not None
    out = await _drain(handle.stdout)
    err = await _drain(handle.stderr)
    assert b"OUT" in out and b"ERR" not in out
    assert b"ERR" in err and b"OUT" not in err


async def test_launch_subprocess_merge_stderr_true_explicit_keeps_stderr_none(localhost):
    handle = await localhost.launch_subprocess(
        "echo hello",
        merge_stderr=True,
    )
    _ = await _drain(handle.stdout)
    assert handle.stderr is None
