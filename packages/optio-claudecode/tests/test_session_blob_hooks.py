"""Tests for the optional session_blob_encrypt / session_blob_decrypt hooks."""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig


def test_both_hooks_none_is_valid():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_both_hooks_set_is_valid():
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    assert cfg.session_blob_encrypt is not None
    assert cfg.session_blob_decrypt is not None


def test_only_encrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            session_blob_encrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_only_decrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            session_blob_decrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_supports_resume_defaults_true():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.supports_resume is True


def test_workdir_exclude_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.workdir_exclude is None


def test_on_resume_refresh_defaults_none():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x")
    assert cfg.on_resume_refresh is None


"""Roundtrip a fake session tar through _capture_snapshot with a
non-identity hook to confirm the encrypt wiring, plus a source check that
the resume body invokes the decrypt hook."""

from unittest.mock import AsyncMock, MagicMock


def _reverse(b: bytes) -> bytes:
    return b[::-1]


@pytest.mark.asyncio
async def test_capture_writes_through_session_blob_encrypt(monkeypatch):
    from optio_claudecode.session import _capture_snapshot
    from optio_claudecode import session as session_mod

    fake_session_tar = b"hello-home-claude-tar"
    monkeypatch.setattr(
        session_mod, "_archive_home_claude",
        AsyncMock(return_value=fake_session_tar),
    )
    monkeypatch.setattr(session_mod, "insert_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(session_mod, "prune_snapshots", AsyncMock(return_value=[]))

    captured: dict[str, bytes] = {}

    class _FakeWriter:
        def __init__(self, slot: str):
            self.slot = slot
            self._buf = bytearray()
            self.file_id = "f-" + slot
            self._position = 0
        async def write(self, b: bytes):
            self._buf.extend(b)
            self._position = len(self._buf)
            captured[self.slot] = bytes(self._buf)

    class _FakeBlobCtx:
        def __init__(self, slot: str): self._slot = slot
        async def __aenter__(self):
            self._w = _FakeWriter(self._slot)
            return self._w
        async def __aexit__(self, *exc): return False

    fake_ctx = MagicMock()
    fake_ctx.store_blob = lambda slot: _FakeBlobCtx(slot)
    fake_ctx._db = None
    fake_ctx._prefix = "test"
    fake_ctx.process_id = "pid-x"
    fake_ctx.delete_blob = AsyncMock()
    fake_ctx.mark_has_saved_state = AsyncMock()

    async def _fake_archive(_excl):
        yield b"workdir-bytes"
    fake_host = MagicMock()
    fake_host.workdir = "/tmp/wd"
    fake_host.archive_workdir = _fake_archive
    # The credentials-present capture guard runs `test -s …/.credentials.json`
    # first; model a configured (logged-in) session so capture proceeds.
    fake_host.run_command = AsyncMock(return_value=MagicMock(stdout="OK\n"))

    await _capture_snapshot(
        fake_ctx, fake_host,
        end_state="done",
        workdir_exclude=None,
        session_blob_encrypt=_reverse,
    )

    assert captured["session"] == _reverse(fake_session_tar)


def test_resume_body_invokes_decrypt_hook_in_source():
    import inspect
    from optio_claudecode import session as session_mod
    src = inspect.getsource(session_mod.run_claudecode_session)
    assert "decrypt = config.session_blob_decrypt or (lambda b: b)" in src
    assert "decrypt(payload)" in src
