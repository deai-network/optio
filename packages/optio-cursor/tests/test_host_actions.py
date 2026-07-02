from optio_cursor.host_actions import (
    _build_cursor_shell_command,
    _isolation_env,
    build_cli_config,
)


def test_isolation_env_all_keys():
    """_isolation_env is the single source of truth for per-task HOME/XDG
    identity — five explicit keys derived from the workdir (NO_OPEN_BROWSER
    is part of the identity: no launch path may spawn a host browser)."""
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
        "NO_OPEN_BROWSER": "1",
    }
    # No path-valued entry may point outside the workdir (PATH is layered by
    # the caller and intentionally absent here).
    for key, value in env.items():
        if value.startswith("/"):
            assert value.startswith("/w/task/"), f"{key} leaks outside workdir"


def test_build_shell_command_uses_isolation_env():
    """_build_cursor_shell_command emits every _isolation_env key (plus PATH)."""
    env, _cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    assert any(a.startswith("PATH=") for a in env)


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
