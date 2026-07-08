"""Stage 9: claustrum is the fs-isolation guarantee for codex.

Claustrum (Landlock, fail-closed) now owns filesystem isolation; codex's
native sandbox is demoted to carrying the network knob only. These unit tests
cover the config-level contract (mandatory ``delivery_type``, native mode
decoupled from ``fs_isolation``) and the claustrum wrap builder shape.
"""

from __future__ import annotations

import pytest

from optio_codex import host_actions, session
from optio_codex.types import AllowedDir, CodexTaskConfig


def _cfg(**kw) -> CodexTaskConfig:
    base = dict(consumer_instructions="x", delivery_type="audit")
    base.update(kw)
    return CodexTaskConfig(**base)


# --- config contract --------------------------------------------------------


def test_fs_isolation_on_requires_delivery_type():
    # Default fs_isolation=True with no delivery_type is a hard error: the
    # operator must be reachable for the "newer claustrum available" notice.
    with pytest.raises(ValueError, match="delivery_type"):
        CodexTaskConfig(consumer_instructions="x")


def test_delivery_type_satisfies_the_rule():
    c = _cfg()
    assert c.fs_isolation is True
    assert c.delivery_type == "audit"


def test_fs_isolation_off_needs_no_delivery_type():
    c = CodexTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert c.fs_isolation is False
    assert c.delivery_type is None


def test_effective_sandbox_mode_is_workspace_write_regardless_of_fs_isolation():
    # Native mode is decoupled from fs_isolation now (claustrum owns fs); the
    # native default carries only the network posture.
    assert _cfg(fs_isolation=True).effective_sandbox_mode == "workspace-write"
    assert (
        CodexTaskConfig(consumer_instructions="x", fs_isolation=False)
        .effective_sandbox_mode
        == "workspace-write"
    )


def test_fs_isolation_off_with_danger_full_access_no_longer_raises():
    # Was a config error (fs_isolation⇄danger-full-access); now fs is claustrum's
    # job, so the native mode is free to be danger-full-access.
    c = CodexTaskConfig(
        consumer_instructions="x", fs_isolation=False, sandbox="danger-full-access",
    )
    assert c.effective_sandbox_mode == "danger-full-access"


def test_fs_isolation_on_with_danger_full_access_no_longer_raises():
    c = _cfg(sandbox="danger-full-access")
    assert c.effective_sandbox_mode == "danger-full-access"


# --- claustrum wrap builder -------------------------------------------------


class _FakeHost:
    def __init__(self):
        self.workdir = "/task/workdir"

    async def resolve_host_home(self):
        return "/home/op"


@pytest.mark.asyncio
async def test_build_claustrum_wrap_shape(monkeypatch):
    async def _fake_cache(host, override):
        return "/opt/codex-cache"

    monkeypatch.setattr(host_actions, "_resolve_codex_cache_dir", _fake_cache)

    wrap = await session._build_claustrum_wrap(
        _FakeHost(), _cfg(extra_allowed_dirs=[AllowedDir("~/data", "rw")]),
        "/bin/claustrum",
    )
    assert wrap is not None
    # shared shape: [claustrum, --best-effort, --abi-min, 1, *grants, --]
    assert wrap[0] == "/bin/claustrum"
    assert wrap[1:4] == ["--best-effort", "--abi-min", "1"]
    assert wrap[-1] == "--"
    # workdir rwx + codex cache rox + the ~/ extra expanded against host home
    assert "--rwx" in wrap and "/task/workdir" in wrap
    assert "--rox" in wrap and "/opt/codex-cache" in wrap
    assert "--rw" in wrap and "/home/op/data" in wrap
    # system baseline present
    assert "/usr" in wrap


@pytest.mark.asyncio
async def test_build_claustrum_wrap_none_when_fs_isolation_off():
    wrap = await session._build_claustrum_wrap(
        _FakeHost(),
        CodexTaskConfig(consumer_instructions="x", fs_isolation=False),
        "/bin/claustrum",
    )
    assert wrap is None
