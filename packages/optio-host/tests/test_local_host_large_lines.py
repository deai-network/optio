"""Tests for LocalHost handling subprocess output lines >64 KiB.

Default asyncio.StreamReader limit is 64 KiB; readline() raises
LimitOverrunError when a separator is found beyond that. LocalHost
must lift that cap so callers iterating handle.stdout (recipe-runner's
iter_ndjson, etc.) can consume large NDJSON events.
"""

import asyncio
import shlex
import sys

import pytest

from optio_host.host import LocalHost


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


async def test_launch_subprocess_handles_line_larger_than_default_limit(localhost):
    payload_len = 200_000  # well over the 64 KiB asyncio default
    py = shlex.quote(sys.executable)
    cmd = (
        f"{py} -c "
        f"\"import sys; sys.stdout.buffer.write(b'X' * {payload_len} + b'\\n')\""
    )
    handle = await localhost.launch_subprocess(cmd, merge_stderr=False)
    out = await _drain(handle.stdout)
    assert len(out) == payload_len + 1  # +1 for the trailing newline
    assert out == b"X" * payload_len + b"\n"


async def test_tail_file_yields_line_larger_than_default_limit(localhost, tmp_path):
    log_path = tmp_path / "large.log"
    payload = "Y" * 200_000
    log_path.write_text(payload + "\n")

    received: list[str] = []

    async def _consume():
        async for line in localhost.tail_file(str(log_path)):
            received.append(line)
            if received:
                break  # one line is enough to prove the cap is lifted

    # tail -F never exits on its own; bound the test with a timeout and then
    # terminate the underlying tail process.
    try:
        await asyncio.wait_for(_consume(), timeout=5.0)
    finally:
        if localhost._tail_proc is not None and localhost._tail_proc.returncode is None:
            localhost._tail_proc.terminate()
            try:
                await asyncio.wait_for(localhost._tail_proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                localhost._tail_proc.kill()
                await localhost._tail_proc.wait()

    assert received == [payload]
