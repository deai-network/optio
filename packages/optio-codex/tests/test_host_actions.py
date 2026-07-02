from optio_codex.host_actions import _build_codex_shell_command, _isolation_env


def test_isolation_env_all_keys():
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "CODEX_HOME": "/w/task/home/.codex",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
    }


def test_build_shell_command_uses_isolation_env():
    env, _cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task", extra_env=None,
        codex_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    assert any(a.startswith("PATH=") for a in env)


def test_env_isolation_and_done_error():
    from optio_codex.host_actions import build_codex_flags

    flags = build_codex_flags(model="gpt-test")
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task", extra_env=None,
        codex_flags=flags,
    )
    assert "HOME=/w/task/home" in env
    assert "CODEX_HOME=/w/task/home/.codex" in env
    assert "echo DONE" in cmd and "ERROR: codex exited" in cmd
    assert "--ask-for-approval" in cmd and "never" in cmd
    assert "--sandbox" in cmd and "workspace-write" in cmd
    assert "--model" in cmd and "gpt-test" in cmd