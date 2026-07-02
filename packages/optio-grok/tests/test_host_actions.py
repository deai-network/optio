from optio_grok.host_actions import (
    _build_grok_shell_command,
    _isolation_env,
    build_conversation_argv,
    build_grok_flags,
)


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


def _flags(**over):
    base = dict(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
        model=None, effort=None, reasoning_effort=None, no_leader=False,
    )
    base.update(over)
    return build_grok_flags(**base)


def test_build_grok_flags_sandbox_on():
    """fs_isolation=True appends the fail-closed custom sandbox profile."""
    flags = _flags(fs_isolation=True)
    assert "--sandbox" in flags
    i = flags.index("--sandbox")
    assert flags[i + 1] == "optio"


def test_build_grok_flags_sandbox_off():
    assert "--sandbox" not in _flags(fs_isolation=False)


def test_build_conversation_argv_sandbox_on():
    """--sandbox is a top-level grok flag, so it must precede the `agent`
    subcommand in the conversation launch argv."""
    argv = build_conversation_argv("/x/grok", fs_isolation=True)
    assert "--sandbox" in argv and "optio" in argv
    i = argv.index("--sandbox")
    assert argv[i + 1] == "optio"
    assert argv.index("--sandbox") < argv.index("agent")
    assert argv.index("optio") < argv.index("agent")


def test_build_conversation_argv_sandbox_off():
    assert "--sandbox" not in build_conversation_argv("/x/grok", fs_isolation=False)
