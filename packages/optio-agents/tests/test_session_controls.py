from optio_agents.session_controls import ControlOption, SessionControl, model_control


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
