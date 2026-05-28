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


def test_build_claude_flags_all_none():
    flags = host_actions.build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert flags == []


def test_build_claude_flags_permission_mode_only():
    flags = host_actions.build_claude_flags(
        permission_mode="bypassPermissions",
        allowed_tools=None, disallowed_tools=None,
    )
    assert flags == ["--permission-mode", "bypassPermissions"]


def test_build_claude_flags_allowed_disallowed_joined_with_commas():
    flags = host_actions.build_claude_flags(
        permission_mode=None,
        allowed_tools=["Read", "Write"],
        disallowed_tools=["Bash"],
    )
    assert flags == [
        "--allowed-tools", "Read,Write",
        "--disallowed-tools", "Bash",
    ]


def test_build_claude_flags_all_three():
    flags = host_actions.build_claude_flags(
        permission_mode="acceptEdits",
        allowed_tools=["Read"],
        disallowed_tools=["Bash", "Write"],
    )
    assert flags == [
        "--permission-mode", "acceptEdits",
        "--allowed-tools", "Read",
        "--disallowed-tools", "Bash,Write",
    ]


def test_build_claude_flags_empty_list_treated_as_none():
    """An empty list is equivalent to None: no flag emitted."""
    flags = host_actions.build_claude_flags(
        permission_mode=None,
        allowed_tools=[],
        disallowed_tools=[],
    )
    assert flags == []


async def test_plant_home_files_credentials_dict():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/optio-claudecode-abc"

    await host_actions.plant_home_files(
        host,
        credentials_json={"oauth_token": "secret"},
        claude_config=None,
    )

    paths_written = [c.args[0] for c in host.write_text.call_args_list]
    assert "home/.claude/.credentials.json" in paths_written
    cred_call = [c for c in host.write_text.call_args_list
                 if c.args[0] == "home/.claude/.credentials.json"][0]
    assert json.loads(cred_call.args[1]) == {"oauth_token": "secret"}

    chmod_cmds = [c.args[0] for c in host.run_command.call_args_list
                  if "chmod" in c.args[0]]
    assert any("600" in c and "credentials.json" in c for c in chmod_cmds)


async def test_plant_home_files_credentials_bytes_kept_verbatim():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    raw = b'{"opaque":"blob"}'
    await host_actions.plant_home_files(
        host, credentials_json=raw, claude_config=None,
    )

    cred_call = [c for c in host.write_text.call_args_list
                 if c.args[0] == "home/.claude/.credentials.json"][0]
    assert cred_call.args[1] == raw.decode("utf-8")


async def test_plant_home_files_settings_json():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    settings = {"permissions": {"allow": ["Read"]}}
    await host_actions.plant_home_files(
        host, credentials_json=None, claude_config=settings,
    )

    paths_written = [c.args[0] for c in host.write_text.call_args_list]
    assert "home/.claude/settings.json" in paths_written
    settings_call = [c for c in host.write_text.call_args_list
                     if c.args[0] == "home/.claude/settings.json"][0]
    assert json.loads(settings_call.args[1]) == settings


async def test_plant_home_files_none_writes_nothing():
    host = MagicMock()
    host.write_text = AsyncMock()
    host.run_command = AsyncMock(return_value=RunResult(stdout="", stderr="", exit_code=0))
    host.workdir = "/tmp/x"

    await host_actions.plant_home_files(
        host, credentials_json=None, claude_config=None,
    )

    # No files written; mkdir -p still runs for home/.claude
    assert host.write_text.call_count == 0


def test_build_ttyd_argv_basic():
    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path="/opt/claude/claude",
        workdir="/tmp/optio-claudecode-x",
        bind_iface="127.0.0.1",
        port=8765,
        extra_env={"ANTHROPIC_BASE_URL": "https://api.example.com"},
        claude_flags=["--permission-mode", "bypassPermissions"],
    )
    assert argv[0] == "/usr/bin/ttyd"
    assert "-W" in argv
    assert "-i" in argv and "127.0.0.1" in argv
    assert "-p" in argv and "8765" in argv
    assert "-m" in argv and "1" in argv
    assert "-T" in argv and "xterm-256color" in argv
    assert "--" in argv
    sep_idx = argv.index("--")
    assert argv[sep_idx + 1] == "env"
    assert any(a.startswith("HOME=") for a in argv[sep_idx + 1:])
    assert "HOME=/tmp/optio-claudecode-x/home" in argv
    assert "ANTHROPIC_BASE_URL=https://api.example.com" in argv
    bash_idx = argv.index("bash", sep_idx)
    assert argv[bash_idx + 1] == "-c"
    bash_payload = argv[bash_idx + 2]
    assert "cd /tmp/optio-claudecode-x" in bash_payload
    assert "exec /opt/claude/claude" in bash_payload
    assert "--permission-mode bypassPermissions" in bash_payload


@pytest.mark.parametrize("banner,expected_port", [
    # ttyd 1.7.x with libwebsockets logging prefix + colon after "port"
    ("[2026/05/28 23:20:13:3422] N:  Listening on port: 33449", 33449),
    # Older / simpler ttyd builds
    ("Listening on port 7681", 7681),
    # Some forks log a URL instead of "port"
    ("[INFO] tty.c:131 listening on http://127.0.0.1:7681/", 7681),
    # URL variant ending with whitespace
    ("Now listening at http://0.0.0.0:8080 hello", 8080),
])
def test_ttyd_ready_regex_matches_known_banners(banner: str, expected_port: int):
    m = host_actions._TTYD_READY_RE.search(banner)
    assert m is not None, f"regex failed on banner: {banner!r}"
    port = int(m.group(1) or m.group(2))
    assert port == expected_port


@pytest.mark.parametrize("noise", [
    # ttyd internal tag lines contain `<ip>|<port>` but no "port" word
    "[wsi|1|listen|default|127.0.0.1|33449]",
    # Unrelated stdout
    "something else entirely",
    "",
])
def test_ttyd_ready_regex_ignores_noise(noise: str):
    assert host_actions._TTYD_READY_RE.search(noise) is None


def test_build_ttyd_argv_no_extra_env():
    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path="/opt/claude/claude",
        workdir="/tmp/cc",
        bind_iface="0.0.0.0",
        port=9000,
        extra_env=None,
        claude_flags=[],
    )
    sep_idx = argv.index("--")
    env_section = argv[sep_idx + 1:argv.index("bash", sep_idx)]
    # Only HOME assignment (no extra vars when extra_env=None)
    assert env_section == ["env", "HOME=/tmp/cc/home"]


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
