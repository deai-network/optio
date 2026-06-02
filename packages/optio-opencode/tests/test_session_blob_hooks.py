"""Tests for the optional session_blob_encrypt / session_blob_decrypt hooks."""

import pytest

from optio_opencode.types import OpencodeTaskConfig


def test_both_hooks_none_is_valid():
    """Default: both hooks None means plaintext blob (current behavior)."""
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_both_hooks_set_is_valid():
    cfg = OpencodeTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    assert cfg.session_blob_encrypt is not None
    assert cfg.session_blob_decrypt is not None


def test_only_encrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        OpencodeTaskConfig(
            consumer_instructions="x",
            session_blob_encrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


def test_only_decrypt_set_raises():
    with pytest.raises(ValueError) as exc:
        OpencodeTaskConfig(
            consumer_instructions="x",
            session_blob_decrypt=lambda b: b,
        )
    assert "session_blob_encrypt" in str(exc.value)
    assert "session_blob_decrypt" in str(exc.value)


"""Roundtrip a fake session blob through capture + resume with non-identity hooks
to confirm the wiring inside _capture_snapshot and the resume path."""

from unittest.mock import AsyncMock, MagicMock


def _reverse(b: bytes) -> bytes:
    return b[::-1]


@pytest.mark.asyncio
async def test_capture_writes_through_session_blob_encrypt(monkeypatch):
    """In _capture_snapshot, the bytes that reach store_blob('session') must
    be config.session_blob_encrypt(session_json), not session_json itself."""
    from optio_opencode.session import _capture_snapshot
    from optio_opencode import session as session_mod

    fake_session_json = b"hello-session-bytes"
    monkeypatch.setattr(
        session_mod.host_actions, "opencode_export",
        AsyncMock(return_value=fake_session_json),
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
    fake_host.archive_workdir = _fake_archive
    # Satisfy the snapshot-capture defense-in-depth guard: it runs a
    # `test -s .../auth.json && echo OK` probe on the host and refuses to
    # capture unless the output contains "OK".
    fake_host.workdir = "/work"
    fake_host.run_command = AsyncMock(return_value=MagicMock(stdout="OK\n"))

    await _capture_snapshot(
        fake_ctx, fake_host,
        session_id="sid",
        opencode_db="/tmp/opencode.db",
        end_state="done",
        workdir_exclude=None,
        opencode_executable="opencode",
        session_blob_encrypt=_reverse,
    )

    assert captured["session"] == _reverse(fake_session_json), (
        f"session blob bytes were not piped through session_blob_encrypt; "
        f"got {captured['session']!r}, expected {_reverse(fake_session_json)!r}"
    )


def test_resume_body_invokes_decrypt_hook_in_source():
    """Belt-and-braces: confirm the resume body in session.py actually
    invokes the decrypt hook between reading the blob and importing the
    session. Catches regressions that drop the decrypt() call.

    This is a source-string check, not a behavioral one, because the
    integration-level fake-driven test would re-implement the resume
    body and miss the same regression. Future end-to-end coverage in
    excavator's T7 will exercise the full wire-up with real callables."""
    import inspect
    from optio_opencode import session as session_mod
    src = inspect.getsource(session_mod.run_opencode_session)
    assert "decrypt = config.session_blob_decrypt or (lambda b: b)" in src, (
        "resume body must invoke config.session_blob_decrypt; if this assertion "
        "fires, the wiring at session.py around the resume restore block was "
        "removed or refactored. See T4 of the credential-plaintext-containment plan."
    )
    assert "decrypt(session_bytes_raw)" in src, (
        "resume body must apply decrypt() to the raw blob bytes before "
        "passing them to opencode_import. See T4 of the plan."
    )
