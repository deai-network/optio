"""Stage 8 filesystem isolation: grant-flag builder + claustrum wrap.

Ported from optio-claudecode's ``test_fs_allowlist.py`` / ``test_claustrum_wrap.py``
(claude→kimi, the pasta bash-hop dropped — kimicode has no network namespace).
Real Landlock enforcement is an opt-in, skip-if-unsupported test tracked as the
row-30 real-binary follow-up (mirrors optio-grok ``test_*_sandbox_enforce.py``).
"""

from __future__ import annotations

import pytest

from optio_kimicode import fs_allowlist, host_actions
from optio_kimicode.types import AllowedDir, KimiCodeTaskConfig


# --- grant-flag composition (build_grant_flags) -----------------------------


def test_grant_flags_orders_modes_and_maps_caller():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        kimi_cache_dir="/cache/bin",
        extra_allowed_dirs=[
            AllowedDir(path="/data", mode="ro"),
            AllowedDir(path="/scratch", mode="rw"),
        ],
    )
    # workdir is read-write-execute (agent writes + runs its tool scripts).
    i = flags.index("/wd")
    assert flags[i - 1] == "--rwx"
    # the kimi binary cache (outside the workdir) is read+exec so the launch
    # symlink's real target can be exec'd under the isolated $HOME.
    i = flags.index("/cache/bin")
    assert flags[i - 1] == "--rox"
    # caller extras mapped to their claustrum flag verbatim.
    i = flags.index("/data")
    assert flags[i - 1] == "--ro"
    i = flags.index("/scratch")
    assert flags[i - 1] == "--rw"
    # a baseline system dir is always granted.
    assert "/usr" in flags


def test_no_extra_dirs_ok():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd", kimi_cache_dir="/cache", extra_allowed_dirs=None,
    )
    assert "/wd" in flags and "/cache" in flags


def test_trailing_slashes_stripped():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd/", kimi_cache_dir="/cache/", extra_allowed_dirs=None,
    )
    assert "/wd" in flags and "/wd/" not in flags
    assert "/cache" in flags and "/cache/" not in flags


def test_tilde_extras_expand_against_host_home():
    # Grants reach claustrum verbatim (no shell between) and the kimi process
    # runs under an ISOLATED $HOME, so a caller's ``~/`` extra must be expanded
    # against the REAL host home at flag-build time.
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        kimi_cache_dir="/cache",
        extra_allowed_dirs=[
            AllowedDir(path="~/shared-data", mode="ro"),
            AllowedDir(path="/abs/stays", mode="rw"),
        ],
        host_home="/real/home",
    )
    i = flags.index("/real/home/shared-data")
    assert flags[i - 1] == "--ro"
    assert "~/shared-data" not in flags
    assert "/abs/stays" in flags


# --- the claustrum argv wrap (_build_claustrum_wrap) ------------------------


class _FakeHost:
    """Minimal Host stand-in: only the generic primitives the wrap touches
    (workdir, resolve_host_home, run_command). Host-type agnostic — the same
    object models a LocalHost or a RemoteHost, which is exactly why the wrap is
    identical local and remote (no isinstance in the code path)."""

    def __init__(self, *, workdir="/wd", host_home="/real/home", cache="/cache/bin"):
        self.workdir = workdir
        self._host_home = host_home
        self._cache = cache

    async def resolve_host_home(self):
        return self._host_home

    async def run_command(self, cmd, cwd=None):
        from optio_host.host import RunResult
        # _resolve_kimicode_cache_dir resolves the cache via a printf echo.
        return RunResult(stdout=self._cache, stderr="", exit_code=0)


def _config(**kw) -> KimiCodeTaskConfig:
    base = dict(consumer_instructions="do it", mode="conversation", host_protocol=True)
    base.update(kw)
    return KimiCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_wrap_none_when_isolation_off():
    cfg = _config(fs_isolation=False)
    wrap = await host_actions._build_claustrum_wrap(_FakeHost(), cfg, "/c/claustrum")
    assert wrap is None


@pytest.mark.asyncio
async def test_wrap_composed_when_isolation_on():
    cfg = _config(fs_isolation=True)
    wrap = await host_actions._build_claustrum_wrap(_FakeHost(), cfg, "/c/claustrum")
    assert wrap is not None
    # claustrum binary, best-effort + abi floor, grants, then the ``--`` fence.
    assert wrap[0] == "/c/claustrum"
    assert wrap[1:4] == ["--best-effort", "--abi-min", "1"]
    assert wrap[-1] == "--"
    # the task workdir is granted rwx inside the grant span.
    assert "--rwx" in wrap and "/wd" in wrap
    # the kimi binary cache is granted read+exec.
    assert "--rox" in wrap and "/cache/bin" in wrap


@pytest.mark.asyncio
async def test_wrap_maps_extras_and_expands_tilde():
    cfg = _config(
        fs_isolation=True,
        extra_allowed_dirs=[
            AllowedDir(path="~/notes", mode="ro"),
            AllowedDir(path="/data", mode="rw"),
        ],
    )
    wrap = await host_actions._build_claustrum_wrap(_FakeHost(), cfg, "/c/claustrum")
    i = wrap.index("/real/home/notes")
    assert wrap[i - 1] == "--ro"
    i = wrap.index("/data")
    assert wrap[i - 1] == "--rw"


# --- the wrap is applied to the launch command ------------------------------


