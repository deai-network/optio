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
