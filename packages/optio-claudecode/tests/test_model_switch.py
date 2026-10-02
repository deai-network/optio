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
    model_from_transcript, resume_launch_model,
)


def _cfg(**kw):
    # Mirror the existing conversation-config tests' construction: the only
    # required field is consumer_instructions, and fs_isolation=False keeps
    # the config valid without a live host.
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_show_session_controls_requires_conversation_ui():
    # A valid conversation permission setup (permission_gate=True) gets us past
    # the unrelated conversation-mode validation so the show_session_controls
    # check is the one that fires.
    with pytest.raises(ValueError, match="show_session_controls"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            show_session_controls=True,
        )


def test_show_session_controls_ok_in_conversation_ui():
    cfg = _cfg(
        mode="conversation",
        permission_gate=True,
        conversation_ui=True,
        show_session_controls=True,
    )
    assert cfg.show_session_controls is True


def test_native_spinner_requires_conversation_ui():
    # A valid conversation permission setup (permission_gate=True) gets us past
    # the unrelated conversation-mode validation so the native_spinner check is
    # the one that fires.
    with pytest.raises(ValueError, match="native_spinner"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            native_spinner=True,
        )


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
        {"id": "claude-haiku-4-5-20251001", "label": "e"},  # dated snapshot
        {"id": "claude-haiku-4-5", "label": "e2"},  # same version; undated wins
        {"id": "claude-fable-5", "label": "f"},
    ])
    assert [m["id"] for m in out] == [
        "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5",
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


def test_model_from_transcript_keeps_the_last_model():
    text = "\n".join([
        "not-json",
        '{"type":"assistant","message":{"model":"claude-opus-4-8"}}',
        '{"type":"assistant","message":{"model":"claude-opus-5[1m]"}}',
        '{"type":"user","message":{"role":"user"}}',
    ])
    assert model_from_transcript(text) == "claude-opus-5[1m]"
    assert model_from_transcript("") is None
    assert model_from_transcript('{"type":"user"}') is None


def test_resume_launch_model_upgrades_to_the_newest_in_family():
    catalog = [
        {"id": "claude-opus-5", "label": "Opus 5"},
        {"id": "claude-opus-5-5", "label": "Opus 5.5"},
        {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5"},
        {"id": "claude-haiku-4-5", "label": "Haiku 4.5"},
        {"id": "claude-fable-5-1", "label": "Fable 5.1"},
    ]
    assert resume_launch_model(
        pinned=None, transcript_model="claude-opus-5", catalog=catalog,
    ) == "claude-opus-5-5"
    assert resume_launch_model(
        pinned=None, transcript_model="claude-opus-5[1m]", catalog=catalog,
    ) == "claude-opus-5-5"
    assert resume_launch_model(
        pinned=None, transcript_model="claude-sonnet-5", catalog=catalog,
    ) == "claude-sonnet-5-5"
    assert resume_launch_model(
        pinned=None, transcript_model="claude-fable-5", catalog=catalog,
    ) == "claude-fable-5-1"
    # A dated snapshot of the same version loses to the undated id.
    assert resume_launch_model(
        pinned=None, transcript_model="claude-haiku-4-5-20251001",
        catalog=[{"id": "claude-haiku-4-5"}],
    ) == "claude-haiku-4-5"


def test_resume_launch_model_does_not_downgrade_or_guess():
    older = [{"id": "claude-opus-4-8", "label": "Opus 4.8"}]
    assert resume_launch_model(
        pinned=None, transcript_model="claude-opus-5", catalog=older,
    ) is None
    assert resume_launch_model(
        pinned=None, transcript_model="opus", catalog=older,
    ) is None
    assert resume_launch_model(
        pinned=None, transcript_model=None, catalog=older,
    ) is None
    # An unavailable newer sibling is not a launch target.
    assert resume_launch_model(
        pinned=None, transcript_model="claude-fable-5",
        catalog=[{"id": "claude-fable-5-1", "disabled": True}],
    ) is None


def test_resume_launch_model_uses_the_pinned_family():
    catalog = [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}]
    assert resume_launch_model(
        pinned="claude-opus-5", transcript_model="claude-sonnet-5", catalog=catalog,
    ) == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_set_control_model_sets_restart_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("model", "claude-opus-4-8")
    assert conv.requested_model == "claude-opus-4-8"
    assert conv.model_change_requested.is_set()


@pytest.mark.asyncio
async def test_set_control_ignores_unknown_id():
    # claudecode exposes only the model control; any other id is a no-op and
    # never triggers a restart.
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("thinking", "high")
    assert conv.requested_model is None
    assert not conv.model_change_requested.is_set()


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
