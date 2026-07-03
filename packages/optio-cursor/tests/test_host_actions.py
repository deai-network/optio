import json

from optio_cursor.host_actions import (
    _build_cursor_shell_command,
    _cursor_data_dir,
    _isolation_env,
    build_cli_config,
    build_conversation_argv,
    build_cursor_flags,
    workspace_trust_marker,
)


def test_cursor_data_dir_is_short_and_deterministic():
    """CURSOR_DATA_DIR must be SHORT: cursor derives its socket/temp dir from it
    and falls back to an ungranted /tmp/.cursor when the base exceeds ~84 chars
    (the long taskdir would trigger that → EACCES). It is symlinked back into the
    workdir, so it must be stable in the workdir for a resume to re-link it."""
    long_wd = "/home/u/.local/share/optio-cursor/cursor-demo-seed-6a47c093324db0cfbec0637a/workdir"
    d = _cursor_data_dir(long_wd)
    assert d.startswith("/tmp/oc-")
    assert len(f"{d}/projects") <= 84
    assert _cursor_data_dir(long_wd) == d  # deterministic
    assert _cursor_data_dir("/w/other") != d  # per-workdir


def test_workspace_trust_marker_path_and_content():
    """Cursor gates a fresh workspace behind an interactive "Do you trust this
    directory?" prompt that blocks an unattended auto_start launch. It records
    trust at $HOME/.cursor/projects/<slug-of-abs-workspace>/.workspace-trusted
    (existence-only check). Pre-plant it so the launch is pre-authorized."""
    rel, content = workspace_trust_marker("/w/my_task/workdir/")
    assert rel == "home/.cursor/projects/w-my-task-workdir/.workspace-trusted"
    doc = json.loads(content)
    assert doc["workspacePath"] == "/w/my_task/workdir"
    assert "trustedAt" in doc and "trustMethod" in doc


def test_isolation_env_all_keys():
    """_isolation_env is the single source of truth for per-task HOME/XDG
    identity — four explicit keys derived from the workdir. NO_OPEN_BROWSER is
    intentionally NOT set: cursor is allowed to attempt xdg-open so the redirect
    browser-shim captures the auth URL and surfaces it via BROWSER:."""
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
        "CURSOR_DATA_DIR": _cursor_data_dir("/w/task"),
    }
    assert "NO_OPEN_BROWSER" not in env
    # HOME/XDG are rooted in the workdir. CURSOR_DATA_DIR is the deliberate
    # exception: a SHORT external path (symlinked back into <workdir>/home/.cursor
    # by link_cursor_data_dir) so cursor's socket paths stay under the length
    # limit — it does not leak operator state.
    for key, value in env.items():
        if key == "CURSOR_DATA_DIR":
            continue
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
    assert not any(a.startswith("NO_OPEN_BROWSER") for a in env)
    assert "echo DONE" in cmd and "ERROR: cursor-agent exited" in cmd
    assert "--force" in cmd


def test_iframe_scrubs_cursor_ssh_detection_vars():
    """Cursor's isSSH() gate refuses to spawn xdg-open (so the redirect shim can
    never capture the login URL) whenever an SSH_* var is present. The launch
    scrubs cursor's detection vars via ``env -u`` so cursor DOES open → the shim
    captures. SSH_AUTH_SOCK / SSH_AGENT_PID are deliberately KEPT (not in the
    detection set; needed for git-over-SSH / agent forwarding inside the task)."""
    _env, cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=[],
    )
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "SSH2_CLIENT", "SSH2_TTY"):
        assert f"-u {var}" in cmd, var
    assert "-u SSH_AUTH_SOCK" not in cmd
    assert "-u SSH_AGENT_PID" not in cmd


