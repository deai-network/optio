from optio_cursor.host_actions import _build_cursor_shell_command, build_cli_config


def test_env_isolation_and_done_error():
    env, cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=["--force"],
    )
    assert "HOME=/w/task/home" in env
    assert "NO_OPEN_BROWSER=1" in env
    assert "echo DONE" in cmd and "ERROR: cursor-agent exited" in cmd
    assert "--force" in cmd


def test_cli_config_rules():
    cfg = build_cli_config(allowed_tools=["Shell(ls)"], disallowed_tools=None)
    assert cfg["permissions"]["allow"] == ["Shell(ls)"]
    assert build_cli_config(allowed_tools=None, disallowed_tools=None) is None
