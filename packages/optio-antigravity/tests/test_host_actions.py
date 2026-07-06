from optio_antigravity.host_actions import (
    _build_agy_shell_command,
    _isolation_env,
    build_agy_flags,
    build_host,
)


def test_isolation_env_all_keys():
    """_isolation_env is the single source of truth for a task's HOME/XDG
    identity — agy's ~/.gemini state tree lands in the per-task home."""
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
    }


def test_build_shell_command_uses_isolation_env():
    """_build_agy_shell_command emits every _isolation_env key (plus PATH)."""
    env, _cmd = _build_agy_shell_command(
        agy_path="/x/agy", workdir="/w/task", extra_env=None, agy_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    assert any(a.startswith("PATH=") for a in env)


def test_env_isolation_and_done_error():
    env, cmd = _build_agy_shell_command(
        agy_path="/x/agy", workdir="/w/task", extra_env=None,
        agy_flags=["--model", "gemini-2.5-pro"],
    )
    assert "HOME=/w/task/home" in env
    assert "echo DONE" in cmd and "ERROR: agy exited" in cmd
    assert "--model" in cmd and "gemini-2.5-pro" in cmd


def test_build_agy_flags_skip_permissions_alias():
    """The claudecode-style ``bypassPermissions`` maps to agy's binary
    ``--dangerously-skip-permissions`` flag (agy has no --permission-mode)."""
    flags = build_agy_flags(
        permission_mode="bypassPermissions", model=None, resuming=False,
    )
    assert "--dangerously-skip-permissions" in flags
    assert "--permission-mode" not in flags


def test_build_agy_flags_default_permission_no_skip():
    flags = build_agy_flags(
        permission_mode="default", model=None, resuming=False,
    )
    assert "--dangerously-skip-permissions" not in flags


def test_build_agy_flags_model_and_resume():
    flags = build_agy_flags(
        permission_mode=None, model="gemini-2.5-flash", resuming=True,
    )
    assert flags[flags.index("--model") + 1] == "gemini-2.5-flash"
    assert "--continue" in flags


def test_build_host_local(tmp_path):
    from optio_host.host import LocalHost
    host = build_host(None, str(tmp_path / "task"))
    assert isinstance(host, LocalHost)


def test_auto_start_args_use_prompt_interactive_flag():
    # agy has no bare-positional prompt — the kickoff MUST be behind
    # --prompt-interactive or the TUI sits idle (the demo "does nothing" bug).
    from optio_antigravity.host_actions import (
        AUTO_START_PROMPT, build_auto_start_args, build_resume_notice_args,
    )
    assert build_auto_start_args(auto_start=True, resuming=False) == [
        "--prompt-interactive", AUTO_START_PROMPT,
    ]
    # No kickoff when not auto_start, or when resuming (that path uses --continue).
    assert build_auto_start_args(auto_start=False, resuming=False) == []
    assert build_auto_start_args(auto_start=True, resuming=True) == []
    # The resume notice is likewise delivered via --prompt-interactive, not bare.
    args = build_resume_notice_args(resuming=True)
    assert args[0] == "--prompt-interactive" and len(args) == 2
    assert build_resume_notice_args(resuming=False) == []