def test_teardown_aggressive_grace_for_seeded_sessions():
    """A seeded session must tear cursor down gracefully even on cancel so it
    can flush a rotated (single-use) auth.json before save-back — an aggressive
    kill would strand the rotation and kill the seed. Non-seeded keeps the fast
    kill. Mirrors optio-grok's _teardown_aggressive."""
    from optio_cursor.session import _teardown_aggressive
    assert _teardown_aggressive(cancelled=True, seeded=True) is False    # grace
    assert _teardown_aggressive(cancelled=True, seeded=False) is True    # fast kill
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=False) is False


def test_build_resume_notice_args():
    """PUSH half of resume awareness: on resume, cursor is continued with
    ``--continue`` so a trailing positional lands as a new turn — a
    ``System:``-prefixed "you have been resumed" notice. Empty on a fresh
    launch. Mirrors optio-grok's build_resume_notice_args."""
    from optio_cursor.host_actions import build_resume_notice_args

    # Fresh launch → no notice.
    assert build_resume_notice_args(resuming=False) == []
    # Resume → a single System:-prefixed "you have been resumed" positional.
    notice = build_resume_notice_args(resuming=True)
    assert len(notice) == 1
    assert notice[0].startswith("System: ")
    assert "you have been resumed" in notice[0]


def test_cli_config_rules():
    cfg = build_cli_config(allowed_tools=["Shell(ls)"], disallowed_tools=None)
    assert cfg["permissions"]["allow"] == ["Shell(ls)"]
    assert build_cli_config(allowed_tools=None, disallowed_tools=None) is None


# --- Stage 8: fail-closed fs isolation (claustrum) ---------------------------


def _flags(**kw):
    base = dict(force=False, auto_review=False, sandbox=None, model=None)
    base.update(kw)
    return build_cursor_flags(**base)


def test_fs_isolation_disables_native_cursor_sandbox():
    """Under claustrum fs-isolation the WHOLE process tree is Landlock-confined,
    so cursor's own per-shell-command native sandbox must be turned OFF
    (``--sandbox disabled``) to avoid nesting a helper inside the outer ruleset
    (see fs_allowlist.py). Mirrors grok's fs_isolation→--sandbox coupling
    (grok ADDS its native profile; cursor DISABLES the native one)."""
    flags = _flags(fs_isolation=True)
    assert "--sandbox" in flags
    i = flags.index("--sandbox")
    assert flags[i + 1] == "disabled"


def test_no_fs_isolation_omits_sandbox_flag():
    assert "--sandbox" not in _flags(fs_isolation=False)


def test_explicit_sandbox_overrides_fs_isolation_default():
    """A caller who explicitly sets ``sandbox`` keeps control even under
    fs_isolation (the disabled default only fills an UNSET sandbox)."""
    flags = _flags(sandbox="enabled", fs_isolation=True)
    i = flags.index("--sandbox")
    assert flags[i + 1] == "enabled"


def test_conversation_argv_disables_native_sandbox_under_fs_isolation():
    """--sandbox is a TOP-LEVEL cursor-agent flag, so it must precede the
    ``acp`` subcommand (mirrors grok's build_conversation_argv fs_isolation)."""
    argv = build_conversation_argv("/x/cursor-agent", fs_isolation=True)
    assert "--sandbox" in argv and "disabled" in argv
    assert argv.index("--sandbox") < argv.index("acp")
    assert "--sandbox" not in build_conversation_argv(
        "/x/cursor-agent", fs_isolation=False,
    )


def test_shell_command_claustrum_wraps_cursor():
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/w/task", "--"]
    _env, cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=["--sandbox", "disabled"], claustrum_wrap=wrap,
    )
    assert "/c/claustrum --best-effort --abi-min 1 --rwx /w/task --" in cmd
    # cursor runs after the claustrum separator.
    assert cmd.index("claustrum") < cmd.index("/x/cursor-agent")


def test_shell_command_no_wrap_unchanged():
    _env, cmd = _build_cursor_shell_command(
        cursor_path="/x/cursor-agent", workdir="/w/task", extra_env=None,
        cursor_flags=[], claustrum_wrap=None,
    )
    assert "claustrum" not in cmd
    assert "/x/cursor-agent" in cmd
