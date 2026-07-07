"""Unit tests for ``_maybe_refresh_on_resume`` (P2 resume-refresh hook).

Pure and xdist-safe: no subprocess, no Mongo, no tmux. The helper is driven
directly with a fake host + hook_ctx, mirroring optio-claudecode's CLAUDE.md
refresh test. The instruction file here is AGENTS.md.
"""

from __future__ import annotations

import dataclasses

from optio_grok.prompt import compose_agents_md
from optio_grok.session import _maybe_refresh_on_resume
from optio_grok.types import GrokTaskConfig


class _FakeHost:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_text(self, path: str, text: str) -> None:
        self.writes.append((path, text))


class _FakeHookCtx:
    """Stands in for the HookContext; only read_text_from_host is exercised."""

    def __init__(self, existing: str | None) -> None:
        self._existing = existing

    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        if self._existing is None:
            raise FileNotFoundError(path)
        return self._existing


def _agents_md(cfg: GrokTaskConfig) -> str:
    return compose_agents_md(
        cfg.consumer_instructions,
        host_protocol=cfg.host_protocol,
        workdir_exclude=cfg.workdir_exclude,
        supports_resume=cfg.supports_resume,
        file_download=cfg.file_download,
    )


async def test_refresh_none_hook_is_noop():
    cfg = GrokTaskConfig(consumer_instructions="original", on_resume_refresh=None)
    host, hook = _FakeHost(), _FakeHookCtx(existing="whatever")
    assert await _maybe_refresh_on_resume(host, hook, cfg) == []
    assert host.writes == []


async def test_refresh_identity_unchanged_no_rewrite():
    # Identity default: recomputed AGENTS.md == the restored one → no rewrite.
    cfg = GrokTaskConfig(consumer_instructions="original")
    host = _FakeHost()
    hook = _FakeHookCtx(existing=_agents_md(cfg))
    assert await _maybe_refresh_on_resume(host, hook, cfg) == []
    assert host.writes == []


async def test_refresh_mutating_hook_rewrites_agents_md():
    def _mutate(c: GrokTaskConfig) -> GrokTaskConfig:
        return dataclasses.replace(c, consumer_instructions="UPDATED instructions")

    cfg = GrokTaskConfig(consumer_instructions="original", on_resume_refresh=_mutate)
    host = _FakeHost()
    hook = _FakeHookCtx(existing=_agents_md(cfg))  # old, unmutated content
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == ["AGENTS.md"]
    assert len(host.writes) == 1
    path, text = host.writes[0]
    assert path == "AGENTS.md"
    assert "UPDATED instructions" in text


async def test_refresh_hook_raises_is_ignored():
    def _boom(c: GrokTaskConfig) -> GrokTaskConfig:
        raise RuntimeError("nope")

    cfg = GrokTaskConfig(consumer_instructions="original", on_resume_refresh=_boom)
    host = _FakeHost()
    hook = _FakeHookCtx(existing="restored")
    assert await _maybe_refresh_on_resume(host, hook, cfg) == []
    assert host.writes == []


async def test_refresh_missing_file_rewrites():
    # No AGENTS.md on disk (FileNotFoundError) → treated as changed, rewritten.
    cfg = GrokTaskConfig(consumer_instructions="original")
    host = _FakeHost()
    hook = _FakeHookCtx(existing=None)
    out = await _maybe_refresh_on_resume(host, hook, cfg)
    assert out == ["AGENTS.md"]
    assert len(host.writes) == 1
