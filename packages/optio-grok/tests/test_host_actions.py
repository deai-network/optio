from optio_grok.host_actions import _build_grok_shell_command, _isolation_env


def test_isolation_env_all_keys():
    """_isolation_env is the single source of truth for per-task HOME/XDG/GROK
    identity — six explicit keys derived from the workdir."""
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "GROK_HOME": "/w/task/home/.grok",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
        "CLAUDE_CONFIG_DIR": "/w/task/home/.claude",
    }


def test_build_shell_command_uses_isolation_env():
    """_build_grok_shell_command emits every _isolation_env key (plus PATH)."""
    env, _cmd = _build_grok_shell_command(
        grok_path="/x/grok", workdir="/w/task", extra_env=None,
        grok_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    assert any(a.startswith("PATH=") for a in env)


def test_env_isolation_and_done_error():
    env, cmd = _build_grok_shell_command(
        grok_path="/x/grok", workdir="/w/task", extra_env=None,
        grok_flags=["--no-leader"],
    )
    assert "HOME=/w/task/home" in env
    assert "GROK_HOME=/w/task/home/.grok" in env
    assert "CLAUDE_CONFIG_DIR=/w/task/home/.claude" in env   # claude-compat neutralized
    assert "echo DONE" in cmd and "ERROR: grok exited" in cmd
    assert "--no-leader" in cmd
