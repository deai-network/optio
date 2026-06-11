"""Tests for before_execute / after_execute hook integration with run_opencode_session.

Uses a fake host to record call ordering without launching opencode subprocesses
or going over SSH. The fake intentionally raises in launch_opencode so the
session terminates early — this is sufficient to validate hook ordering.
Successful-path coverage is manual via the demo task (Task 23).
"""

import asyncio
import os
import sys

import pytest

from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig


pytestmark = pytest.mark.asyncio


# A tiny ProcessContext stand-in for tests that don't need MongoDB.
class _MinimalCtx:
    def __init__(self):
        self.process_id = "test-process"
        self.params = {}
        self.metadata = {}
        self.services = {}
        self.resume = False
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))

    def should_continue(self):
        return True

    def set_widget_upstream(self, *a, **kw):
        pass

    def set_widget_data(self, *a, **kw):
        pass

    _db = None
    _prefix = "test"


class _RecordingFakeHost:
    """A minimal Host stand-in that records call order."""

    def __init__(self, *, fail_in: str | None = None):
        self.workdir = "/wd"
        self.taskdir = "/wd"
        self.timeline: list[str] = []
        self._fail_in = fail_in
        self._connected = False

    def _maybe_fail(self, name):
        if self._fail_in == name:
            raise RuntimeError(f"injected failure in {name}")

    async def connect(self):
        self.timeline.append("connect")
        self._connected = True
        self._maybe_fail("connect")

    @property
    def is_connected(self):
        return self._connected

    async def setup_workdir(self):
        self.timeline.append("setup_workdir")
        self._maybe_fail("setup_workdir")

    async def write_text(self, *a, **kw):
        self.timeline.append(f"write_text:{a[0]}")
        self._maybe_fail("write_text")

    async def remove_file(self, *a, **kw):
        self.timeline.append("remove_file")

    async def cleanup_taskdir(self, *a, **kw):
        self.timeline.append("cleanup_taskdir")

    async def disconnect(self):
        self.timeline.append("disconnect")
        self._connected = False

    async def resolve_host_home(self):
        return "/root"

    async def put_file_to_host(self, *a, **kw):
        self.timeline.append("put_file_to_host")

    async def fetch_bytes_from_host(self, *a, **kw):
        self.timeline.append("fetch_bytes_from_host")
        return b""

    async def run_command(self, *a, **kw):
        from optio_host.host import RunResult
        self.timeline.append(f"run_command:{a[0]}")
        return RunResult(stdout="", stderr="", exit_code=0)


def _patch_host_actions(monkeypatch, host):
    """Patch host_actions free functions to record on the fake host's timeline.

    Replaces the bits that would otherwise need a real Host implementation
    with appends-and-fail stubs. ``launch_opencode`` always raises so the
    session terminates early — sufficient to validate hook ordering.
    """
    from optio_opencode import host_actions

    async def _ensure(_host, **kwargs):
        host.timeline.append("install_binary")
        return "opencode"

    async def _version(_host, *, opencode_executable="opencode"):
        return None

    async def _launch(_host, _password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None):
        host.timeline.append("launch_opencode")
        raise RuntimeError("test never gets past launch")

    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)
    monkeypatch.setattr(host_actions, "opencode_version", _version)
    monkeypatch.setattr(host_actions, "launch_opencode", _launch)


async def test_before_execute_runs_after_install_before_launch(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def my_before(hook_ctx):
        host.timeline.append("before_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=my_before,
    )

    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config, process_id: host,
    )
    _patch_host_actions(monkeypatch, host)

    with pytest.raises(RuntimeError, match="never gets past launch"):
        await run_opencode_session(ctx, config)

    install_idx = host.timeline.index("install_binary")
    before_idx = host.timeline.index("before_execute")
    launch_idx = host.timeline.index("launch_opencode")
    assert install_idx < before_idx < launch_idx


async def test_after_execute_runs_when_before_execute_raises(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def failing_before(hook_ctx):
        host.timeline.append("before_execute")
        raise RuntimeError("before fails")

    async def my_after(hook_ctx):
        host.timeline.append("after_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=failing_before,
        after_execute=my_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config, process_id: host,
    )
    _patch_host_actions(monkeypatch, host)

    with pytest.raises(RuntimeError, match="before fails"):
        await run_opencode_session(ctx, config)

    # before_execute and after_execute both ran; cleanup ran.
    assert "before_execute" in host.timeline
    assert "after_execute" in host.timeline
    assert "launch_opencode" not in host.timeline  # never launched
    assert "cleanup_taskdir" in host.timeline


async def test_after_execute_skipped_when_failure_before_host_connect(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost(fail_in="connect")
    ctx = _MinimalCtx()

    async def my_after(hook_ctx):
        host.timeline.append("after_execute")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        after_execute=my_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config, process_id: host,
    )
    _patch_host_actions(monkeypatch, host)

    with pytest.raises(RuntimeError, match="injected failure"):
        await run_opencode_session(ctx, config)

    # Before host connected, hook_ctx wasn't built — after_execute is skipped.
    assert "after_execute" not in host.timeline


async def test_after_execute_failure_does_not_shadow_session_error(tmp_workdir, monkeypatch):
    """If session is already failing, an after_execute exception is logged
    via report_progress but doesn't override the original cause."""
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()

    async def failing_before(hook_ctx):
        raise RuntimeError("primary failure")

    async def failing_after(hook_ctx):
        raise RuntimeError("secondary after failure")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        before_execute=failing_before,
        after_execute=failing_after,
    )
    monkeypatch.setattr(
        "optio_opencode.session._build_host", lambda config, process_id: host,
    )
    _patch_host_actions(monkeypatch, host)

    with pytest.raises(RuntimeError, match="primary failure"):
        await run_opencode_session(ctx, config)

    # The secondary exception was reported via ctx.report_progress.
    assert any(
        "after_execute callback raised" in str(c[2])
        for c in ctx.calls
    )


async def test_on_deliverable_receives_hook_ctx_and_can_use_host_primitives(tmp_workdir, monkeypatch):
    host = _RecordingFakeHost()
    ctx = _MinimalCtx()
    received = []

    async def cb(hook_ctx, path, text):
        received.append((path, text))
        # The hook_ctx must expose host primitives — exercise one.
        await hook_ctx.run_on_host("noop")

    config = OpencodeTaskConfig(
        consumer_instructions="x",
        on_deliverable=cb,
    )
    # We don't run a full session here; we directly invoke
    # _deliverable_fetch_loop with a constructed HookContext.
    from optio_agents.protocol.session import _deliverable_fetch_loop
    from optio_agents import HookContext

    queue = asyncio.Queue()
    await queue.put(("/wd/deliverables/x.txt", "x.txt"))

    # Patch host.fetch_bytes_from_host to return canned content; the
    # free fetch_deliverable_text helper used by _deliverable_fetch_loop
    # decodes those bytes as UTF-8.
    async def _fake_fetch(_path):
        return b"deliverable text"
    host.fetch_bytes_from_host = _fake_fetch  # type: ignore[attr-defined]

    hook_ctx = HookContext(ctx, host)
    task = asyncio.create_task(
        _deliverable_fetch_loop(host, cb, queue, ctx, hook_ctx)
    )
    await queue.join()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert received == [("x.txt", "deliverable text")]
    assert "run_command:noop" in host.timeline
