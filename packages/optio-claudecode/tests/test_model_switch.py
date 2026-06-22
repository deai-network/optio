"""Unit tests for conversation-mode model switching (written against the
pinned interfaces in the Phase-2 plan).

These cover the file-disjoint units that don't need a live claude:
config validation, model-list parse, and the conversation model-change
signal. The full restart loop is exercised manually (see plan Task V3).
"""

import json

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_claudecode.models import (
    parse_models, declutter, fetch_available_models, FALLBACK_MODELS, _FALLBACK_LIST,
)


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
    assert parse_models({"data": []}) == {"models": list(_FALLBACK_LIST), "default": None}


def test_declutter_keeps_latest_per_family():
    out = declutter([
        {"id": "claude-opus-4-8", "label": "a"},
        {"id": "claude-opus-4-6", "label": "b"},
        {"id": "claude-opus-4-5-20251101", "label": "c"},  # dated, older
        {"id": "claude-sonnet-4-6", "label": "d"},
        {"id": "claude-haiku-4-5-20251001", "label": "e"},  # only haiku (dated) — kept
        {"id": "claude-fable-5", "label": "f"},
    ])
    assert [m["id"] for m in out] == [
        "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-fable-5",
    ]


class _FakeHost:
    """run_command serves GET /v1/models + per-model probe POSTs."""
    def __init__(self, models, unavailable):
        self._models = models
        self._unavailable = set(unavailable)

    async def fetch_bytes_from_host(self, path):
        return json.dumps({"claudeAiOauth": {"accessToken": "tok"}}).encode()

    async def run_command(self, cmd):
        class R:
            exit_code = 0
        r = R()
        if "/v1/models " in cmd:
            r.stdout = json.dumps({"data": [{"id": m} for m in self._models]})
            return r
        # probe POST /v1/messages — find which model id it carries
        hit = next((m for m in self._unavailable if m in cmd), None)
        r.stdout = (
            json.dumps({"type": "error", "error": {"type": "not_found_error"}})
            if hit else json.dumps({"id": "msg", "stop_reason": "max_tokens"})
        )
        return r


@pytest.mark.asyncio
async def test_fetch_marks_unavailable_models_disabled_and_skips_known_good():
    host = _FakeHost(
        models=["claude-opus-4-8", "claude-haiku-4-5-20251001", "claude-fable-5"],
        unavailable=["claude-fable-5"],
    )
    out = await fetch_available_models(host, home_dir="/w/home")
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["claude-opus-4-8"]["disabled"] is False        # known-good, not probed
    assert by_id["claude-haiku-4-5-20251001"]["disabled"] is False
    assert by_id["claude-fable-5"]["disabled"] is True          # probe -> not_found_error


@pytest.mark.asyncio
async def test_conversation_request_model_change_sets_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    conv.request_model_change("claude-opus-4-8")
    assert conv.requested_model == "claude-opus-4-8"
    assert conv.model_change_requested.is_set()


@pytest.mark.asyncio
async def test_restart_keeps_conversation_open_and_emits_no_close():
    """A model-swap process EOF must not close the conversation or emit
    x-optio-closed (which would gray the widget input)."""
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    events: list = []
    conv.on_event(lambda e: events.append(e))

    conv.begin_restart()
    await conv._finish("process ended")          # old process EOF during swap
    assert not conv._closed.is_set()
    assert not any(e.get("type") == "x-optio-closed" for e in events)

    class _FakeHandle:
        stdin = object()
    conv.attach(_FakeHandle())                    # relaunched process wired in
    await conv._finish("process ended")           # a real later EOF
    assert conv._closed.is_set()
    assert any(e.get("type") == "x-optio-closed" for e in events)
