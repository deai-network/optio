"""Tests for KimiCodeTaskConfig validation (Appendix D surface).

Ported from optio-grok's type tests; kimi deltas: effort is a validated
enum (low|medium|high|xhigh|max), model stays an unvalidated alias string,
and there are no ttyd / no_leader / reasoning_effort fields.
"""

import pytest

from optio_kimicode.types import AllowedDir, KimiCodeTaskConfig


# --- happy path / defaults -------------------------------------------------


def test_minimal_construction_and_defaults():
    cfg = KimiCodeTaskConfig(consumer_instructions="do the thing")
    assert cfg.consumer_instructions == "do the thing"
    # kimi-specific / parity defaults
    assert cfg.mode == "iframe"
    assert cfg.auto_start is False
    assert cfg.thinking_verbosity == "hidden"
    assert cfg.tool_verbosity == "description-only"
    assert cfg.fs_isolation is True
    assert cfg.host_protocol is True
    assert cfg.supports_resume is True
    assert cfg.conversation_ui is False


def test_model_is_an_unvalidated_alias():
    # kimi models are aliases, not raw ids, and are NOT enum-validated.
    cfg = KimiCodeTaskConfig(consumer_instructions="x", model="kimi-k2-anything")
    assert cfg.model == "kimi-k2-anything"


# --- mode ------------------------------------------------------------------


def test_bad_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        KimiCodeTaskConfig(consumer_instructions="x", mode="tui")  # type: ignore[arg-type]


def test_valid_modes_accepted():
    for m in ("iframe", "conversation"):
        assert KimiCodeTaskConfig(consumer_instructions="x", mode=m).mode == m


# --- effort enum (kimi delta) ---------------------------------------------


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_valid_effort_accepted(effort):
    cfg = KimiCodeTaskConfig(consumer_instructions="x", effort=effort)
    assert cfg.effort == effort


def test_effort_none_ok():
    assert KimiCodeTaskConfig(consumer_instructions="x").effort is None


def test_bad_effort_rejected():
    with pytest.raises(ValueError, match="effort"):
        KimiCodeTaskConfig(consumer_instructions="x", effort="ultra")


# --- permission_mode -------------------------------------------------------


def test_bad_permission_mode_rejected():
    # kimi's modes are yolo/manual/auto — claudecode values are NOT valid here.
    with pytest.raises(ValueError, match="permission_mode"):
        KimiCodeTaskConfig(consumer_instructions="x", permission_mode="bypassPermissions")  # type: ignore[arg-type]


def test_valid_permission_mode_accepted():
    cfg = KimiCodeTaskConfig(consumer_instructions="x", permission_mode="yolo")
    assert cfg.permission_mode == "yolo"


# --- conversation-only flags require mode=conversation ---------------------


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        KimiCodeTaskConfig(consumer_instructions="x", mode="iframe", conversation_ui=True)


def test_conversation_ui_ok_in_conversation_mode():
    cfg = KimiCodeTaskConfig(
        consumer_instructions="x", mode="conversation", conversation_ui=True
    )
    assert cfg.conversation_ui is True


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError, match="permission_gate"):
        KimiCodeTaskConfig(consumer_instructions="x", mode="iframe", permission_gate=True)


def test_host_protocol_false_requires_conversation_mode():
    with pytest.raises(ValueError, match="host_protocol"):
        KimiCodeTaskConfig(consumer_instructions="x", mode="iframe", host_protocol=False)


def test_host_protocol_false_ok_in_conversation_mode():
    cfg = KimiCodeTaskConfig(
        consumer_instructions="x", mode="conversation", host_protocol=False
    )
    assert cfg.host_protocol is False


# --- frontend-parity flags require conversation UI -------------------------


def test_default_model_requires_conversation_ui():
    with pytest.raises(ValueError, match="default_model"):
        KimiCodeTaskConfig(
            consumer_instructions="x", mode="conversation", default_model="kimi-k2"
        )


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_session_controls"):
        KimiCodeTaskConfig(
            consumer_instructions="x", mode="conversation", show_session_controls=True
        )


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        KimiCodeTaskConfig(
            consumer_instructions="x", mode="conversation", show_file_upload=True
        )


def test_file_download_requires_conversation_ui():
    with pytest.raises(ValueError, match="file_download"):
        KimiCodeTaskConfig(
            consumer_instructions="x", mode="conversation", file_download=True
        )


def test_frontend_parity_flags_ok_with_conversation_ui():
    cfg = KimiCodeTaskConfig(
        consumer_instructions="x",
        mode="conversation",
        conversation_ui=True,
        default_model="kimi-k2",
        show_session_controls=True,
        show_file_upload=True,
        file_download=True,
    )
    assert cfg.show_session_controls is True


# --- verbosity enums -------------------------------------------------------


def test_bad_tool_verbosity_rejected():
    with pytest.raises(ValueError, match="tool_verbosity"):
        KimiCodeTaskConfig(consumer_instructions="x", tool_verbosity="loud")  # type: ignore[arg-type]


def test_bad_thinking_verbosity_rejected():
    with pytest.raises(ValueError, match="thinking_verbosity"):
        KimiCodeTaskConfig(consumer_instructions="x", thinking_verbosity="loud")  # type: ignore[arg-type]


# --- AllowedDir / extra_allowed_dirs --------------------------------------


def test_allowed_dir_bad_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        AllowedDir(path="/data", mode="wx")  # type: ignore[arg-type]


def test_allowed_dir_valid_modes():
    for m in ("ro", "rw"):
        assert AllowedDir(path="/data", mode=m).mode == m


def test_extra_allowed_dirs_accepted():
    cfg = KimiCodeTaskConfig(
        consumer_instructions="x",
        extra_allowed_dirs=[AllowedDir(path="/opt/tools", mode="ro")],
    )
    assert cfg.extra_allowed_dirs[0].path == "/opt/tools"


# --- at-rest session-blob cipher (both-or-none) ----------------------------


def _cipher(b: bytes) -> bytes:
    return b[::-1]


def test_session_blob_cipher_defaults_none():
    cfg = KimiCodeTaskConfig(consumer_instructions="x")
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_session_blob_cipher_pair_accepted():
    cfg = KimiCodeTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=_cipher,
        session_blob_decrypt=_cipher,
    )
    assert cfg.session_blob_encrypt is _cipher
    assert cfg.session_blob_decrypt is _cipher


def test_session_blob_encrypt_without_decrypt_rejected():
    with pytest.raises(ValueError, match="session_blob"):
        KimiCodeTaskConfig(consumer_instructions="x", session_blob_encrypt=_cipher)


def test_session_blob_decrypt_without_encrypt_rejected():
    with pytest.raises(ValueError, match="session_blob"):
        KimiCodeTaskConfig(consumer_instructions="x", session_blob_decrypt=_cipher)


# --- install-dir override must be absolute ---------------------------------


def test_kimi_install_dir_must_be_absolute():
    with pytest.raises(ValueError, match="kimi_install_dir"):
        KimiCodeTaskConfig(consumer_instructions="x", kimi_install_dir="relative/path")


def test_kimi_install_dir_absolute_ok():
    cfg = KimiCodeTaskConfig(consumer_instructions="x", kimi_install_dir="/opt/kimi")
    assert cfg.kimi_install_dir == "/opt/kimi"
