"""Tests for ClaudeCodeTaskConfig defaults and validation."""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig


def test_minimal_config_uses_defaults():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.consumer_instructions == "hi"
    assert cfg.credentials_json is None
    assert cfg.claude_config is None
    assert cfg.env is None
    assert cfg.permission_mode is None
    assert cfg.allowed_tools is None
    assert cfg.disallowed_tools is None
    assert cfg.ssh is None
    assert cfg.install_if_missing is True
    assert cfg.install_ttyd_if_missing is True
    assert cfg.claude_install_dir is None
    assert cfg.ttyd_install_dir is None
    assert cfg.before_execute is None
    assert cfg.after_execute is None
    assert cfg.on_deliverable is None


def test_permission_mode_invalid_value_rejected():
    with pytest.raises(ValueError) as exc_info:
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            permission_mode="invalidMode",
        )
    assert "permission_mode" in str(exc_info.value)
    assert "invalidMode" in str(exc_info.value)


@pytest.mark.parametrize("mode", ["default", "plan", "acceptEdits", "bypassPermissions"])
def test_permission_mode_accepts_documented_values(mode: str):
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi", permission_mode=mode)
    assert cfg.permission_mode == mode


def test_install_dir_must_be_absolute_when_set():
    with pytest.raises(ValueError) as exc_info:
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            claude_install_dir="relative/path",
        )
    assert "absolute" in str(exc_info.value).lower()

    with pytest.raises(ValueError):
        ClaudeCodeTaskConfig(
            consumer_instructions="hi",
            ttyd_install_dir="also-relative",
        )


def test_install_dir_accepts_absolute():
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="hi",
        claude_install_dir="/opt/claude",
        ttyd_install_dir="/opt/ttyd",
    )
    assert cfg.claude_install_dir == "/opt/claude"
    assert cfg.ttyd_install_dir == "/opt/ttyd"


def test_credentials_json_accepts_dict_bytes_str():
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json={"a": 1})
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json=b"{}")
    ClaudeCodeTaskConfig(consumer_instructions="hi", credentials_json='{"a":1}')


def test_minimal_config_resume_defaults():
    cfg = ClaudeCodeTaskConfig(consumer_instructions="hi")
    assert cfg.supports_resume is True
    assert cfg.workdir_exclude is None
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None
    assert cfg.on_resume_refresh is None
