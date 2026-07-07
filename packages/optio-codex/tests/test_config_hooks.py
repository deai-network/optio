"""Config-surface + unit tests for the ported generic features:

- P1 session-blob encryption (paired hooks + capture/restore round-trip),
- P2 on_resume_refresh (default identity + AGENTS.md rewrite-when-changed),
- P3 caller-message channel (fields + protocol/session wiring).

All xdist-safe: pure config construction, in-memory fakes, and source
inspection — nothing spawns codex.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from optio_agents import get_protocol
from optio_codex.prompt import compose_agents_md
from optio_codex.types import CodexTaskConfig


def _cfg(**kw) -> CodexTaskConfig:
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return CodexTaskConfig(**base)


# --- P1: session-blob encryption paired-hook validation --------------------


def test_session_blob_hooks_both_none_is_valid():
    cfg = _cfg()
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_session_blob_hooks_both_set_is_valid():
    cfg = _cfg(session_blob_encrypt=lambda b: b, session_blob_decrypt=lambda b: b)
    assert cfg.session_blob_encrypt is not None
    assert cfg.session_blob_decrypt is not None


def test_session_blob_only_encrypt_raises():
    with pytest.raises(ValueError) as exc:
        _cfg(session_blob_encrypt=lambda b: b)
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_session_blob_only_decrypt_raises():
    with pytest.raises(ValueError) as exc:
        _cfg(session_blob_decrypt=lambda b: b)
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


# --- P2: on_resume_refresh default + field ---------------------------------


def test_on_resume_refresh_defaults_identity():
    cfg = _cfg()
    # Default is identity-refresh (recompose AGENTS.md from the same config),
    # not None — a resumed session no longer freezes its instructions.
    assert cfg.on_resume_refresh is not None
    assert cfg.on_resume_refresh(cfg) is cfg


def test_on_resume_refresh_accepts_none():
    cfg = _cfg(on_resume_refresh=None)
    assert cfg.on_resume_refresh is None


# --- P3: caller-message channel fields -------------------------------------


def test_caller_message_fields_default_off():
    cfg = _cfg()
    assert cfg.use_client_messages is False
    assert cfg.on_caller_message is None


def test_caller_message_fields_settable():
    async def _on_caller(_hook_ctx, _keyword, _payload):
        return None

    cfg = _cfg(use_client_messages=True, on_caller_message=_on_caller)
    assert cfg.use_client_messages is True
    assert cfg.on_caller_message is _on_caller


# --- P1 round-trip: capture encrypts, restore decrypts ---------------------


def _reverse(b: bytes) -> bytes:
    return b[::-1]


class _FakeWriter:
    def __init__(self, slot: str):
        self.slot = slot
        self._buf = bytearray()
        self.file_id = "f-" + slot

    async def write(self, b: bytes):
        self._buf.extend(b)


class _FakeBlobCtx:
    def __init__(self, slot: str, captured: dict):
        self._slot = slot
        self._captured = captured

    async def __aenter__(self):
        self._w = _FakeWriter(self._slot)
        return self._w

    async def __aexit__(self, *exc):
        self._captured[self._slot] = bytes(self._w._buf)
        return False


async def test_capture_snapshot_writes_through_session_blob_encrypt(monkeypatch):
    from optio_codex import session as session_mod
    from optio_codex.session import _capture_snapshot

    monkeypatch.setattr(session_mod, "insert_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(session_mod, "prune_snapshots", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        session_mod, "effective_workdir_exclude", lambda x: [],
    )

    captured: dict[str, bytes] = {}
    fake_ctx = MagicMock()
    fake_ctx.store_blob = lambda slot: _FakeBlobCtx(slot, captured)
    fake_ctx._db = None
    fake_ctx._prefix = "test"
    fake_ctx.process_id = "pid-x"
    fake_ctx.delete_blob = AsyncMock()
    fake_ctx.mark_has_saved_state = AsyncMock()

    workdir_tar = b"codex-workdir-tar-with-home-dot-codex"

    async def _fake_archive(_excl):
        # Emit in two chunks to prove the encrypt path buffers whole.
        yield workdir_tar[:10]
        yield workdir_tar[10:]

    fake_host = MagicMock()
    fake_host.workdir = "/tmp/wd"
    fake_host.archive_workdir = _fake_archive

    await _capture_snapshot(
        fake_ctx, fake_host,
        end_state="done",
        workdir_exclude=None,
        session_id="sess-1",
        session_blob_encrypt=_reverse,
    )

    assert captured["workdir"] == _reverse(workdir_tar)


async def test_capture_snapshot_plaintext_streams_unwrapped(monkeypatch):
    from optio_codex import session as session_mod
    from optio_codex.session import _capture_snapshot

    monkeypatch.setattr(session_mod, "insert_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(session_mod, "prune_snapshots", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        session_mod, "effective_workdir_exclude", lambda x: [],
    )

    captured: dict[str, bytes] = {}
    fake_ctx = MagicMock()
    fake_ctx.store_blob = lambda slot: _FakeBlobCtx(slot, captured)
    fake_ctx.delete_blob = AsyncMock()
    fake_ctx.mark_has_saved_state = AsyncMock()
    fake_ctx.process_id = "pid-x"

    async def _fake_archive(_excl):
        yield b"plain-workdir"

    fake_host = MagicMock()
    fake_host.workdir = "/tmp/wd"
    fake_host.archive_workdir = _fake_archive

    await _capture_snapshot(
        fake_ctx, fake_host,
        end_state="done",
        workdir_exclude=None,
        session_id=None,
    )
    assert captured["workdir"] == b"plain-workdir"


async def test_restore_workdir_blob_decrypts(monkeypatch):
    from optio_codex.session import _restore_workdir_blob

    cipher = _reverse(b"restored-workdir-tar")

    class _FakeReader:
        def __init__(self):
            self._done = False

        async def read(self, _n):
            if self._done:
                return b""
            self._done = True
            return cipher

    class _FakeLoad:
        async def __aenter__(self):
            return _FakeReader()

        async def __aexit__(self, *exc):
            return False

    fake_ctx = MagicMock()
    fake_ctx.load_blob = lambda blob_id: _FakeLoad()

    restored: dict[str, bytes] = {}

    async def _restore_workdir(stream):
        buf = bytearray()
        async for chunk in stream:
            buf.extend(chunk)
        restored["plain"] = bytes(buf)

    fake_host = MagicMock()
    fake_host.restore_workdir = _restore_workdir

    await _restore_workdir_blob(
        fake_ctx, fake_host, "blob-1", session_blob_decrypt=_reverse,
    )
    assert restored["plain"] == b"restored-workdir-tar"


# --- P2 unit: _maybe_refresh_on_resume -------------------------------------


def _resume_fakes(existing_agents_md: str):
    """Build (host, hook_ctx) fakes whose read returns ``existing_agents_md``
    and whose write records the rewritten body."""
    written: dict[str, str] = {}

    async def _write_text(name, body):
        written[name] = body

    fake_host = MagicMock()
    fake_host.write_text = _write_text

    fake_hook = MagicMock()
    fake_hook.read_text_from_host = AsyncMock(return_value=existing_agents_md)
    return fake_host, fake_hook, written


async def test_maybe_refresh_identity_unchanged_is_noop():
    from optio_codex.session import _maybe_refresh_on_resume

    protocol = get_protocol(browser="redirect")
    cfg = _cfg(consumer_instructions="orig")
    existing = compose_agents_md(
        cfg.consumer_instructions,
        documentation=protocol.documentation if cfg.host_protocol else None,
        host_protocol=cfg.host_protocol,
        workdir_exclude=cfg.workdir_exclude,
        supports_resume=cfg.supports_resume,
        file_download=cfg.file_download,
    )
    host, hook, written = _resume_fakes(existing)

    refreshed = await _maybe_refresh_on_resume(host, hook, cfg, protocol)
    assert refreshed == []
    assert "AGENTS.md" not in written


async def test_maybe_refresh_mutating_hook_rewrites_agents_md():
    from optio_codex.session import _maybe_refresh_on_resume

    protocol = get_protocol(browser="redirect")

    def _bump(c: CodexTaskConfig) -> CodexTaskConfig:
        import dataclasses
        return dataclasses.replace(c, consumer_instructions="UPDATED INSTRUCTIONS")

    cfg = _cfg(consumer_instructions="orig", on_resume_refresh=_bump)
    stale = compose_agents_md(
        "orig",
        documentation=protocol.documentation,
        host_protocol=True,
    )
    host, hook, written = _resume_fakes(stale)

    refreshed = await _maybe_refresh_on_resume(host, hook, cfg, protocol)
    assert refreshed == ["AGENTS.md"]
    assert "UPDATED INSTRUCTIONS" in written["AGENTS.md"]


async def test_maybe_refresh_none_hook_is_noop():
    from optio_codex.session import _maybe_refresh_on_resume

    protocol = get_protocol(browser="redirect")
    cfg = _cfg(on_resume_refresh=None)
    host, hook, written = _resume_fakes("whatever")

    refreshed = await _maybe_refresh_on_resume(host, hook, cfg, protocol)
    assert refreshed == []
    assert "AGENTS.md" not in written


# --- P3 wiring: protocol built with the flags + sender threaded ------------


def test_session_builds_protocol_with_message_flags():
    from optio_codex import session as session_mod

    src = inspect.getsource(session_mod.run_codex_session)
    assert "client_messages=config.use_client_messages" in src
    assert "caller_messages=config.on_caller_message is not None" in src
    assert "on_caller_message=config.on_caller_message" in src
