"""Conversation-mode model list + switching tests (Stage 7 Task 1).

File-disjoint units that don't need a live grok:
  * models.py parse helpers (ACP session block + `grok models` CLI text);
  * fetch_available_models source precedence (ACP → CLI → fallback);
  * GrokTaskConfig.show_session_controls / native_spinner validation.

The inline model-switch mechanism itself (session/set_model over ACP) is
covered at the conversation level in test_conversation.py; the end-to-end swap
is verified manually.
"""

import pytest

from optio_grok.models import (
    FALLBACK_MODELS,
    fetch_available_models,
    parse_acp_models,
    parse_grok_models_text,
)
from optio_grok.types import GrokTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things", delivery_type="audit")
    base.update(kw)
    return GrokTaskConfig(**base)


# --- ACP session/new models block ------------------------------------------

_ACP = {
    "currentModelId": "grok-composer-2.5-fast",
    "availableModels": [
        {"modelId": "grok-composer-2.5-fast", "name": "Composer 2.5"},
        {"modelId": "grok-build", "name": "Grok Build"},
    ],
}


def test_parse_acp_models_maps_id_and_label():
    out = parse_acp_models(_ACP)
    assert out["default"] == "grok-composer-2.5-fast"
    assert out["models"] == [
        {"id": "grok-composer-2.5-fast", "label": "Composer 2.5", "disabled": False},
        {"id": "grok-build", "label": "Grok Build", "disabled": False},
    ]


def test_parse_acp_models_missing_name_uses_id():
    out = parse_acp_models({"availableModels": [{"modelId": "grok-build"}]})
    assert out["models"] == [{"id": "grok-build", "label": "grok-build", "disabled": False}]


def test_parse_acp_models_none_falls_back():
    assert parse_acp_models(None) == FALLBACK_MODELS
    assert parse_acp_models({}) == FALLBACK_MODELS


# --- `grok models` CLI text ------------------------------------------------

_GROK_MODELS_TEXT = """You are logged in with grok.com.

Default model: grok-composer-2.5-fast

Available models:
  * grok-composer-2.5-fast (default)
  - grok-build
"""


def test_parse_grok_models_text():
    out = parse_grok_models_text(_GROK_MODELS_TEXT)
    assert out["default"] == "grok-composer-2.5-fast"
    assert [m["id"] for m in out["models"]] == ["grok-composer-2.5-fast", "grok-build"]
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
    out = await fetch_available_models(_ACP, host=host, grok_path="/bin/grok")
    assert out["default"] == "grok-composer-2.5-fast"
    assert [m["id"] for m in out["models"]] == ["grok-composer-2.5-fast", "grok-build"]
    assert host.calls == []  # ACP present → no CLI call


@pytest.mark.asyncio
async def test_fetch_falls_back_to_grok_models_cli():
    host = _FakeHost(_GROK_MODELS_TEXT)
    out = await fetch_available_models(None, host=host, grok_path="/bin/grok")
    assert [m["id"] for m in out["models"]] == ["grok-composer-2.5-fast", "grok-build"]
    assert host.calls and "models" in host.calls[0]


@pytest.mark.asyncio
async def test_fetch_falls_back_to_static_list_without_source():
    out = await fetch_available_models(None)
    assert out == FALLBACK_MODELS


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cli_fails():
    host = _FakeHost("boom", exit_code=1)
    out = await fetch_available_models(None, host=host, grok_path="/bin/grok")
    assert out == FALLBACK_MODELS


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


def test_model_preselects_picker_with_no_conversation_ui_gate():
    """C3: the single ``model`` field both drives the launch --model flag and
    preselects the conversation picker — it is valid in every mode (the old
    ``default_model`` conversation_ui gate is gone)."""
    cfg = _cfg(model="grok-build")
    assert cfg.model == "grok-build"
    # No gate: a model is accepted in plain iframe mode too.
    cfg2 = _cfg(mode="iframe", model="grok-composer-2.5-fast")
    assert cfg2.model == "grok-composer-2.5-fast"


# --- per-model _meta carries NO reasoning-effort capability ----------------

# REAL captured shape of a session/new model block (authed `grok agent stdio`,
# grok 0.2.81). Each model's ``_meta`` carries ONLY {totalContextTokens,
# agentType} — there is no supportsReasoningEffort / reasoningEfforts field in
# ANY casing, so no live effort capability can be derived from ACP. (Real
# per-model capability lives only in ~/.grok/models_cache.json as snake_case
# ``supports_reasoning_effort``, currently false for every model.)
_ACP_REAL_META = {
    "currentModelId": "grok-composer-2.5-fast",
    "availableModels": [
        {
            "modelId": "grok-composer-2.5-fast",
            "name": "Composer 2.5",
            "_meta": {"totalContextTokens": 256000, "agentType": "grok-composer-2.5-fast"},
        },
        {
            "modelId": "grok-build",
            "name": "Grok Build",
            "_meta": {"totalContextTokens": 256000, "agentType": "grok-build"},
        },
    ],
}


def test_parse_acp_models_real_meta_surfaces_no_effort_capability():
    # grok's real _meta ({totalContextTokens, agentType}) advertises no reasoning
    # capability, so entries are plain {id,label,disabled} — no effort keys.
    out = parse_acp_models(_ACP_REAL_META)
    assert out["default"] == "grok-composer-2.5-fast"
    assert out["models"] == [
        {"id": "grok-composer-2.5-fast", "label": "Composer 2.5", "disabled": False},
        {"id": "grok-build", "label": "Grok Build", "disabled": False},
    ]
    for entry in out["models"]:
        assert "reasoningEfforts" not in entry
        assert "supportsReasoningEffort" not in entry
