"""Unit tests for the kimi conversation-widget model catalog (``models.py``).

kimi surfaces its model picker as the ACP ``configOptions`` unified list (a
single ``{type:'select', id:'model', currentValue, options:[{value,name}]}``
option) — NOT grok/cursor's ``models`` block. ``models.py`` maps that ACP
surface to the widget shape ``{models:[{id,label,disabled}], default}`` and
provides a static fallback of model ALIASES for the degenerate case.
"""

from optio_kimicode import models
from optio_kimicode.types import _VALID_EFFORTS


def test_efforts_surface_matches_type_enum_and_is_ordered():
    # models.EFFORTS is the ordered low..max surface the widget/consumers read;
    # it must cover exactly the KimiCodeTaskConfig.effort enum.
    assert models.EFFORTS == ("low", "medium", "high", "xhigh", "max")
    assert set(models.EFFORTS) == _VALID_EFFORTS


def test_parse_config_options_maps_the_model_select_option():
    # The unified configOptions list carries a `model` select alongside other
    # options; only the `model` option feeds the picker.
    config_options = [
        {
            "type": "select", "id": "model", "name": "Model", "category": "model",
            "currentValue": "kimi-k2",
            "options": [
                {"value": "kimi-k2", "name": "Kimi K2"},
                {"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"},
            ],
        },
        {
            "type": "select", "id": "mode", "name": "Mode", "category": "mode",
            "currentValue": "default",
            "options": [{"value": "default", "name": "Default"}],
        },
    ]
    out = models.parse_config_options(config_options)
    assert out["default"] == "kimi-k2"
    assert out["models"] == [
        {"id": "kimi-k2", "label": "Kimi K2", "disabled": False},
        {"id": "kimi-k2-thinking", "label": "Kimi K2 Thinking", "disabled": False},
    ]


def test_parse_config_options_labels_fall_back_to_the_value():
    out = models.parse_config_options([
        {"type": "select", "id": "model", "currentValue": "kimi-k2",
         "options": [{"value": "kimi-k2"}]},
    ])
    assert out["models"] == [{"id": "kimi-k2", "label": "kimi-k2", "disabled": False}]


def test_parse_config_options_ignores_malformed_entries():
    out = models.parse_config_options([
        {"type": "select", "id": "model", "currentValue": "kimi-k2",
         "options": [
             {"value": "kimi-k2", "name": "Kimi K2"},
             "not-a-dict",
             {"name": "no value"},
             {"value": "", "name": "empty id"},
         ]},
    ])
    assert out["models"] == [{"id": "kimi-k2", "label": "Kimi K2", "disabled": False}]


def test_parse_config_options_falls_back_when_no_model_option():
    # No `model` option present → never falsely empty the picker.
    out = models.parse_config_options([
        {"type": "select", "id": "mode", "currentValue": "default",
         "options": [{"value": "default", "name": "Default"}]},
    ])
    assert out == models.FALLBACK_MODELS
    # A defensive copy, not the module-level constant.
    assert out is not models.FALLBACK_MODELS


def test_parse_config_options_falls_back_on_bad_shapes():
    for bad in (None, {}, "nope", [], [{"id": "model"}], [{"id": "model", "options": "x"}]):
        out = models.parse_config_options(bad)
        assert out == models.FALLBACK_MODELS


def test_available_models_prefers_the_acp_surface():
    out = models.available_models([
        {"type": "select", "id": "model", "currentValue": "kimi-k2-thinking",
         "options": [{"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"}]},
    ])
    assert out["default"] == "kimi-k2-thinking"
    assert out["models"] == [
        {"id": "kimi-k2-thinking", "label": "Kimi K2 Thinking", "disabled": False},
    ]


def test_available_models_falls_back_when_surface_absent():
    assert models.available_models(None) == models.FALLBACK_MODELS
    assert models.available_models([]) == models.FALLBACK_MODELS


def test_fallback_catalog_is_kimi_aliases_with_a_valid_default():
    ids = [m["id"] for m in models.FALLBACK_MODELS["models"]]
    assert ids, "fallback must not be empty"
    assert all(i.startswith("kimi-") for i in ids)
    assert models.FALLBACK_MODELS["default"] in ids
