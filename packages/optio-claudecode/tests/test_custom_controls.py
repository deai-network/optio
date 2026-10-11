"""Custom session controls in Claude Code (custom session controls, Part B).

A task's own selectors (``optio_agents.custom_controls``): each option sets a
fixed combination of model / reasoning effort / permission mode. Pure units
here: the settings the defaults launch with, the selection derived from the
running values, the widget's ``customControls`` shape, what
``ClaudeCodeTaskConfig`` refuses, the listener's ``/control`` for
``custom:<id>`` ids (against a fake conversation), and the conversation taking
a model the task itself names though the CLI's list lacks it. The session
tests are in ``test_custom_controls_session.py``.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from optio_agents import ControlSettings, CustomControl, CustomOption
from optio_agents.conversation import ConversationClosed
from optio_claudecode import controls as cc_controls
from optio_claudecode import models as cc_models
from optio_claudecode.conversation import ClaudeCodeConversation, ControlRejected
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.types import ClaudeCodeTaskConfig

from .fake_claude import CLI_MODELS
from .test_conversation_listener import FakeConversation, _auth
from .test_live_switch import _answer, _conv, _done, _next_request

CATALOG = cc_models.parse_cli_models(CLI_MODELS)

# The data chat's control (Global Constraints).
SPEED = CustomControl(
    id="speed", label={"en": "Model", "hu": "Modell", "de": "Modell"}, default="extensive",
    options=[
        CustomOption("fast", "Fast", ControlSettings(model="sonnet", reasoning_effort="medium")),
        CustomOption("extensive", "Extensive", ControlSettings(model="opus", reasoning_effort="xhigh")),
    ],
)
# A second control, owning the permission mode.
ACCESS = CustomControl(
    id="access", label="Access", default="edits", description={"en": "What may Claude do?"},
    options=[
        CustomOption("edits", "Edits", ControlSettings(permission_mode="acceptEdits"),
                     description="Edits files without asking"),
        CustomOption("auto", {"en": "Auto", "hu": "Automatikus"},
                     ControlSettings(permission_mode="auto")),
    ],
)


def _control(*settings: ControlSettings, id: str = "c") -> CustomControl:
    """A control with one option per settings, valued a, b, c, ...; default a."""
    values = "abcdefgh"
    return CustomControl(id=id, label=id.upper(), default="a", options=[
        CustomOption(values[i], values[i].upper(), s) for i, s in enumerate(settings)
    ])


# --- the defaults a session starts with ------------------------------------------


def test_the_defaults_are_the_settings_of_each_controls_default_option():
    assert cc_controls.custom_defaults([SPEED, ACCESS]) == {
        "model": "opus", "reasoning_effort": "xhigh", "permission_mode": "acceptEdits",
    }
    assert cc_controls.custom_defaults(None) == {}
    assert cc_controls.custom_defaults([]) == {}


def test_the_task_models_are_the_models_its_options_name():
    assert cc_controls.task_models([SPEED, ACCESS]) == frozenset({"sonnet", "opus"})
    assert cc_controls.task_models(None) == frozenset()


# --- the selection, derived from the running values (Review Focus 2) ---------------


def _value(control, *, model=None, effort=None, mode=None, catalog=CATALOG):
    return cc_controls.custom_control_value(
        control, catalog=catalog, model=model, effort=effort, permission_mode=mode,
    )


def test_the_selection_is_the_option_whose_settings_all_match():
    assert _value(SPEED, model="sonnet", effort="medium") == "fast"
    assert _value(SPEED, model="opus", effort="xhigh") == "extensive"


def test_no_option_matching_selects_none():
    assert _value(SPEED, model="sonnet", effort="xhigh") is None     # a manual effort change
    assert _value(SPEED, model="haiku", effort="medium") is None     # a manual model change
    assert _value(SPEED, model="opus", effort=None) is None          # an unknown level never matches


def test_a_setting_the_option_leaves_out_does_not_count():
    assert _value(ACCESS, model="haiku", effort="low", mode="auto") == "auto"
    assert _value(SPEED, model="sonnet", effort="medium", mode="plan") == "fast"


def test_a_model_matches_as_the_model_select_shows_it():
    # A full id an alias resolves to counts as that alias; no model is `default`.
    pinned = _control(ControlSettings(model="claude-sonnet-5"), ControlSettings(model="default"))
    assert _value(pinned, model="sonnet") == "a"
    assert _value(pinned, model="claude-sonnet-5") == "a"
    assert _value(pinned, model=None) == "b"
    # Without the CLI's list only the same value matches.
    assert _value(pinned, model="sonnet", catalog=cc_models.fallback_models()) is None


def test_manual_is_the_default_mode():
    asking = _control(ControlSettings(permission_mode="default"),
                      ControlSettings(permission_mode="plan"))
    assert _value(asking, mode="manual") == "a"
    assert _value(asking, mode="plan") == "b"


# --- widgetData.customControls / x-optio-control-update -----------------------------


def test_the_snapshot_carries_the_definitions_unresolved_and_the_derived_value():
    snapshot = cc_controls.custom_controls_snapshot(
        [SPEED, ACCESS], catalog=CATALOG,
        model="sonnet", effort="medium", permission_mode="acceptEdits",
    )
    assert snapshot == [
        {"id": "speed", "label": {"en": "Model", "hu": "Modell", "de": "Modell"},
         "description": None,
         "options": [{"value": "fast", "label": "Fast", "description": None},
                     {"value": "extensive", "label": "Extensive", "description": None}],
         "value": "fast"},
        {"id": "access", "label": "Access", "description": {"en": "What may Claude do?"},
         "options": [{"value": "edits", "label": "Edits",
                      "description": "Edits files without asking"},
                     {"value": "auto", "label": {"en": "Auto", "hu": "Automatikus"},
                      "description": None}],
         "value": "edits"},
    ]
    assert json.loads(json.dumps(snapshot)) == snapshot
    snapshot[0]["label"]["en"] = "changed"               # a copy: the definition stays
    assert SPEED.label["en"] == "Model"
    assert cc_controls.custom_controls_snapshot(
        None, catalog=CATALOG, model=None, effort=None, permission_mode=None) == []


def test_the_emitted_update_carries_the_custom_controls_when_given():
    conv = ClaudeCodeConversation()
    conv.emit_control_update([{"id": "model"}], custom_controls=[{"id": "speed"}])
    conv.emit_control_update([{"id": "model"}])
    assert conv._event_queue.get_nowait() == {
        "type": "x-optio-control-update", "controls": [{"id": "model"}],
        "customControls": [{"id": "speed"}],
    }
    assert conv._event_queue.get_nowait() == {
        "type": "x-optio-control-update", "controls": [{"id": "model"}],
    }


# --- what ClaudeCodeTaskConfig refuses ------------------------------------------------


def _config(**kw) -> ClaudeCodeTaskConfig:
    base = dict(consumer_instructions="x", fs_isolation=False, mode="conversation",
                conversation_ui=True, permission_mode="bypassPermissions")
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_the_data_chat_control_fits_a_data_chat_config():
    config = ClaudeCodeTaskConfig(
        consumer_instructions="x", fs_isolation=False, mode="conversation",
        conversation_ui=True, show_session_controls=False, tools=[],
        allowed_tools=["mcp__kontext__query"], custom_controls=[SPEED],
    )
    assert config.custom_controls == [SPEED]


@pytest.mark.parametrize("kw", [
    {"mode": "iframe", "conversation_ui": False},
    {"conversation_ui": False},
])
def test_custom_controls_need_the_conversation_ui(kw):
    with pytest.raises(ValueError, match=r"custom_controls requires mode='conversation' "
                                         r"and conversation_ui=True"):
        _config(custom_controls=[SPEED], **kw)
    _config(custom_controls=[], **kw)                  # none: nothing to show
    _config(custom_controls=None, **kw)


@pytest.mark.parametrize("mode", ["default", "plan"])
def test_an_option_that_asks_the_operator_needs_the_permission_gate(mode):
    control = _control(ControlSettings(permission_mode="acceptEdits"),
                       ControlSettings(permission_mode=mode), id="access")
    with pytest.raises(ValueError, match=rf"option 'b' of 'access' sets permission_mode='{mode}'"
                                         r".*permission gate"):
        _config(custom_controls=[control])
    _config(permission_gate=True, permission_mode=None, custom_controls=[control])


def test_a_bypass_option_needs_bypass_to_be_allowed():
    control = _control(ControlSettings(permission_mode="acceptEdits"),
                       ControlSettings(permission_mode="bypassPermissions"), id="access")
    for kw in ({"permission_mode": "acceptEdits"},
               {"permission_mode": "acceptEdits", "allow_bypass_permissions": False}):
        with pytest.raises(ValueError, match=r"option 'b' of 'access' sets "
                                             r"permission_mode='bypassPermissions'"
                                             r".*allow_bypass_permissions"):
            _config(custom_controls=[control], **kw)
    _config(permission_mode="acceptEdits", allow_bypass_permissions=True, custom_controls=[control])
    _config(permission_mode="bypassPermissions", custom_controls=[control])


@pytest.mark.parametrize("a, b", [
    (ControlSettings(model="opus"), ControlSettings(model="opus")),                          # the same
    (ControlSettings(model="opus"), ControlSettings(model="opus", reasoning_effort="low")),  # one within the other
    (ControlSettings(model="opus"), ControlSettings(reasoning_effort="low")),                # nothing in common
])
def test_two_options_that_could_both_match_are_refused(a, b):
    with pytest.raises(ValueError, match=r"options 'a' and 'b' of 'speed' could both match"):
        _config(custom_controls=[_control(a, b, id="speed")])


def test_options_that_differ_in_a_setting_both_set_are_accepted():
    _config(custom_controls=[_control(
        ControlSettings(model="opus"),
        ControlSettings(model="sonnet", reasoning_effort="low"),
        ControlSettings(model="sonnet", reasoning_effort="high"),
        id="speed",
    )])


# --- the listener: POST /control {id: "custom:<id>", value} ---------------------------


class _Conversation(FakeConversation):
    """FakeConversation plus apply_settings: records each call, raises
    ``fail`` when set."""

    def __init__(self):
        super().__init__()
        self.applied: list[dict] = []
        self.fail: Exception | None = None

    async def apply_settings(self, model=None, effort=None, permission_mode=None):
        if self.closed:
            raise ConversationClosed("closed")
        if self.fail is not None:
            raise self.fail
        self.applied.append({"model": model, "effort": effort, "permission_mode": permission_mode})

    async def set_control(self, control_id, value):
        raise AssertionError(f"set_control({control_id!r}, {value!r})")


@pytest.fixture
async def custom():
    conv = _Conversation()
    refreshed: list[int] = []
    lst = ConversationListener(
        conv, password="pw",
        allowed_controls=[],                      # the controls bar is off
        custom_controls=[SPEED, ACCESS],
        refresh_controls=lambda: refreshed.append(1),
    )
    port = await lst.start("127.0.0.1")
    yield conv, f"http://127.0.0.1:{port}", refreshed
    await lst.stop()


async def _post(url: str, body: dict) -> tuple[int, dict]:
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{url}/control", json=body, headers=_auth("pw")) as r:
            return r.status, await r.json()


async def test_a_custom_pick_applies_the_options_settings_in_one_call(custom):
    conv, url, refreshed = custom
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (200, {"ok": True})
    assert conv.applied == [{"model": "sonnet", "effort": "medium", "permission_mode": None}]
    assert await _post(url, {"id": "custom:access", "value": "auto"}) == (200, {"ok": True})
    assert conv.applied[-1] == {"model": None, "effort": None, "permission_mode": "auto"}
    assert refreshed == []


async def test_custom_ids_are_accepted_while_the_built_in_controls_are_not(custom):
    conv, url, _ = custom
    assert (await _post(url, {"id": "model", "value": "sonnet"}))[0] == 403
    assert (await _post(url, {"id": "custom:speed", "value": "extensive"}))[0] == 200


@pytest.mark.parametrize("body, reason", [
    ({"id": "custom:nope", "value": "fast"}, "bad-id"),
    ({"id": "custom:", "value": "fast"}, "bad-id"),
    ({"id": "custom:speed", "value": "turbo"}, "bad-value"),
    ({"id": "custom:speed", "value": "auto"}, "bad-value"),       # another control's option
    ({"id": "custom:speed", "value": None}, "bad-value"),
    ({"id": "custom:speed"}, "bad-value"),
    ({"id": "custom:speed", "value": ["fast"]}, "bad-value"),
])
async def test_an_unknown_control_or_option_is_400(custom, body, reason):
    conv, url, refreshed = custom
    assert await _post(url, body) == (400, {"ok": False, "reason": reason})
    assert conv.applied == [] and refreshed == []


async def test_a_listener_without_custom_controls_knows_no_custom_id():
    conv = _Conversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    try:
        assert await _post(f"http://127.0.0.1:{port}", {"id": "custom:speed", "value": "fast"}) == (
            400, {"ok": False, "reason": "bad-id"})
    finally:
        await lst.stop()


async def test_a_pick_while_a_turn_runs_is_refused_and_changes_nothing(custom):
    # Review Focus 1: 409 busy, nothing applied; the running values are
    # re-sent so the widget's optimistic selection is replaced.
    conv, url, refreshed = custom
    conv.pending = True                                   # a message awaits its result
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "busy"})
    assert conv.applied == [] and refreshed == [1]
    conv.pending = False
    conv.fire({"type": "system", "subtype": "session_state_changed", "state": "running"})
    assert (await _post(url, {"id": "custom:speed", "value": "fast"}))[0] == 409
    assert conv.applied == [] and refreshed == [1, 1]
    conv.fire({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    assert (await _post(url, {"id": "custom:speed", "value": "fast"}))[0] == 200
    assert conv.applied == [{"model": "sonnet", "effort": "medium", "permission_mode": None}]


async def test_a_pick_on_a_closed_conversation_is_refused(custom):
    conv, url, refreshed = custom
    conv.closed = True
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "closed"})
    assert conv.applied == [] and refreshed == []


async def test_a_conversation_closing_during_the_switch_is_closed(custom):
    conv, url, refreshed = custom
    conv.fail = ConversationClosed("process ended")
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "closed"})
    assert refreshed == []


@pytest.mark.parametrize("error", [
    ControlRejected("apply_flag_settings: no can do"),                       # claude refused it
    ValueError("reasoning effort 'xhigh' is not offered for claude-opus-4-8"),  # the session cannot
])
async def test_a_refused_switch_is_409_rejected_with_the_reason(custom, error):
    conv, url, refreshed = custom
    conv.fail = error
    assert await _post(url, {"id": "custom:speed", "value": "extensive"}) == (
        409, {"ok": False, "reason": "rejected", "error": str(error)})
    assert conv.applied == [] and refreshed == [1]


# --- a model the task names is switched to as given --------------------------------------


async def test_a_model_the_task_names_is_switched_to_though_the_cli_list_lacks_it():
    # The CLI's list holds its picker's entries (no plain `opus` in CLI_MODELS),
    # while claude takes any alias it knows: a model a custom option names is
    # the task's own choice, like a configured model.
    conv, handle, reader = _conv(model="sonnet", effort="medium")
    with pytest.raises(ValueError):
        await conv.apply_settings(model="opus", effort="xhigh")
    assert handle.stdin.lines.empty()
    conv.task_models = frozenset({"opus", "sonnet"})
    switch = asyncio.create_task(conv.apply_settings(model="opus", effort="xhigh"))
    req = await _next_request(handle)
    assert req["request"] == {"subtype": "apply_flag_settings",
                              "settings": {"model": "opus", "effortLevel": "xhigh"}}
    _answer(handle, req)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("opus", "xhigh")
    with pytest.raises(ValueError):                    # one it does not name still is not
        await conv.apply_settings(model="claude-fable-9")
    await _done(handle, reader)
