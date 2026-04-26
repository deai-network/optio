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

    async def install_opencode_binary(self, *a, **kw):
        self.timeline.append("install_binary")
        self._maybe_fail("install_binary")

    async def ensure_opencode_installed(self, *a, **kw):
        self.timeline.append("install_binary")
        self._maybe_fail("install_binary")

    async def remove_file(self, *a, **kw):
        self.timeline.append("remove_file")

    async def opencode_version(self, *a, **kw):
        return None

    async def launch_opencode(self, *a, **kw):
        self.timeline.append("launch_opencode")
        self._maybe_fail("launch_opencode")
        raise RuntimeError("test never gets past launch")

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
        from optio_opencode.hook_context import RunResult
        self.timeline.append(f"run_command:{a[0]}")
        return RunResult(stdout="", stderr="", exit_code=0)


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

    with pytest.raises(RuntimeError, match="never gets past launch"):
        await run_opencode_session(ctx, config)

    install_idx = host.timeline.index("install_binary")
    before_idx = host.timeline.index("before_execute")
    launch_idx = host.timeline.index("launch_opencode")
    assert install_idx < before_idx < launch_idx
