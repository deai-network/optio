from optio_grok.host_actions import _build_grok_shell_command


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
