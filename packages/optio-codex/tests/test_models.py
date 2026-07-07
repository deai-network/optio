"""model/list → widget-shape mapping (Stage 7 model picker).

The conversation captures the raw ``model/list`` result at bootstrap
(``{data:[Model], nextCursor}`` — v2 ModelListResponse, codex-cli 0.142.5);
``parse_model_list`` maps it to the widget shape ``{models:[{id,label,
disabled}], default}``. Missing/malformed input falls back to the static
list — the picker is never falsely emptied and the parser never raises.
"""

from optio_codex.models import (
    FALLBACK_MODELS,
    build_controls,
    effort_for_model,
    parse_model_list,
)


def _entry(mid, name, *, default=False, hidden=False,
           efforts=None, default_effort="medium"):
    # Only the fields the parser reads + the schema-required discriminators.
    # ``supportedReasoningEfforts`` is the REAL app-server wire shape (codex
    # 0.142.5 model/list): a LIST OF OBJECTS ``[{"reasoningEffort": "low",
    # "description": ...}, ...]`` — NOT a list of bare strings. ``efforts`` is
    # given here as convenient level names and expanded to that object shape.
    return {
        "id": mid, "displayName": name, "description": "",
        "hidden": hidden, "isDefault": default, "model": mid,
        "defaultReasoningEffort": default_effort,
        "supportedReasoningEfforts": [
            {"reasoningEffort": lvl, "description": f"{lvl} reasoning"}
            for lvl in (efforts if efforts is not None else [])
        ],
    }


def test_parse_maps_data_entries_to_widget_shape():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True),
        _entry("gpt-5.4-mini", "GPT-5.4 Mini"),
    ], "nextCursor": None})
    assert out == {
        "models": [
            {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False,
             "efforts": [], "defaultEffort": "medium"},
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False,
             "efforts": [], "defaultEffort": "medium"},
        ],
        "default": "gpt-5.5",
    }


def test_parse_captures_supported_reasoning_efforts():
    out = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True,
               efforts=["low", "medium", "high", "xhigh"], default_effort="high"),
        _entry("gpt-5.4-mini", "GPT-5.4 Mini"),  # supportedReasoningEfforts=[]
    ]})
    a, b = out["models"]
    assert a["efforts"] == ["low", "medium", "high", "xhigh"]
    assert a["defaultEffort"] == "high"
    assert b["efforts"] == []  # non-graded model


def test_effort_for_model_reads_capability():
    ml = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True,
               efforts=["low", "high"], default_effort="high"),
        _entry("gpt-5.4-mini", "GPT-5.4 Mini"),
    ]})
    assert effort_for_model(ml, "gpt-5.5") == (["low", "high"], "high")
    assert effort_for_model(ml, "gpt-5.4-mini") == ([], "medium")
    assert effort_for_model(ml, "unknown") == ([], None)


def test_build_controls_omits_effort_for_non_graded_model():
    ml = parse_model_list({"data": [_entry("gpt-5.4-mini", "GPT-5.4 Mini", default=True)]})
    controls = build_controls(ml, "gpt-5.4-mini", None)
    assert [c.id for c in controls] == ["model"]  # no reasoning_effort control


def test_build_controls_appends_effort_slider_for_graded_model():
    ml = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True,
               efforts=["low", "medium", "high"], default_effort="medium"),
    ]})
    controls = build_controls(ml, "gpt-5.5", None)
    assert [c.id for c in controls] == ["model", "reasoning_effort"]
    effort = controls[1]
    assert effort.kind == "slider"
    assert effort.levels == ["low", "medium", "high"]
    assert effort.value == "medium"  # falls back to the model default


def test_build_controls_uses_requested_effort_when_valid():
    ml = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True,
               efforts=["low", "medium", "high"], default_effort="medium"),
    ]})
    controls = build_controls(ml, "gpt-5.5", "high")
    assert controls[1].value == "high"


def test_build_controls_requested_effort_invalid_falls_back_to_default():
    ml = parse_model_list({"data": [
        _entry("gpt-5.5", "GPT-5.5", default=True,
               efforts=["low", "medium"], default_effort="low"),
    ]})
    controls = build_controls(ml, "gpt-5.5", "xhigh")  # not supported
    assert controls[1].value == "low"


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
