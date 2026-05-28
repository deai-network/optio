"""Tests for optio-claudecode host actions (claude/ttyd install, launch)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from optio_claudecode import host_actions
from optio_host import RunResult


class _FakeHost:
    """Minimal Host shim that records run_command calls and returns scripted results."""

    def __init__(self, scripted_results, host_home: str = "/root") -> None:
        self.commands: list[str] = []
        self._scripted = list(scripted_results)
        self._host_home = host_home

    async def resolve_host_home(self) -> str:
        return self._host_home

    async def run_command(self, cmd: str, *, check: bool = False) -> RunResult:
        self.commands.append(cmd)
        nxt = self._scripted.pop(0)
        if callable(nxt):
            return nxt(cmd)
        return nxt


def _hook_ctx(host) -> MagicMock:
    """Build a minimal HookContext-shaped mock with .report_progress and ._host."""
    ctx = MagicMock()
    ctx._host = host
    ctx.report_progress = MagicMock()
    return ctx


async def test_ensure_claude_installed_present():
    host = _FakeHost([
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/claude"
    assert len(host.commands) == 1
    assert "/root/.local/bin/claude" in host.commands[0]


async def test_ensure_claude_installed_missing_install_disabled_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="No such file", exit_code=1),
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=False)
    assert "install_if_missing" in str(exc_info.value)
    assert "False" in str(exc_info.value)


async def test_ensure_claude_installed_missing_runs_vendor_install():
    host = _FakeHost([
        RunResult(stdout="", stderr="No such file", exit_code=1),
        RunResult(stdout="Installation complete\n", stderr="", exit_code=0),
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True,
    )
    assert path == "/root/.local/bin/claude"
    assert len(host.commands) == 3
    assert "claude.ai/install.sh" in host.commands[1]
    assert "bash" in host.commands[1]


async def test_ensure_claude_installed_explicit_install_dir_used():
    host = _FakeHost([
        RunResult(stdout="2.1.153 (Claude Code)\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True, install_dir="/opt/claude",
    )
    assert path == "/opt/claude/claude"
    assert "/opt/claude/claude" in host.commands[0]


async def test_ensure_claude_installed_install_failure_propagates():
    host = _FakeHost([
        RunResult(stdout="", stderr="No such file", exit_code=1),
        RunResult(stdout="", stderr="curl: 404 Not Found", exit_code=22),
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert "install" in str(exc_info.value).lower()
    assert "22" in str(exc_info.value) or "404" in str(exc_info.value)


async def test_ensure_ttyd_installed_present():
    host = _FakeHost([
        RunResult(stdout="ttyd version 1.7.7-9d2", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    path = await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/ttyd"
    assert ctx.download_file.call_count == 0


async def test_ensure_ttyd_installed_missing_install_disabled_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="not found", exit_code=1),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_ttyd_installed(ctx, install_if_missing=False)
    assert "install_ttyd_if_missing" in str(exc_info.value)


async def test_ensure_ttyd_installed_downloads_from_github_releases():
    host = _FakeHost([
        RunResult(stdout="", stderr="not found", exit_code=1),
        RunResult(stdout="x86_64\n", stderr="", exit_code=0),
        RunResult(stdout="Linux\n", stderr="", exit_code=0),
        RunResult(stdout="", stderr="", exit_code=0),  # mkdir -p
        RunResult(stdout="", stderr="", exit_code=0),  # chmod +x
        RunResult(stdout="ttyd version 1.7.7-9d2", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    path = await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert path == "/root/.local/bin/ttyd"
    assert ctx.download_file.call_count == 1
    download_url = ctx.download_file.call_args.args[0]
    assert "github.com/tsl0922/ttyd" in download_url
    assert "ttyd.x86_64" in download_url
    download_target = ctx.download_file.call_args.args[1]
    assert download_target == "/root/.local/bin/ttyd"


async def test_ensure_ttyd_installed_unsupported_os_raises():
    host = _FakeHost([
        RunResult(stdout="", stderr="not found", exit_code=1),
        RunResult(stdout="x86_64\n", stderr="", exit_code=0),
        RunResult(stdout="Darwin\n", stderr="", exit_code=0),
    ])
    ctx = _hook_ctx(host)
    ctx.download_file = AsyncMock()
    with pytest.raises(RuntimeError) as exc_info:
        await host_actions.ensure_ttyd_installed(ctx, install_if_missing=True)
    assert "Darwin" in str(exc_info.value) or "darwin" in str(exc_info.value).lower()
    assert "macOS" in str(exc_info.value) or "unsupported" in str(exc_info.value).lower()
