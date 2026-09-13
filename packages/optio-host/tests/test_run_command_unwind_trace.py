"""Tests for RemoteHost.run_command's unwind-gated OPTIO_CANCEL_TRACE trace.

run_command has ~250 call sites, several of them polled once per second for
a session's entire lifetime (tmux_session_alive and siblings). Tracing every
one of those under OPTIO_CANCEL_TRACE=1 floods the log, so run_command only
traces while `unwind_tracing()` (a module-level ContextVar) marks a
task-cancel/snapshot unwind in progress. These tests exercise that gate
directly against a fake asyncssh connection — no live SSH needed.
"""

import logging

import pytest

from optio_host import host as host_mod
from optio_host.host import RemoteHost, unwind_tracing
from optio_host.types import SSHConfig


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_status: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class _FakeConn:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, full_command: str, check: bool = False):
        self.commands.append(full_command)
        return _FakeResult(stdout="out", stderr="", exit_status=0)


def _make_host() -> RemoteHost:
    host = RemoteHost(
        ssh_config=SSHConfig(host="h", user="u", key_path="/k", port=22),
        taskdir="/tmp/optio-host-unwind-trace-test",
    )
    host._conn = _FakeConn()
    return host


async def test_no_trace_when_flag_on_but_no_unwind_marked(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    host = _make_host()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        await host.run_command("echo hi")
    assert caplog.records == []


async def test_traces_start_and_done_inside_unwind_tracing(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    host = _make_host()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        with unwind_tracing():
            await host.run_command("echo hi")
    messages = [r.getMessage() for r in caplog.records]
    assert any("run_command START" in m and "echo hi" in m for m in messages)
    assert any("run_command DONE" in m and "echo hi" in m for m in messages)


async def test_no_trace_when_unwind_tracing_but_flag_off(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", False)
    host = _make_host()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        with unwind_tracing():
            await host.run_command("echo hi")
    assert caplog.records == []


async def test_no_trace_outside_the_unwind_tracing_block(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    host = _make_host()
    with unwind_tracing():
        pass  # var is set and reset here, before run_command is ever called
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        await host.run_command("echo hi")
    assert caplog.records == []


async def test_unwind_tracing_var_resets_after_the_block():
    assert host_mod._unwind_tracing.get() is False
    with unwind_tracing():
        assert host_mod._unwind_tracing.get() is True
    assert host_mod._unwind_tracing.get() is False


async def test_unwind_tracing_var_resets_even_when_the_block_raises():
    assert host_mod._unwind_tracing.get() is False
    with pytest.raises(ValueError, match="boom"):
        with unwind_tracing():
            assert host_mod._unwind_tracing.get() is True
            raise ValueError("boom")
    assert host_mod._unwind_tracing.get() is False
