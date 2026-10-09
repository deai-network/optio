import pytest

from optio_agents.session_controls import (
    SINGLE_OPTION_REASON,
    ControlOption,
    SessionControl,
    effort_control,
    model_control,
)


def test_select_to_dict_camelcase_and_disabled():
    c = SessionControl(
        id="model", kind="select", label="Model", value="a", category="model",
        options=[
            ControlOption("a", "A"),
            ControlOption("b", "B", disabled=True, why_disabled="plan-gated"),
        ],
    )
    d = c.to_dict()
    assert d["id"] == "model" and d["kind"] == "select" and d["value"] == "a"
    assert d["category"] == "model"
    assert d["options"][0] == {"value": "a", "label": "A", "disabled": False}
    assert d["options"][1] == {
        "value": "b", "label": "B", "disabled": True, "whyDisabled": "plan-gated",
    }


def test_segmented_levels_and_boolean_shapes():
    seg = SessionControl(id="thinking", kind="segmented", label="Thinking",
                         value="high", levels=["low", "high", "max"])
    assert seg.to_dict()["levels"] == ["low", "high", "max"]
    assert "options" not in seg.to_dict()
    b = SessionControl(id="wide", kind="boolean", label="Wide", value=True)
    bd = b.to_dict()
    assert bd["value"] is True and "options" not in bd and "levels" not in bd


def test_model_control_helper():
    c = model_control(
        models=[{"id": "m1", "label": "M1"},
                {"id": "m2", "label": "M2", "disabled": True, "disabledReason": "no plan"}],
        current="m1",
    )
    assert c.id == "model" and c.kind == "select" and c.value == "m1"
    opts = c.to_dict()["options"]
    assert opts[1]["disabled"] is True and opts[1]["whyDisabled"] == "no plan"


def test_control_level_disabled_serialization():
    # A control (not just an option) can be disabled with a hover reason.
    c = SessionControl(id="thinking", kind="segmented", label="Thinking",
                       value="on", levels=["on"],
                       disabled=True, why_disabled="always on")
    d = c.to_dict()
    assert d["disabled"] is True and d["whyDisabled"] == "always on"
    # default: enabled, no whyDisabled key
    e = SessionControl(id="mode", kind="select", label="Mode", value="a").to_dict()
    assert e["disabled"] is False and "whyDisabled" not in e


def test_model_control_single_option_auto_locks():
    one = model_control(models=[{"id": "only", "label": "Only"}], current="only")
    assert one.disabled is True and one.why_disabled == SINGLE_OPTION_REASON
    two = model_control(models=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                        current="a")
    assert two.disabled is False and two.why_disabled is None


def test_effort_control_builds_slider():
    c = effort_control(levels=["low", "medium", "high"], current="high")
    assert c.id == "reasoning_effort" and c.kind == "slider"
    assert c.category == "thought_level" and c.value == "high"
    d = c.to_dict()
    assert d["kind"] == "slider"
    assert d["levels"] == ["low", "medium", "high"]
    assert d["value"] == "high"
    assert "options" not in d


def test_effort_control_defaults_current_to_first_level():
    c = effort_control(levels=["low", "medium", "high"], current=None)
    assert c.value == "low"
    # empty levels degrade to an empty-string value rather than raising
    empty = effort_control(levels=[], current=None)
    assert empty.value == "" and empty.to_dict()["levels"] == []


def test_effort_control_disabled_locks():
    c = effort_control(levels=["high"], current="high",
                       disabled=True, why_disabled="always on")
    d = c.to_dict()
    assert d["disabled"] is True and d["whyDisabled"] == "always on"


# --- per-task allowlist of offered controls (session_controls) ---------------

def _snapshot():
    return [
        {"id": "model", "kind": "select", "value": "a"},
        {"id": "reasoning_effort", "kind": "slider", "value": "high"},
        {"id": "permission_mode", "kind": "select", "value": "default"},
    ]


def test_filter_controls_without_allowlist_keeps_everything():
    from optio_agents.session_controls import filter_controls
    assert filter_controls(_snapshot(), None) == _snapshot()


def test_filter_controls_keeps_only_the_listed_ids_in_their_own_order():
    from optio_agents.session_controls import filter_controls
    kept = filter_controls(_snapshot(), ["permission_mode", "model"])
    assert [c["id"] for c in kept] == ["model", "permission_mode"]


def test_filter_controls_with_an_empty_allowlist_offers_nothing():
    from optio_agents.session_controls import filter_controls
    assert filter_controls(_snapshot(), []) == []


def test_control_allowed():
    from optio_agents.session_controls import control_allowed
    assert control_allowed("anything", None) is True
    assert control_allowed("model", ["model"]) is True
    assert control_allowed("permission_mode", ["model"]) is False


def test_validate_session_controls_accepts_none_and_a_list_with_controls_shown():
    from optio_agents.session_controls import validate_session_controls
    validate_session_controls(None, show_session_controls=False, owner="XConfig")
    validate_session_controls(["model"], show_session_controls=True, owner="XConfig")


def test_validate_session_controls_requires_the_controls_to_be_shown():
    from optio_agents.session_controls import validate_session_controls
    with pytest.raises(ValueError, match="XConfig.*session_controls.*show_session_controls"):
        validate_session_controls(["model"], show_session_controls=False, owner="XConfig")


@pytest.mark.parametrize("bad", ["model", ["model", ""], ["model", 3]])
def test_validate_session_controls_rejects_anything_but_a_list_of_ids(bad):
    from optio_agents.session_controls import validate_session_controls
    with pytest.raises(ValueError, match="session_controls"):
        validate_session_controls(bad, show_session_controls=True, owner="XConfig")


# --- model descriptions; option variant and confirmation -----------------------

def test_model_control_carries_a_models_description():
    c = model_control(models=[{"id": "m1", "label": "M1", "description": "Fast and cheap"}, {"id": "m2"}], current="m1")
    opts = c.to_dict()["options"]
    assert opts[0]["description"] == "Fast and cheap"
    assert "description" not in opts[1]


def test_an_option_may_be_primary_or_danger_and_ask_a_confirmation():
    o = ControlOption("bypass", "Bypass", variant="danger", confirm="Switch to Bypass?").to_dict()
    assert o["variant"] == "danger" and o["confirm"] == "Switch to Bypass?"
    plain = ControlOption("a", "A").to_dict()
    assert "variant" not in plain and "confirm" not in plain
