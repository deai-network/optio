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


def test_pasta_outside_claustrum_inside(monkeypatch):
    wrap = ["/c/claustrum", "--", ]
    shell = _shell(wrap, True, monkeypatch, netns="pasta --config-net --")
    # pasta is outermost, then claustrum, then bash -c claude
    assert shell.index("pasta") < shell.index("claustrum") < shell.index("IS_SANDBOX=1")
