"""Custom session controls in the shared agent configuration: the definition
(ControlSettings, CustomOption, CustomControl, their texts checked as the shared
``optio_agents.text.Text``) and the mixin's validation. Text resolution itself
is tested with the shared helpers (test_text.py)."""
from dataclasses import dataclass

import pytest

import optio_agents
from optio_agents import (ControlSettings, CustomControl, CustomControlsConfigMixin,
                          CustomOption)
from optio_agents.custom_controls import KNOWN_PERMISSION_MODES, KNOWN_REASONING_EFFORTS


@dataclass(frozen=True, kw_only=True)
class _Cfg(CustomControlsConfigMixin):
    consumer_instructions: str = "x"


def _option(value="fast", label="Fast", **settings):
    return CustomOption(value=value, label=label,
                        settings=ControlSettings(**(settings or {"model": "sonnet"})))


def _speed(id="speed", **kw):
    """The data chat's control (plan, Global Constraints)."""
    return CustomControl(
        id=id,
        label={"en": "Model", "hu": "Modell", "de": "Modell"},
        default="extensive",
        options=[
            CustomOption(value="fast", label="Fast",
                         settings=ControlSettings(model="sonnet", reasoning_effort="medium")),
            CustomOption(value="extensive", label="Extensive",
                         settings=ControlSettings(model="opus", reasoning_effort="xhigh")),
        ],
        **kw,
    )


def _mode(id="mode"):
    """A control over the permission mode only."""
    return CustomControl(
        id=id, label="Mode", default="careful",
        options=[_option("careful", "Careful", permission_mode="default"),
                 _option("free", "Free", permission_mode="acceptEdits")],
    )


# --- exports ---------------------------------------------------------------------

def test_top_level_exports():
    for name in ("ControlSettings", "CustomOption", "CustomControl",
                 "CustomControlsConfigMixin"):
        assert hasattr(optio_agents, name), name
        assert name in optio_agents.__all__, name


def test_known_values():
    assert KNOWN_REASONING_EFFORTS == ("low", "medium", "high", "xhigh", "max")
    assert set(KNOWN_PERMISSION_MODES) == {
        "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"}


# --- Text validation (labels and descriptions) --------------------------------------

@pytest.mark.parametrize("label", ["", {}, {"en": ""}, {"": "Fast"}, {"en": 5}, 5, None],
                         ids=["empty-str", "empty-map", "empty-value", "empty-key",
                              "non-str-value", "int", "none"])
def test_a_bad_option_label_is_refused(label):
    with pytest.raises(ValueError, match="label"):
        _option(label=label)


def test_a_bad_description_is_refused():
    with pytest.raises(ValueError, match="description"):
        CustomOption(value="fast", label="Fast", description={"en": ""},
                     settings=ControlSettings(model="sonnet"))
    with pytest.raises(ValueError, match="description"):
        CustomControl(id="speed", label="Speed", description="", default="a",
                      options=[_option("a"), _option("b")])


def test_a_language_map_is_copied():
    labels = {"en": "Fast", "hu": "Gyors"}
    option = _option(label=labels)
    labels["en"] = "Changed"
    assert option.label == {"en": "Fast", "hu": "Gyors"}


def test_descriptions_are_optional():
    option = _option()
    assert option.description is None
    described = CustomOption(value="fast", label="Fast", description={"en": "Quick answers"},
                             settings=ControlSettings(model="sonnet"))
    assert described.description == {"en": "Quick answers"}


# --- ControlSettings -----------------------------------------------------------------

def test_settings_need_at_least_one_value():
    with pytest.raises(ValueError, match="at least one"):
        ControlSettings()


def test_settings_positional_order():
    assert ControlSettings("sonnet", "medium", "plan") == ControlSettings(
        model="sonnet", reasoning_effort="medium", permission_mode="plan")


@pytest.mark.parametrize("effort", KNOWN_REASONING_EFFORTS)
def test_settings_accept_every_known_effort(effort):
    assert ControlSettings(reasoning_effort=effort).reasoning_effort == effort


def test_settings_refuse_an_unknown_effort():
    with pytest.raises(ValueError, match="reasoning_effort='extreme'.*xhigh"):
        ControlSettings(reasoning_effort="extreme")


@pytest.mark.parametrize("mode", KNOWN_PERMISSION_MODES)
def test_settings_accept_every_known_permission_mode(mode):
    assert ControlSettings(permission_mode=mode).permission_mode == mode


def test_settings_refuse_an_unknown_permission_mode():
    with pytest.raises(ValueError, match="permission_mode='yolo'.*acceptEdits"):
        ControlSettings(permission_mode="yolo")


def test_settings_refuse_an_empty_model():
    with pytest.raises(ValueError, match="model"):
        ControlSettings(model="")
    with pytest.raises(ValueError, match="model"):
        ControlSettings(model=3)


def test_settings_to_dict_holds_only_the_set_values():
    assert ControlSettings(model="opus", reasoning_effort="xhigh").to_dict() == {
        "model": "opus", "reasoning_effort": "xhigh"}
    assert ControlSettings(permission_mode="plan").to_dict() == {"permission_mode": "plan"}


# --- CustomOption --------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "two words", "a.b", "é", 3])
def test_option_refuses_a_bad_value(value):
    with pytest.raises(ValueError, match="value"):
        _option(value=value)


def test_option_needs_control_settings():
    with pytest.raises(ValueError, match="settings"):
        CustomOption(value="fast", label="Fast", settings={"model": "sonnet"})


def test_option_positional_order():
    settings = ControlSettings(model="sonnet")
    option = CustomOption("fast", "Fast", settings, "Quick")
    assert (option.value, option.label, option.settings, option.description) == (
        "fast", "Fast", settings, "Quick")


