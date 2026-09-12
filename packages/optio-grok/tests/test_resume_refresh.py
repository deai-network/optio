"""Unit tests for ``_maybe_refresh_on_resume`` (P2 resume-refresh hook).

Pure and xdist-safe: no subprocess, no Mongo, no tmux. The helper is driven
directly with a fake host + hook_ctx, mirroring optio-claudecode's CLAUDE.md
refresh test. The instruction file here is AGENTS.md.
"""

from __future__ import annotations

import dataclasses

from optio_agents import get_protocol
from optio_agents.fs_grants import fs_isolation_dirs

from optio_grok.prompt import compose_agents_md
from optio_grok.session import _maybe_refresh_on_resume
from optio_grok.types import GrokTaskConfig

PROTOCOL = get_protocol(browser="redirect")


class _FakeHost:
    workdir = "/tmp/fake-wd"

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
        documentation=PROTOCOL.documentation if cfg.host_protocol else None,
        host_protocol=cfg.host_protocol,
        workdir_exclude=cfg.workdir_exclude,
        supports_resume=cfg.supports_resume,
        fs_isolation_dirs=fs_isolation_dirs(cfg, "/tmp/fake-wd"),
        file_download=cfg.file_download,
    )


async def test_refresh_none_hook_is_noop():
    cfg = GrokTaskConfig(consumer_instructions="original", delivery_type="audit", on_resume_refresh=None)
    host, hook = _FakeHost(), _FakeHookCtx(existing="whatever")
    assert await _maybe_refresh_on_resume(host, hook, cfg, PROTOCOL) == []
    assert host.writes == []


async def test_refresh_identity_unchanged_no_rewrite():
    # Identity default: recomputed AGENTS.md == the restored one → no rewrite.
    cfg = GrokTaskConfig(consumer_instructions="original", delivery_type="audit")
    host = _FakeHost()
    hook = _FakeHookCtx(existing=_agents_md(cfg))
    assert await _maybe_refresh_on_resume(host, hook, cfg, PROTOCOL) == []
    assert host.writes == []


async def test_refresh_mutating_hook_rewrites_agents_md():
    def _mutate(c: GrokTaskConfig) -> GrokTaskConfig:
        return dataclasses.replace(c, consumer_instructions="UPDATED instructions")

    cfg = GrokTaskConfig(consumer_instructions="original", delivery_type="audit", on_resume_refresh=_mutate)
    host = _FakeHost()
    hook = _FakeHookCtx(existing=_agents_md(cfg))  # old, unmutated content
    out = await _maybe_refresh_on_resume(host, hook, cfg, PROTOCOL)
    assert out == ["AGENTS.md"]
    assert len(host.writes) == 1
    path, text = host.writes[0]
    assert path == "AGENTS.md"
    assert "UPDATED instructions" in text


async def test_refresh_hook_raises_is_ignored():
    def _boom(c: GrokTaskConfig) -> GrokTaskConfig:
        raise RuntimeError("nope")

    cfg = GrokTaskConfig(consumer_instructions="original", delivery_type="audit", on_resume_refresh=_boom)
    host = _FakeHost()
    hook = _FakeHookCtx(existing="restored")
    assert await _maybe_refresh_on_resume(host, hook, cfg, PROTOCOL) == []
    assert host.writes == []


async def test_refresh_missing_file_rewrites():
    # No AGENTS.md on disk (FileNotFoundError) → treated as changed, rewritten.
    cfg = GrokTaskConfig(consumer_instructions="original", delivery_type="audit")
    host = _FakeHost()
    hook = _FakeHookCtx(existing=None)
    out = await _maybe_refresh_on_resume(host, hook, cfg, PROTOCOL)
    assert out == ["AGENTS.md"]
    assert len(host.writes) == 1


async def test_refresh_threads_session_docs():
    cfg = GrokTaskConfig(
        consumer_instructions="original", delivery_type="audit", use_client_messages=True,
        on_resume_refresh=lambda c: dataclasses.replace(c, consumer_instructions="UPDATED"),
    )
    host, hook = _FakeHost(), _FakeHookCtx(existing="stale")
    protocol = get_protocol(browser="redirect", client_messages=True)
    assert await _maybe_refresh_on_resume(host, hook, cfg, protocol) == ["AGENTS.md"]
    (_, text), = host.writes
    assert "CLIENT_MESSAGE:" in text and "`/tmp/fake-wd`" in text
