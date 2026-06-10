"""Config validation + prompt composition for conversation mode."""

import pytest

from optio_claudecode.prompt import (
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    compose_agents_md,
)
from optio_claudecode.types import ClaudeCodeTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_defaults_backcompat():
    cfg = _cfg()
    assert cfg.mode == "iframe"
    assert cfg.host_protocol is True
    assert cfg.permission_gate is False


def test_iframe_requires_host_protocol():
    with pytest.raises(ValueError):
        _cfg(host_protocol=False)


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError):
        _cfg(permission_gate=True)


def test_conversation_requires_noninteractive_permissions():
    with pytest.raises(ValueError):
        _cfg(mode="conversation")  # permission_mode=None, no gate
    _cfg(mode="conversation", permission_mode="bypassPermissions")
    _cfg(mode="conversation", permission_mode="acceptEdits")
    _cfg(mode="conversation", allowed_tools=["Read"])
    _cfg(mode="conversation", permission_gate=True)  # gate replaces the rule


def test_dontask_is_valid_permission_mode():
    _cfg(mode="conversation", permission_mode="dontAsk")


def test_prompt_with_host_protocol_off_omits_keyword_docs():
    text = compose_agents_md(
        "instructions", workdir_exclude=None, supports_resume=True,
        host_protocol=False,
    )
    assert "Log channel" not in text
    assert "optio.log" not in text
    assert "resume.log" in text                      # resume section stays
    # System: explainer added (normalize whitespace: the explainer wraps
    # across a line break in the source constant).
    assert "originate from the harness" in " ".join(text.split())


def test_prompt_default_instructions_and_framing_omission():
    text = compose_agents_md(
        DEFAULT_CONVERSATION_INSTRUCTIONS,
        workdir_exclude=None, supports_resume=True,
        host_protocol=False, omit_task_framing=True,
    )
    assert DEFAULT_CONVERSATION_INSTRUCTIONS in text
    assert "## Task" not in text


def test_prompt_unchanged_for_iframe_path():
    """Regression: default-args output identical to the pre-change renderer."""
    text = compose_agents_md("instructions", workdir_exclude=None)
    assert "Log channel" in text
    assert "## Task" in text


def test_tool_verbosity_default_is_description_only():
    assert _cfg().tool_verbosity == "description-only"


def test_tool_verbosity_accepts_levels():
    for v in ("silent", "description-only", "verbose"):
        assert _cfg(tool_verbosity=v).tool_verbosity == v


def test_tool_verbosity_rejects_bad_value():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(tool_verbosity="loud")
