"""model/list → widget-shape mapping (Stage 7 model picker).

The conversation captures the raw ``model/list`` result at bootstrap
(``{data:[Model], nextCursor}`` — v2 ModelListResponse, codex-cli 0.142.5);
``parse_model_list`` maps it to the widget shape ``{models:[{id,label,
disabled}], default}``. Missing/malformed input falls back to the static
list — the picker is never falsely emptied and the parser never raises.
"""

from optio_codex.models import FALLBACK_MODELS, parse_model_list


def _entry(mid, name, *, default=False, hidden=False):
    # Only the fields the parser reads + the schema-required discriminators.
    return {
        "id": mid, "displayName": name, "description": "",
        "hidden": hidden, "isDefault": default, "model": mid,
        "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [],
    }


def test_parse_maps_data_entries_to_widget_shape():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True),
        _entry("gpt-5.4-mini", "GPT-5.4 Mini"),
    ], "nextCursor": None})
    assert out == {
        "models": [
            {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False},
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False},
        ],
        "default": "gpt-5.5",
    }


def test_parse_skips_hidden_models():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True),
        _entry("gpt-internal", "Internal", hidden=True),
    ]})
    assert [m["id"] for m in out["models"]] == ["gpt-5.5"]


def test_parse_default_none_when_no_isdefault_entry():
    out = parse_model_list({"data": [_entry("gpt-5.4-mini", "GPT-5.4 Mini")]})
    assert out["default"] is None
    assert out["models"][0]["id"] == "gpt-5.4-mini"


def test_parse_falls_back_on_none_and_malformed():
    assert parse_model_list(None) == FALLBACK_MODELS
    assert parse_model_list({}) == FALLBACK_MODELS
    assert parse_model_list({"data": "nope"}) == FALLBACK_MODELS
    assert parse_model_list({"data": []}) == FALLBACK_MODELS
    assert parse_model_list({"data": [{"displayName": "no id"}]}) == FALLBACK_MODELS


def test_fallback_is_copied_not_shared():
    out = parse_model_list(None)
    out["models"].append({"id": "x", "label": "x", "disabled": False})
    assert parse_model_list(None) == FALLBACK_MODELS  # untouched


def test_fallback_contents():
    ids = [m["id"] for m in FALLBACK_MODELS["models"]]
    assert ids == ["gpt-5.5", "gpt-5.4-mini"]
    assert FALLBACK_MODELS["default"] == "gpt-5.5"