def test_wrapped_exec_cmd_prepends_claustrum_before_kimi():
    # iframe launch argv (kimi web server) is confined by claustrum: the wrap
    # goes ahead of the kimi invocation, and exec replaces /bin/sh with
    # claustrum, which execve's kimi.
    argv = host_actions.build_kimi_server_argv(
        "/wd/home/.local/bin/kimi", bind_iface="127.0.0.1",
    )
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]
    cmd = host_actions.build_wrapped_exec_cmd(argv, claustrum_wrap=wrap)
    assert cmd.startswith("exec ")
    assert "/c/claustrum --best-effort --abi-min 1 --rwx /wd --" in cmd
    assert cmd.index("claustrum") < cmd.index("/wd/home/.local/bin/kimi")


def test_wrapped_exec_cmd_confines_acp_conversation():
    # conversation launch argv (kimi acp over stdio) is confined the same way.
    argv = ["/wd/home/.local/bin/kimi", "acp"]
    wrap = ["/c/claustrum", "--", ]
    cmd = host_actions.build_wrapped_exec_cmd(argv, claustrum_wrap=wrap)
    assert cmd.index("claustrum") < cmd.index("/wd/home/.local/bin/kimi acp")


def test_wrapped_exec_cmd_no_wrap_is_plain_exec():
    # fs_isolation off → no claustrum, the kimi launch is unchanged.
    argv = ["/wd/home/.local/bin/kimi", "acp"]
    cmd = host_actions.build_wrapped_exec_cmd(argv, claustrum_wrap=None)
    assert "claustrum" not in cmd
    assert cmd == "exec /wd/home/.local/bin/kimi acp"


# --- fail-closed: no silent unconfined launch -------------------------------


def test_detect_goarch_maps_known_arches():
    assert host_actions._GOARCH_BY_UNAME["x86_64"] == "amd64"
    assert host_actions._GOARCH_BY_UNAME["aarch64"] == "arm64"


@pytest.mark.asyncio
async def test_detect_goarch_rejects_non_linux():
    class _Host:
        async def run_command(self, cmd, cwd=None):
            from optio_host.host import RunResult
            return RunResult(stdout="Darwin", stderr="", exit_code=0)

    with pytest.raises(RuntimeError, match="Linux"):
        await host_actions._detect_goarch(_Host())


@pytest.mark.asyncio
async def test_detect_goarch_rejects_unknown_arch():
    class _Host:
        async def run_command(self, cmd, cwd=None):
            from optio_host.host import RunResult
            out = "Linux" if cmd == "uname -s" else "riscv64"
            return RunResult(stdout=out, stderr="", exit_code=0)

    with pytest.raises(RuntimeError, match="unsupported host arch"):
        await host_actions._detect_goarch(_Host())


@pytest.mark.asyncio
async def test_ensure_claustrum_fail_closed_when_verify_fails(monkeypatch, tmp_path):
    # If claustrum is placed but ``--version`` fails, provisioning RAISES rather
    # than returning a path — so an fs-isolated session can never launch
    # unconfined (fail-closed). Mirrors the claudecode contract.
    from optio_host.host import RunResult

    calls: list[str] = []

    class _Host:
        workdir = "/wd"

        async def run_command(self, cmd, cwd=None):
            calls.append(cmd)
            if cmd == "uname -s":
                return RunResult(stdout="Linux", stderr="", exit_code=0)
            if cmd == "uname -m":
                return RunResult(stdout="x86_64", stderr="", exit_code=0)
            if "printf" in cmd:
                return RunResult(stdout=str(tmp_path / "cache"), stderr="", exit_code=0)
            if "__optio_claustrum_ok__" in cmd:
                # The functional probe: a non-functioning claustrum (here, the
                # stub) never echoes the sentinel, so both the pre-probe and the
                # post-place verify treat it as invalid.
                return RunResult(stdout="", stderr="", exit_code=0)
            # chmod / test -x etc.
            return RunResult(stdout="", stderr="", exit_code=0)

        async def put_file_to_host(self, src, dst):
            return None

    class _HookCtx:
        _host = _Host()

        def report_progress(self, percent, message=None):
            pass

    async def _fake_build(goarch, tag, dest):
        import os
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("#!/bin/sh\n")

    monkeypatch.setattr(host_actions, "_build_claustrum_on_engine", _fake_build)
    # CRITICAL isolation: the engine-local build cache in ensure_claustrum_installed
    # is ``~/.cache/optio-kimicode/...`` (NOT the install_dir), so without redirecting
    # HOME the fake build would poison the operator's real cache — exactly the bug
    # that broke the live dashboard. Pin HOME into tmp so the stub stays in tmp.
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="functioning claustrum"):
        await host_actions.ensure_claustrum_installed(
            _HookCtx(), install_dir=str(tmp_path / "cache"),
        )


def test_is_elf_rejects_shell_stub(tmp_path):
    """_is_elf accepts a real ELF header and rejects a #!/bin/sh stub — the guard
    that stops a poisoned/placeholder engine-cache file from being shipped."""
    from optio_kimicode import host_actions
    stub = tmp_path / "stub"; stub.write_text("#!/bin/sh\n")
    assert host_actions._is_elf(str(stub)) is False
    elf = tmp_path / "elf"; elf.write_bytes(b"\x7fELF" + b"\x00" * 60)
    assert host_actions._is_elf(str(elf)) is True
    assert host_actions._is_elf(str(tmp_path / "missing")) is False
