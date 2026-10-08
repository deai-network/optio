from optio_claudecode import host_actions


def _shell(claustrum_wrap, local_mode, monkeypatch, netns=""):
    monkeypatch.setenv("OPTIO_CLAUDECODE_NETNS", netns)
    _, shell = host_actions._build_claude_shell_command(
        claude_path="/wd/home/.local/bin/claude",
        workdir="/wd",
        extra_env=None,
        claude_flags=["--print", "x"],
        local_mode=local_mode,
        claustrum_wrap=claustrum_wrap,
    )
    return shell


def test_no_wrap_unchanged(monkeypatch):
    shell = _shell(None, False, monkeypatch)
    assert "claustrum" not in shell
    assert "/wd/home/.local/bin/claude" in shell


def test_claustrum_wraps_claude(monkeypatch):
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]
    shell = _shell(wrap, False, monkeypatch)
    assert "/c/claustrum --best-effort --abi-min 1 --rwx /wd --" in shell
    # claude runs after the claustrum separator
    assert shell.index("claustrum") < shell.index("/wd/home/.local/bin/claude")


def test_pasta_execs_bash_not_claustrum(monkeypatch):
    # pasta's AppArmor profile (abstractions/pasta) only allows exec from
    # /{usr/,}bin/** (Ux). Exec'ing claustrum directly — it lives in the
    # version cache under $HOME — is denied ("Failed to start command or
    # shell: Permission denied"). pasta must exec bash (allowed, escapes
    # confinement via Ux), which then execs claustrum -> claude.
    wrap = ["/c/claustrum", "--", ]
    shell = _shell(wrap, True, monkeypatch, netns="pasta --config-net --")
    assert "pasta --config-net -- bash -c" in shell
    # pasta outermost, bash -c payload carries claustrum, then claude
    assert (shell.index("pasta") < shell.index("IS_SANDBOX=1")
            < shell.index("claustrum") < shell.index("/wd/home/.local/bin/claude"))


# Under claustrum Claude Code cannot reach /tmp: it refuses its default temp
# dir /tmp/claude-<uid> ("Temp directory ... is not readable") and exits 1,
# interactive and -p alike, every CLI version cached (2.1.288-2.1.294). The
# owner's "Setup Claude Code seed" died of it (2026-10-08). The wrap points
# CLAUDE_CODE_TMPDIR into the task's own (granted) workdir; claude creates it.

async def _wrap_for(fs_isolation, monkeypatch):
    from types import SimpleNamespace
    from optio_claudecode import session

    async def _cache_dir(host, install_dir):
        return "/cache"

    monkeypatch.setattr(host_actions, "_resolve_cache_dir", _cache_dir)
    host = SimpleNamespace(workdir="/wd")
    config = SimpleNamespace(fs_isolation=fs_isolation, install_dir=None, extra_allowed_dirs=[])
    return await session._build_claustrum_wrap(host, config, "/c/claustrum")


async def test_sandboxed_claude_gets_a_temp_dir_inside_its_workdir(monkeypatch):
    wrap = await _wrap_for(True, monkeypatch)
    sep = wrap.index("--")
    assert wrap[sep + 1:] == ["env", "CLAUDE_CODE_TMPDIR=/wd/.claude-tmp"]
    assert wrap[wrap.index("/wd") - 1] == "--rwx"  # inside a dir claude may write
    shell = _shell(wrap, False, monkeypatch)
    assert "-- env CLAUDE_CODE_TMPDIR=/wd/.claude-tmp /wd/home/.local/bin/claude" in shell


async def test_unsandboxed_claude_keeps_its_default_temp_dir(monkeypatch):
    assert await _wrap_for(False, monkeypatch) is None
