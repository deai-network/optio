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


# --- claustrum provisioning is delegated to the shared module ---------------
#
# The provisioning logic itself (detect arch, cross-compile on the engine, ELF
# guard, place + FUNCTIONAL validate, fail-closed) now lives in
# ``optio_agents.claustrum`` and is covered by ``optio_agents/tests/test_claustrum.py``.
# Here we only assert the wrapper shim wires the two wrapper-specific paths
# (target cache dir + engine cache dir) and the progress callback into it.


@pytest.mark.asyncio
async def test_ensure_claustrum_installed_delegates_to_shared_module(monkeypatch):
    from optio_agents import claustrum

    captured: dict = {}

    async def _fake_shared(host, *, cache_dir, engine_cache_dir, report_progress=None):
        captured["host"] = host
        captured["cache_dir"] = cache_dir
        captured["engine_cache_dir"] = engine_cache_dir
        captured["report_progress"] = report_progress
        return f"{cache_dir}/claustrum/vX/amd64/claustrum"

    # Patch the name the shim actually calls (bound in host_actions' namespace).
    monkeypatch.setattr(claustrum, "ensure_claustrum_installed", _fake_shared)

    host = _FakeHost(cache="/worker/cache/bin")

    def _report(percent, message=None):
        pass

    class _HookCtx:
        _host = host
        report_progress = staticmethod(_report)

    result = await host_actions.ensure_claustrum_installed(_HookCtx())

    # cache_dir is resolved via the wrapper's own _resolve_kimicode_cache_dir
    # (the _FakeHost echoes it back from run_command).
    assert captured["host"] is host
    assert captured["cache_dir"] == "/worker/cache/bin"
    # engine_cache_dir is the wrapper-owned engine build root (expanded ~).
    import os
    assert captured["engine_cache_dir"] == os.path.expanduser("~/.cache/optio-kimicode")
    # the HookContext's progress callback is threaded through for the UI.
    assert captured["report_progress"] is _report
    assert result == "/worker/cache/bin/claustrum/vX/amd64/claustrum"
