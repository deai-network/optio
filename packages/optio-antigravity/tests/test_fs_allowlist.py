"""Stage 8 filesystem isolation: grant-flag builder + claustrum wrap.

Ported from optio-kimicode's ``test_fs_allowlist.py`` (kimi→agy; the only
existing claustrum lineage — claudecode → kimicode → antigravity). agy runs
under an ISOLATED ``$HOME`` (``<workdir>/home``, so its ``~/.gemini`` config
tree lives inside the workdir and is covered by the ``--rwx`` workdir grant);
claustrum is the outer kernel jail (design §8: native ``--sandbox`` is a future
opt-in, unverifiable without a real Google login — claustrum is the enforced,
fail-closed path here). Real Landlock enforcement is an opt-in,
skip-if-unsupported test (``test_sandbox_enforce.py`` /
``test_conversation_sandbox_enforce.py``), the row-30 real-binary follow-up.
"""

from __future__ import annotations

import pytest

from optio_antigravity import fs_allowlist, host_actions
from optio_antigravity.conversation import AntigravityConversation
from optio_antigravity.types import AllowedDir, AntigravityTaskConfig


# --- grant-flag composition (build_grant_flags) -----------------------------


def test_grant_flags_orders_modes_and_maps_caller():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        agy_cache_dir="/cache/bin",
        extra_allowed_dirs=[
            AllowedDir(path="/data", mode="ro"),
            AllowedDir(path="/scratch", mode="rw"),
        ],
    )
    # workdir is read-write-execute (agy writes + runs its tool scripts, and its
    # isolated ~/.gemini config tree lives under it).
    i = flags.index("/wd")
    assert flags[i - 1] == "--rwx"
    # the agy binary cache (outside the workdir) is read+exec so the launch
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
        workdir="/wd", agy_cache_dir="/cache", extra_allowed_dirs=None,
    )
    assert "/wd" in flags and "/cache" in flags


def test_trailing_slashes_stripped():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd/", agy_cache_dir="/cache/", extra_allowed_dirs=None,
    )
    assert "/wd" in flags and "/wd/" not in flags
    assert "/cache" in flags and "/cache/" not in flags


def test_tilde_extras_expand_against_host_home():
    # Grants reach claustrum verbatim (no shell between) and the agy process
    # runs under an ISOLATED $HOME, so a caller's ``~/`` extra must be expanded
    # against the REAL host home at flag-build time.
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        agy_cache_dir="/cache",
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
        # _resolve_antigravity_cache_dir resolves the cache via a printf echo.
        return RunResult(stdout=self._cache, stderr="", exit_code=0)


def _config(**kw) -> AntigravityTaskConfig:
    base = dict(
        consumer_instructions="do it", mode="conversation", host_protocol=True,
        delivery_type="audit",  # mandatory while fs_isolation is on (default)
    )
    base.update(kw)
    return AntigravityTaskConfig(**base)


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
    # the agy binary cache is granted read+exec.
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


# --- the wrap is applied to the iframe launch (tmux/agy shell command) -------


def test_agy_shell_command_prepends_claustrum_before_agy():
    # iframe launch: agy runs inside the detached tmux session's bash payload;
    # the claustrum wrap goes ahead of the agy invocation, so /bin/sh execs
    # claustrum, which applies Landlock then execve's agy.
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]
    _, cmd = host_actions._build_agy_shell_command(
        agy_path="/wd/home/.local/bin/agy",
        workdir="/wd",
        extra_env=None,
        agy_flags=["--model", "gemini"],
        claustrum_wrap=wrap,
    )
    assert "/c/claustrum --best-effort --abi-min 1 --rwx /wd --" in cmd
    assert cmd.index("claustrum") < cmd.index("/wd/home/.local/bin/agy")


def test_agy_shell_command_no_wrap_is_unconfined():
    # fs_isolation off → no claustrum, the agy launch is unchanged.
    _, cmd = host_actions._build_agy_shell_command(
        agy_path="/wd/home/.local/bin/agy",
        workdir="/wd",
        extra_env=None,
        agy_flags=[],
        claustrum_wrap=None,
    )
    assert "claustrum" not in cmd
    assert "/wd/home/.local/bin/agy" in cmd


# --- the wrap is applied to the conversation launch (agy -p under a PTY) -----


def test_conversation_prepends_claustrum_before_agy():
    # conversation launch: each turn is a fresh ``agy -p`` under a PTY; the
    # claustrum wrap is prepended to the turn argv so claustrum execve's agy
    # inside the PTY (baseline grants /dev/pts + /dev/ptmx for the pty).
    wrap = ["/c/claustrum", "--best-effort", "--abi-min", "1", "--rwx", "/wd", "--"]
    conv = AntigravityConversation(
        host=None,
        agy_path="/wd/home/.local/bin/agy",
        cwd="/wd",
        home="/wd/home",
        claustrum_wrap=wrap,
    )
    argv = conv._build_argv("hello")
    assert argv[0] == "/c/claustrum"
    assert argv[1:4] == ["--best-effort", "--abi-min", "1"]
    assert argv.index("/c/claustrum") < argv.index("/wd/home/.local/bin/agy")
    # the turn's real payload survives after the ``--`` fence.
    assert argv[-1] == "hello"


def test_conversation_no_wrap_is_unconfined():
    conv = AntigravityConversation(
        host=None,
        agy_path="/wd/home/.local/bin/agy",
        cwd="/wd",
        home="/wd/home",
        claustrum_wrap=None,
    )
    argv = conv._build_argv("hello")
    assert argv[0] == "/wd/home/.local/bin/agy"
    assert "claustrum" not in " ".join(argv)


# --- claustrum provisioning is delegated to the shared module ---------------
#
# The provisioning logic itself (detect arch, cross-compile on the engine, ELF
# guard, place + FUNCTIONAL validate, fail-closed) lives in
# ``optio_agents.claustrum`` and is covered by
# ``optio_agents/tests/test_claustrum.py``. Here we only assert the wrapper
# shim wires the two wrapper-specific paths (target cache dir + engine cache
# dir) and the progress callback into it.


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

    # cache_dir is resolved via the wrapper's own _resolve_antigravity_cache_dir
    # (the _FakeHost echoes it back from run_command).
    assert captured["host"] is host
    assert captured["cache_dir"] == "/worker/cache/bin"
    # engine_cache_dir is the wrapper-owned engine build root (expanded ~).
    import os
    assert captured["engine_cache_dir"] == os.path.expanduser("~/.cache/optio-antigravity")
    # the HookContext's progress callback is threaded through for the UI.
    assert captured["report_progress"] is _report
    assert result == "/worker/cache/bin/claustrum/vX/amd64/claustrum"
