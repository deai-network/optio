"""Conversation-mode model list + switching tests (Stage 7 Task 1).

Adapted from optio-grok's test_models.py. File-disjoint units that don't need
a live cursor:
  * models.py parse helpers (ACP session block + `cursor-agent models` CLI
    text — CLI format runtime-unverified, see models.py header);
  * fetch_available_models source precedence (ACP → CLI → fallback);
  * CursorTaskConfig.show_session_controls validation + the single `model`
    field (harmonization C3 dropped the separate default_model).

The inline model-switch mechanism itself (session/set_model over ACP,
[grok-pinned, cursor runtime-unverified]) is covered at the conversation level
in test_conversation.py.
"""

import pytest

from optio_cursor.models import (
    FALLBACK_MODELS,
    fetch_available_models,
    parse_acp_models,
    parse_cursor_models_text,
)
from optio_cursor.types import CursorTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return CursorTaskConfig(**base)


# --- ACP session/new models block ------------------------------------------

_ACP = {
    "currentModelId": "composer-1",
    "availableModels": [
        {"modelId": "composer-1", "name": "Composer 1"},
        {"modelId": "gpt-5", "name": "GPT-5"},
    ],
}


def test_parse_acp_models_maps_id_and_label():
    out = parse_acp_models(_ACP)
    assert out["default"] == "composer-1"
    assert out["models"] == [
        {"id": "composer-1", "label": "Composer 1", "disabled": False},
        {"id": "gpt-5", "label": "GPT-5", "disabled": False},
    ]


def test_parse_acp_models_missing_name_uses_id():
    out = parse_acp_models({"availableModels": [{"modelId": "gpt-5"}]})
    assert out["models"] == [{"id": "gpt-5", "label": "gpt-5", "disabled": False}]


def test_parse_acp_models_none_falls_back():
    assert parse_acp_models(None) == FALLBACK_MODELS
    assert parse_acp_models({}) == FALLBACK_MODELS


# --- `cursor-agent models` CLI text -----------------------------------------
# The real output shape is auth-gated and runtime-unverified (host not logged
# in); the parser targets the grok-style bulleted list AND bare-id lines.

_CURSOR_MODELS_TEXT = """Default model: composer-1

Available models:
  * composer-1 (default)
  - gpt-5
  sonnet-4.5-thinking
"""


def test_parse_cursor_models_text():
    out = parse_cursor_models_text(_CURSOR_MODELS_TEXT)
    assert out["default"] == "composer-1"
    assert [m["id"] for m in out["models"]] == [
        "composer-1", "gpt-5", "sonnet-4.5-thinking",
    ]
    assert all(m["disabled"] is False for m in out["models"])


# --- source precedence -----------------------------------------------------


class _FakeHost:
    def __init__(self, text, exit_code=0):
        self._text = text
        self._exit = exit_code
        self.calls = []

    async def run_command(self, cmd):
        self.calls.append(cmd)

        class R:
            pass

        r = R()
        r.exit_code = self._exit
        r.stdout = self._text
        return r


@pytest.mark.asyncio
async def test_fetch_prefers_acp_session_models():
    host = _FakeHost("SHOULD NOT BE READ")
    out = await fetch_available_models(_ACP, host=host, cursor_path="/bin/cursor-agent")
    assert out["default"] == "composer-1"
    assert [m["id"] for m in out["models"]] == ["composer-1", "gpt-5"]
    assert host.calls == []  # ACP present → no CLI call


@pytest.mark.asyncio
async def test_fetch_falls_back_to_cursor_models_cli():
    host = _FakeHost(_CURSOR_MODELS_TEXT)
    out = await fetch_available_models(None, host=host, cursor_path="/bin/cursor-agent")
    assert [m["id"] for m in out["models"]] == [
        "composer-1", "gpt-5", "sonnet-4.5-thinking",
    ]
    assert host.calls and "models" in host.calls[0]


@pytest.mark.asyncio
async def test_fetch_falls_back_to_static_list_without_source():
    out = await fetch_available_models(None)
    assert {m["id"] for m in out["models"]} == {
        m["id"] for m in FALLBACK_MODELS["models"]
    }
    assert out["default"] == FALLBACK_MODELS["default"]


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cli_fails():
    # `cursor-agent models` is auth-gated ("Error: Authentication required"
    # on a logged-out host) — a failing CLI must yield the static fallback.
    host = _FakeHost("Error: Authentication required.", exit_code=1)
    out = await fetch_available_models(None, host=host, cursor_path="/bin/cursor-agent")
    assert {m["id"] for m in out["models"]} == {
        m["id"] for m in FALLBACK_MODELS["models"]
    }
    assert out["default"] == FALLBACK_MODELS["default"]


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cli_output_unparseable():
    # cursor-agent has been observed exiting 0 while printing an error line —
    # unparseable output (no model list) must also yield the static fallback.
    host = _FakeHost("Error: Authentication required.", exit_code=0)
    out = await fetch_available_models(None, host=host, cursor_path="/bin/cursor-agent")
    assert {m["id"] for m in out["models"]} == {
        m["id"] for m in FALLBACK_MODELS["models"]
    }
    assert out["default"] == FALLBACK_MODELS["default"]


@pytest.mark.asyncio
async def test_fetch_sorts_models_alphabetically_by_label():
    # Every cursor source emits models in an arbitrary order; the picker list is
    # sorted alphabetically by label (case-insensitive).
    out = await fetch_available_models(None)  # static fallback
    labels = [m["label"] for m in out["models"]]
    assert labels == sorted(labels, key=str.lower)
    # concrete: "Opus 4.5" sorts before "Sonnet 4.5" though it is last in source.
    ids = [m["id"] for m in out["models"]]
    assert ids.index("opus-4.5") < ids.index("sonnet-4.5")


# --- config validation -----------------------------------------------------


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_session_controls"):
        _cfg(mode="conversation", conversation_ui=False, show_session_controls=True)


def test_show_session_controls_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, show_session_controls=True)
    assert cfg.show_session_controls is True


def test_native_spinner_requires_conversation_ui():
    with pytest.raises(ValueError, match="native_spinner"):
        _cfg(mode="conversation", conversation_ui=False, native_spinner=True)


def test_native_spinner_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, native_spinner=True)
    assert cfg.native_spinner is True


def test_model_is_ungated_single_field():
    # Harmonization C3: the separate default_model field is gone. `model` is
    # the single source both for the launch --model flag and the conversation
    # picker's initial value, and it is valid in ALL modes (no conversation_ui
    # gate — the old default_model gate was removed).
    assert not hasattr(CursorTaskConfig(consumer_instructions="x"), "default_model")
    cfg_iframe = _cfg(model="gpt-5")
    assert cfg_iframe.model == "gpt-5"
    cfg_conv = _cfg(mode="conversation", conversation_ui=True, model="gpt-5")
    assert cfg_conv.model == "gpt-5"
