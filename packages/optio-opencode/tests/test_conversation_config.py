"""OpencodeTaskConfig validation for conversation mode (mirrors claudecode)."""

import pytest

from optio_opencode.types import OpencodeTaskConfig


def _cfg(**kw):
    # fs_isolation off by default here: these tests exercise conversation-mode
    # validation, not claustrum (which would otherwise demand delivery_type).
    kw.setdefault("fs_isolation", False)
    return OpencodeTaskConfig(consumer_instructions="do things", **kw)


def test_defaults_preserve_iframe_behavior():
    cfg = _cfg()
    assert cfg.mode == "iframe"
    assert cfg.host_protocol is True
    assert cfg.conversation_ui is False
    assert cfg.tool_verbosity == "description-only"


def test_mode_must_be_known():
    with pytest.raises(ValueError, match="mode="):
        _cfg(mode="tui")


def test_iframe_requires_host_protocol():
    with pytest.raises(ValueError, match="host_protocol=False requires"):
        _cfg(mode="iframe", host_protocol=False)


def test_conversation_allows_host_protocol_off():
    cfg = _cfg(mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui=True requires"):
        _cfg(conversation_ui=True)
    cfg = _cfg(mode="conversation", conversation_ui=True)
    assert cfg.conversation_ui is True


def test_tool_verbosity_validated():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(mode="conversation", tool_verbosity="chatty")


def test_empty_instructions_allowed_in_conversation_mode():
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", fs_isolation=False,
    )
    assert cfg.consumer_instructions == ""
