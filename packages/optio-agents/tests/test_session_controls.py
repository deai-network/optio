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
