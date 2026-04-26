"""Tests for HookContext foundations: dataclasses + path resolver."""

import pytest

from optio_opencode.hook_context import (
    HostCommandError,
    RunResult,
    _resolve_target_path,
)


def test_run_result_fields():
    r = RunResult(stdout="hi\n", stderr="", exit_code=0)
    assert r.stdout == "hi\n"
    assert r.stderr == ""
    assert r.exit_code == 0


def test_host_command_error_str_includes_exit_code_and_stderr():
    err = HostCommandError(
        command="false", exit_code=1, stdout="", stderr="boom\n",
    )
    s = str(err)
    assert "exit 1" in s
    assert "boom" in s
    assert "false" in s


def test_resolve_target_path_workdir_relative():
    out = _resolve_target_path("data/foo.yaml", "/wd", "/home/u")
    assert out == "/wd/data/foo.yaml"


def test_resolve_target_path_absolute_passthrough():
    assert _resolve_target_path("/usr/local/bin/tool", "/wd", "/home/u") == "/usr/local/bin/tool"


def test_resolve_target_path_home_relative_expanded():
    assert _resolve_target_path("~/.local/bin/tool", "/wd", "/home/u") == "/home/u/.local/bin/tool"


def test_resolve_target_path_bare_tilde_expanded():
    assert _resolve_target_path("~", "/wd", "/home/u") == "/home/u"


def test_resolve_target_path_rejects_empty():
    with pytest.raises(ValueError):
        _resolve_target_path("", "/wd", "/home/u")


def test_resolve_target_path_rejects_dotdot_in_workdir_relative():
    with pytest.raises(ValueError):
        _resolve_target_path("data/../../etc/passwd", "/wd", "/home/u")


def test_resolve_target_path_rejects_workdir_relative_escape():
    # Even after normalization, must stay inside workdir.
    with pytest.raises(ValueError):
        _resolve_target_path("../outside", "/wd", "/home/u")


def test_resolve_target_path_dotdot_allowed_in_absolute():
    # Absolute paths are consumer-trusted; .. is fine there.
    assert _resolve_target_path("/usr/../tmp/x", "/wd", "/home/u") == "/usr/../tmp/x"


def test_resolve_target_path_dotdot_allowed_in_home_relative():
    # Home-relative is also consumer-trusted.
    assert _resolve_target_path("~/foo/../bar", "/wd", "/home/u") == "/home/u/foo/../bar"


import asyncio

from optio_opencode.hook_context import HookContext, HookContextProtocol


class _FakeCtx:
    def __init__(self):
        self.process_id = "p"
        self.params = {"k": "v"}
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"


def test_hook_context_delegates_attributes():
    ctx = _FakeCtx()
    host = _FakeHost()
    h = HookContext(ctx, host)
    # Process-context attributes flow through __getattr__.
    assert h.process_id == "p"
    assert h.params == {"k": "v"}
    h.report_progress(50, "halfway")
    assert ctx.calls == [("rp", 50, "halfway")]


def test_hook_context_protocol_is_protocol():
    # Smoke test: the Protocol exists and has the expected method names.
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    expected = {
        "copy_file", "run_on_host", "read_from_host", "read_text_from_host",
        "report_progress", "should_continue", "params", "metadata",
    }
    assert expected <= methods


class _FakeRunHost:
    def __init__(self, results):
        self.workdir = "/wd"
        self._results = list(results)
        self.calls = []

    async def run_command(self, command, *, cwd=None, env=None):
        self.calls.append((command, cwd, env))
        return self._results.pop(0)


async def test_run_on_host_check_true_returns_stdout_on_success():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="hi\n", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("echo hi")
    assert out == "hi\n"


async def test_run_on_host_check_true_raises_on_nonzero():
    from optio_opencode.hook_context import HostCommandError, RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="boom", exit_code=2)])
    h = HookContext(_FakeCtx(), host)
    with pytest.raises(HostCommandError) as ei:
        await h.run_on_host("false")
    assert ei.value.exit_code == 2
    assert ei.value.stderr == "boom"


async def test_run_on_host_check_false_returns_result_object():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="oops", exit_code=3)])
    h = HookContext(_FakeCtx(), host)
    res = await h.run_on_host("false", check=False)
    assert res.exit_code == 3
    assert res.stderr == "oops"


async def test_run_on_host_capture_stderr_merges_into_returned_stdout():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="o", stderr="e", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("cmd", capture_stderr=True)
    assert out == "oe"


async def test_run_on_host_cwd_is_forwarded():
    from optio_opencode.hook_context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    await h.run_on_host("pwd", cwd="/elsewhere")
    assert host.calls[0][1] == "/elsewhere"
