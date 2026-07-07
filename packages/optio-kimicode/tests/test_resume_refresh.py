"""Unit tests for the P2 resume-refresh port (`_maybe_refresh_on_resume`).

Pure/xdist-safe: exercises the helper directly with a fake host + hook_ctx (no
subprocess, no Mongo). Mirrors optio-claudecode's on_resume_refresh behavior,
adapted to kimi's AGENTS.md instruction file.
"""

from __future__ import annotations

import dataclasses

from optio_kimicode.prompt import compose_agents_md
from optio_kimicode.session import _maybe_refresh_on_resume
from optio_kimicode.types import KimiCodeTaskConfig


class _FakeHost:
    def __init__(self) -> None:
        self.writes: dict[str, str] = {}

    async def write_text(self, relpath: str, content: str) -> None:
        self.writes[relpath] = content


class _FakeHookCtx:
    """Provides read_text_from_host(silent=) with a configurable existing file."""

    def __init__(self, existing: str | None = None, raise_on_read: bool = False) -> None:
        self._existing = existing
        self._raise = raise_on_read

    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        if self._raise:
            raise RuntimeError("boom")
        if self._existing is None:
            raise FileNotFoundError(path)
        return self._existing


def _agents_md_for(cfg: KimiCodeTaskConfig) -> str:
    return compose_agents_md(
        cfg.consumer_instructions,
        host_protocol=cfg.host_protocol,
        workdir_exclude=cfg.workdir_exclude,
        supports_resume=cfg.supports_resume,
        file_download=cfg.file_download,
    )


async def test_refresh_disabled_returns_empty_no_write():
    cfg = KimiCodeTaskConfig(consumer_instructions="x", on_resume_refresh=None)
    host, hook = _FakeHost(), _FakeHookCtx(existing="whatever")
    assert await _maybe_refresh_on_resume(host, hook, cfg) == []
    assert host.writes == {}


async def test_identity_refresh_rewrites_when_file_absent():
    cfg = KimiCodeTaskConfig(consumer_instructions="do the task")
    host, hook = _FakeHost(), _FakeHookCtx(existing=None)  # FileNotFoundError
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == ["AGENTS.md"]
    assert host.writes["AGENTS.md"] == _agents_md_for(cfg)


async def test_identity_refresh_is_noop_when_unchanged():
    cfg = KimiCodeTaskConfig(consumer_instructions="do the task")
    host = _FakeHost()
    hook = _FakeHookCtx(existing=_agents_md_for(cfg))
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == []
    assert host.writes == {}


async def test_mutating_hook_rewrites_and_reports_filename():
    def _bump(c: KimiCodeTaskConfig) -> KimiCodeTaskConfig:
        return dataclasses.replace(
            c, consumer_instructions=c.consumer_instructions + " [REFRESHED]",
        )

    cfg = KimiCodeTaskConfig(consumer_instructions="orig", on_resume_refresh=_bump)
    host = _FakeHost()
    # existing == the ORIGINAL rendering; the bumped instructions differ.
    hook = _FakeHookCtx(existing=_agents_md_for(cfg))
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == ["AGENTS.md"]
    assert "[REFRESHED]" in host.writes["AGENTS.md"]


async def test_raising_hook_is_swallowed():
    def _boom(c: KimiCodeTaskConfig) -> KimiCodeTaskConfig:
        raise RuntimeError("nope")

    cfg = KimiCodeTaskConfig(consumer_instructions="x", on_resume_refresh=_boom)
    host, hook = _FakeHost(), _FakeHookCtx(existing="x")
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == []
    assert host.writes == {}
