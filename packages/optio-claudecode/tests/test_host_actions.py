"""Tests for optio-claudecode host actions (claude/ttyd install, launch)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from optio_claudecode import host_actions
from optio_host import RunResult


class _FakeHost:
    """Minimal Host shim that records run_command calls and returns scripted results."""

    def __init__(self, scripted_results, *, workdir: str = "/wd") -> None:
        self.commands: list[str] = []
        self._scripted = list(scripted_results)
        self.workdir = workdir

    async def run_command(self, cmd: str, *, check: bool = False) -> RunResult:
        self.commands.append(cmd)
        nxt = self._scripted.pop(0)
        if callable(nxt):
            return nxt(cmd)
        return nxt

    async def resolve_host_home(self) -> str:
        return "/root"


def _hook_ctx(host) -> MagicMock:
    """Build a minimal HookContext-shaped mock with .report_progress and ._host."""
    ctx = MagicMock()
    ctx._host = host
    ctx.report_progress = MagicMock()
    return ctx


_OK = RunResult(stdout="2.1.158 (Claude Code)\n", stderr="", exit_code=0)
_EMPTY = RunResult(stdout="", stderr="", exit_code=0)


def _resolve_cache(path="/home/u/.cache/optio-claudecode/versions"):
    return RunResult(stdout=path, stderr="", exit_code=0)


async def test_prep_cache_hit_relinks_no_install():
    # resolve cache, setup mkdir/ln, newest=9.9.9, relink bin, --version OK
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        RunResult(stdout="9.9.9\n", stderr="", exit_code=0),  # ls|sort|tail
        _EMPTY,                                              # ln bin -> versions/9.9.9
        _OK,                                                 # --version
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/wd/home/.local/bin/claude"
    joined = " ".join(host.commands)
    assert "curl" not in joined and "install.sh" not in joined  # no install
    assert "ln -sfn" in joined and "versions" in joined


async def test_prep_cache_override_skips_resolve():
    host = _FakeHost([
        _EMPTY,                                              # mkdir + ln (no resolve)
        RunResult(stdout="9.9.9\n", stderr="", exit_code=0),  # newest
        _EMPTY,                                              # relink
        _OK,                                                 # --version
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(
        ctx, install_if_missing=True, install_dir="/opt/claude-cache",
    )
    assert path == "/wd/home/.local/bin/claude"
    assert any("/opt/claude-cache" in c for c in host.commands)
    assert not any("printf" in c for c in host.commands)  # override → no resolve


async def test_prep_cache_miss_runs_install_through_symlink():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty (no version)
        _EMPTY,                                              # install.sh
        _OK,                                                 # --version after install
    ])
    ctx = _hook_ctx(host)
    path = await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert path == "/wd/home/.local/bin/claude"
    install = next(c for c in host.commands if "install.sh" in c)
    assert "HOME=/wd/home" in install and "curl" in install and "bash" in install


async def test_prep_cache_miss_install_disabled_raises():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=False)
    assert "install_if_missing" in str(exc.value) and "False" in str(exc.value)


async def test_prep_install_failure_propagates():
    host = _FakeHost([
        _resolve_cache(),
        _EMPTY,                                              # mkdir + ln
        _EMPTY,                                              # ls → empty
        RunResult(stdout="", stderr="curl: 404", exit_code=22),  # install.sh fails
    ])
    ctx = _hook_ctx(host)
    with pytest.raises(RuntimeError) as exc:
        await host_actions.ensure_claude_installed(ctx, install_if_missing=True)
    assert "install" in str(exc.value).lower()
    assert "22" in str(exc.value) or "404" in str(exc.value)


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
    # PATH prepends the isolated home's .local/bin (where claude is symlinked).
    assert any(
        a.startswith("PATH=/tmp/optio-claudecode-x/home/.local/bin:") for a in argv
    ), argv
    bash_idx = argv.index("bash", sep_idx)
    assert argv[bash_idx + 1] == "-c"
    bash_payload = argv[bash_idx + 2]
    assert "cd /tmp/optio-claudecode-x" in bash_payload
    assert "/opt/claude/claude" in bash_payload
    assert "--permission-mode bypassPermissions" in bash_payload
    # prep owns the bin symlink now — build_ttyd_argv no longer creates it.
    assert "ln -sf" not in bash_payload
    # PATH still prepends the isolated home's .local/bin.
    assert any(
        a.startswith("PATH=/tmp/optio-claudecode-x/home/.local/bin:") for a in argv
    ), argv
    # claude is run (not exec'd) so the wrapper can signal completion.
    assert "exec /opt/claude/claude" not in bash_payload
    assert "rc=$?" in bash_payload
    assert "echo DONE >>" in bash_payload
    assert "ERROR: claude exited" in bash_payload


def test_build_ttyd_argv_netns_wraps_claude_and_drops_root_unsafe_flags(monkeypatch):
    monkeypatch.setenv("OPTIO_CLAUDECODE_NETNS", "pasta --config-net --")
    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path="/opt/claude/claude",
        workdir="/tmp/wd",
        bind_iface="127.0.0.1",
        port=8765,
        extra_env=None,
        claude_flags=["--permission-mode", "bypassPermissions", "--model", "x"],
    )
    payload = argv[argv.index("bash") + 2]
    # claude is run via `bash -c` inside the isolation command (pasta can't
    # directly exec a $HOME binary); ttyd itself is NOT wrapped; and the
    # root-unsafe bypass flag is stripped (rootless netns runs as root).
    assert "pasta --config-net -- bash -c '/opt/claude/claude --model x'" in payload
    assert "bypassPermissions" not in payload
    assert argv[0] == "/usr/bin/ttyd"


def test_drop_root_unsafe_flags():
    assert host_actions._drop_root_unsafe_flags(
        ["--permission-mode", "bypassPermissions", "--model", "x"]
    ) == ["--model", "x"]
    assert host_actions._drop_root_unsafe_flags(
        ["--dangerously-skip-permissions", "--foo"]
    ) == ["--foo"]
    # A non-bypass --permission-mode value is preserved.
    assert host_actions._drop_root_unsafe_flags(
        ["--permission-mode", "acceptEdits"]
    ) == ["--permission-mode", "acceptEdits"]
    assert host_actions._drop_root_unsafe_flags([]) == []


def test_build_ttyd_argv_no_netns_by_default(monkeypatch):
    monkeypatch.delenv("OPTIO_CLAUDECODE_NETNS", raising=False)
    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path="/opt/claude/claude",
        workdir="/tmp/wd",
        bind_iface="127.0.0.1",
        port=8765,
        extra_env=None,
        claude_flags=[],
    )
    payload = argv[argv.index("bash") + 2]
    assert "pasta" not in payload
    assert "/opt/claude/claude" in payload


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
    # HOME + a PATH that prepends the isolated home's .local/bin; no other vars.
    assert env_section[0] == "env"
    assert "HOME=/tmp/cc/home" in env_section
    path_entries = [a for a in env_section if a.startswith("PATH=")]
    assert len(path_entries) == 1
    assert path_entries[0].startswith("PATH=/tmp/cc/home/.local/bin:")
    # Only env + HOME + PATH (no extra vars when extra_env=None).
    assert len(env_section) == 3


def _run_payload_with_fake_claude(tmp_path, exit_code: int) -> str:
    """Build the ttyd payload with a fake claude exiting ``exit_code``, run
    it under bash, and return the resulting optio.log contents."""
    import subprocess

    workdir = tmp_path / "wd"
    (workdir / "home").mkdir(parents=True)
    (workdir / "optio.log").write_text("")
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    fake.chmod(0o755)

    argv = host_actions.build_ttyd_argv(
        ttyd_path="/usr/bin/ttyd",
        claude_path=str(fake),
        workdir=str(workdir),
        bind_iface="127.0.0.1",
        port=0,
        extra_env=None,
        claude_flags=[],
    )
    payload = argv[argv.index("bash") + 2]
    subprocess.run(["bash", "-c", payload], check=True)
    return (workdir / "optio.log").read_text()


def test_payload_appends_done_when_claude_exits_clean(tmp_path):
    """claude exiting 0 without writing DONE → wrapper appends DONE, so the
    driver completes the session instead of the task hanging."""
    log = _run_payload_with_fake_claude(tmp_path, exit_code=0)
    assert "DONE" in log
    assert "ERROR" not in log


def test_payload_appends_error_when_claude_exits_nonzero(tmp_path):
    log = _run_payload_with_fake_claude(tmp_path, exit_code=3)
    assert "ERROR: claude exited 3" in log
    assert "DONE" not in log


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


def test_build_claude_flags_no_continue_by_default():
    flags = host_actions.build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert "--continue" not in flags


def test_build_claude_flags_appends_continue_when_resuming():
    flags = host_actions.build_claude_flags(
        permission_mode="bypassPermissions",
        allowed_tools=None, disallowed_tools=None,
        resuming=True,
    )
    assert "--continue" in flags
    assert flags.index("--permission-mode") < flags.index("--continue")


def test_build_claude_flags_continue_is_last():
    flags = host_actions.build_claude_flags(
        permission_mode=None,
        allowed_tools=["Read"],
        disallowed_tools=None,
        resuming=True,
    )
    assert flags[-1] == "--continue"


def test_auto_start_args_fresh_appends_prompt():
    args = host_actions.build_auto_start_args(auto_start=True, resuming=False)
    assert args == [host_actions.AUTO_START_PROMPT]
    assert "Read CLAUDE.md" in args[0]


def test_auto_start_args_suppressed_on_resume():
    # Gated on `resuming`, NOT transcript presence: ANY resume suppresses the
    # kickoff, including a no-transcript resume (which previously re-triggered
    # the task — the kickoff would restart it instead of continuing).
    assert host_actions.build_auto_start_args(auto_start=True, resuming=True) == []


def test_auto_start_args_off_by_default():
    assert host_actions.build_auto_start_args(auto_start=False, resuming=False) == []


def test_focus_mode_off_passthrough():
    cfg, env = host_actions.build_focus_mode(focus_mode=False, claude_config={"x": 1})
    assert cfg == {"x": 1}
    assert env == {}


def test_focus_mode_on_layers_settings_and_env():
    cfg, env = host_actions.build_focus_mode(focus_mode=True, claude_config={"x": 1})
    assert cfg == {"x": 1, "tui": "fullscreen", "viewMode": "focus"}
    assert env == {"CLAUDE_CODE_NO_FLICKER": "1"}


def test_focus_mode_on_with_none_config():
    cfg, env = host_actions.build_focus_mode(focus_mode=True, claude_config=None)
    assert cfg == {"tui": "fullscreen", "viewMode": "focus"}
    assert env == {"CLAUDE_CODE_NO_FLICKER": "1"}