# --- CustomControl -------------------------------------------------------------------

def test_the_data_chat_control_is_valid():
    control = _speed()
    assert control.id == "speed"
    assert control.default == "extensive"
    assert [o.value for o in control.options] == ["fast", "extensive"]
    assert control.description is None


def test_control_positional_order():
    options = [_option("a"), _option("b")]
    control = CustomControl("pick", "Pick", "a", options, "Which one")
    assert (control.id, control.label, control.default, control.options,
            control.description) == ("pick", "Pick", "a", options, "Which one")


def test_a_controls_label_is_optional():
    # None: the control shows no caption (an option's label stays required).
    control = CustomControl(id="speed", label=None, default="a",
                            options=[_option("a"), _option("b")])
    assert control.label is None
    assert CustomControl("speed", None, "a", [_option("a"), _option("b")]).label is None


@pytest.mark.parametrize("label", ["", {}, {"en": ""}], ids=["empty-str", "empty-map", "empty-value"])
def test_a_controls_empty_label_is_still_refused(label):
    with pytest.raises(ValueError, match="label"):
        CustomControl(id="speed", label=label, default="a", options=[_option("a"), _option("b")])


@pytest.mark.parametrize("bad_id", ["", "custom:speed", "has space", None])
def test_control_refuses_a_bad_id(bad_id):
    with pytest.raises(ValueError, match="id"):
        CustomControl(id=bad_id, label="X", default="a", options=[_option("a"), _option("b")])


def test_control_needs_two_options():
    with pytest.raises(ValueError, match="at least 2 options"):
        CustomControl(id="speed", label="X", default="a", options=[_option("a")])
    with pytest.raises(ValueError, match="at least 2 options"):
        CustomControl(id="speed", label="X", default="a", options=[])


def test_control_refuses_duplicate_option_values():
    with pytest.raises(ValueError, match="duplicate option value 'a'"):
        CustomControl(id="speed", label="X", default="a",
                      options=[_option("a"), _option("a", model="opus")])


def test_control_default_must_be_one_of_the_options():
    with pytest.raises(ValueError, match="default='c'"):
        CustomControl(id="speed", label="X", default="c", options=[_option("a"), _option("b")])


def test_control_options_must_be_custom_options():
    with pytest.raises(ValueError, match="CustomOption"):
        CustomControl(id="speed", label="X", default="a",
                      options=[_option("a"), {"value": "b", "label": "B"}])
    with pytest.raises(ValueError, match="options"):
        CustomControl(id="speed", label="X", default="a", options=_option("a"))


def test_control_owns_a_copy_of_its_options():
    options = [_option("a"), _option("b")]
    control = CustomControl(id="speed", label="X", default="a", options=options)
    options.append(_option("c"))
    assert [o.value for o in control.options] == ["a", "b"]
    assert isinstance(control.options, list)


# --- CustomControlsConfigMixin._validate_custom_controls -----------------------------

def test_unset_by_default():
    assert _Cfg().custom_controls is None


def test_unsupported_agent_refuses_custom_controls():
    cfg = _Cfg(custom_controls=[_speed()])
    with pytest.raises(ValueError, match="^codex does not support custom_controls yet$"):
        cfg._validate_custom_controls(supported=False, agent="codex")


def test_unsupported_agent_accepts_none_and_an_empty_list():
    _Cfg()._validate_custom_controls(supported=False, agent="codex")
    _Cfg(custom_controls=[])._validate_custom_controls(supported=False, agent="codex")


def test_supported_agent_accepts_controls_over_disjoint_settings():
    controls = [_speed(), _mode()]
    cfg = _Cfg(custom_controls=controls)
    cfg._validate_custom_controls(supported=True, agent="claudecode")
    assert cfg.custom_controls == controls


def test_duplicate_control_ids_are_refused():
    cfg = _Cfg(custom_controls=[_speed(), _mode(id="speed")])
    with pytest.raises(ValueError, match="duplicate control id 'speed'"):
        cfg._validate_custom_controls(supported=True, agent="claudecode")


def test_a_setting_set_by_two_controls_is_refused():
    effort = CustomControl(
        id="effort", label="Effort", default="low",
        options=[_option("low", reasoning_effort="low"),
                 _option("high", reasoning_effort="high")],
    )
    cfg = _Cfg(custom_controls=[_speed(), effort])
    with pytest.raises(ValueError, match="reasoning_effort.*'speed'.*'effort'"):
        cfg._validate_custom_controls(supported=True, agent="claudecode")


def test_a_setting_set_by_only_some_options_still_belongs_to_the_control():
    # 'speed2' sets the model in one option only; 'other' sets it too: refused.
    speed2 = CustomControl(
        id="speed2", label="Speed", default="a",
        options=[_option("a", reasoning_effort="low"),
                 _option("b", model="opus", reasoning_effort="high")],
    )
    other = CustomControl(
        id="other", label="Other", default="x",
        options=[_option("x", model="sonnet"), _option("y", model="haiku")],
    )
    cfg = _Cfg(custom_controls=[speed2, other])
    with pytest.raises(ValueError, match="model"):
        cfg._validate_custom_controls(supported=True, agent="claudecode")


def test_custom_controls_must_be_a_list_of_custom_controls():
    with pytest.raises(ValueError, match="list of CustomControl"):
        _Cfg(custom_controls=_speed())._validate_custom_controls(
            supported=True, agent="claudecode")
    with pytest.raises(ValueError, match="CustomControl"):
        _Cfg(custom_controls=[_speed(), {"id": "mode"}])._validate_custom_controls(
            supported=True, agent="claudecode")
