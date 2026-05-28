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
