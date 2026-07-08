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


def test_build_grok_flags_no_native_sandbox():
    """Filesystem isolation is NOT a grok flag anymore: claustrum wraps the
    whole launch from the outside, so ``--sandbox`` never appears (and the
    builder no longer takes an fs_isolation param)."""
    import inspect
    assert "fs_isolation" not in inspect.signature(build_grok_flags).parameters
    assert "--sandbox" not in _flags()


def test_teardown_aggressive_grace_for_seeded_sessions():
    """A seeded session must tear grok down gracefully even on cancel so it can
    flush a rotated (single-use) auth.json before save-back — an aggressive kill
    would strand the rotation and kill the seed. Non-seeded keeps the fast kill."""
    from optio_grok.session import _teardown_aggressive
    assert _teardown_aggressive(cancelled=True, seeded=True) is False    # grace
    assert _teardown_aggressive(cancelled=True, seeded=False) is True    # fast kill
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=False) is False


def test_build_resume_notice_args():
    from optio_grok.host_actions import build_resume_notice_args
    # Fresh launch → no notice.
    assert build_resume_notice_args(resuming=False) == []
    # Resume → a single System:-prefixed "you have been resumed" positional.
    notice = build_resume_notice_args(resuming=True)
    assert len(notice) == 1
    assert "you have been resumed" in notice[0]


def test_build_conversation_argv_no_native_sandbox_or_ctty():
    """Claustrum confines the whole process tree from the outside, so the
    conversation argv is grok directly — no ``--sandbox``, no controlling-tty
    wrapper (claustrum does not open /dev/tty)."""
    argv = build_conversation_argv("/x/grok")
    assert "--sandbox" not in argv
    assert argv[0] == "/x/grok"
    assert "TIOCSCTTY" not in " ".join(argv)


def test_build_conversation_argv_reasoning_effort():
    # The initial graded effort rides --reasoning-effort at launch (mirrors
    # --model), between `agent` and `stdio`.
    argv = build_conversation_argv(
        "/x/grok", model="grok-build", reasoning_effort="high",
    )
    assert argv[argv.index("--reasoning-effort") + 1] == "high"
    assert argv.index("--reasoning-effort") < argv.index("stdio")
    # Omitted when unset (no probe-mismatch risk on the common path).
    assert "--reasoning-effort" not in build_conversation_argv("/x/grok")
