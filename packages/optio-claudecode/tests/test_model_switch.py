"""Unit tests for conversation-mode model switching (written against the
pinned interfaces in the Phase-2 plan).

These cover the file-disjoint units that don't need a live claude:
config validation, model-list parse, and the conversation model-change
signal. The full restart loop is exercised manually (see plan Task V3).
"""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_claudecode.models import parse_models, FALLBACK_MODELS


def _cfg(**kw):
    # Mirror the existing conversation-config tests' construction: the only
    # required field is consumer_instructions, and fs_isolation=False keeps
    # the config valid without a live host.
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_show_model_selector_requires_conversation_ui():
    # A valid conversation permission setup (permission_gate=True) gets us past
    # the unrelated conversation-mode validation so the show_model_selector
    # check is the one that fires.
    with pytest.raises(ValueError, match="show_model_selector"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            show_model_selector=True,
        )


def test_show_model_selector_ok_in_conversation_ui():
    cfg = _cfg(
        mode="conversation",
        permission_gate=True,
        conversation_ui=True,
        show_model_selector=True,
    )
    assert cfg.show_model_selector is True


def test_parse_models_maps_id_and_label():
    out = parse_models({"data": [
        {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5"},
    ]})
    assert out["models"] == [
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5", "label": "claude-haiku-4-5"},
    ]


def test_parse_models_empty_falls_back():
    assert parse_models({"data": []}) == FALLBACK_MODELS


@pytest.mark.asyncio
async def test_conversation_request_model_change_sets_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    conv.request_model_change("claude-opus-4-8")
    assert conv.requested_model == "claude-opus-4-8"
    assert conv.model_change_requested.is_set()
