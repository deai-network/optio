"""Custom session controls in Claude Code (custom session controls, Part B).

A task's own selectors (``optio_agents.custom_controls``): each option sets a
fixed combination of model / reasoning effort / permission mode. Pure units
here: the settings the defaults launch with, the selection derived from the
running values, the widget's ``customControls`` shape, what
``ClaudeCodeTaskConfig`` refuses, the listener's ``/control`` for
``custom:<id>`` ids (against a fake conversation) with the
``x-optio-custom-pick`` marker it emits, the selection ``x-optio-resumed``
carries, and the conversation taking a model the task itself names though
the CLI's list lacks it. The session tests are in
``test_custom_controls_session.py``.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from optio_agents import ControlSettings, CustomControl, CustomOption
from optio_agents.conversation import ConversationClosed
from optio_claudecode import controls as cc_controls
from optio_claudecode import conversation as cc_conversation
from optio_claudecode import models as cc_models
from optio_claudecode.conversation import ClaudeCodeConversation, ControlRejected
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.types import ClaudeCodeTaskConfig

from .fake_claude import CLI_MODELS
from .test_conversation_listener import FakeConversation, _auth
from .test_live_switch import _answer, _answer_readback, _conv, _done, _next_request

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


def test_a_control_without_a_label_has_a_null_label_in_the_snapshot():
    captionless = CustomControl(id="speed", label=None, default="extensive", options=SPEED.options)
    [snapshot] = cc_controls.custom_controls_snapshot(
        [captionless], catalog=CATALOG, model="opus", effort="xhigh", permission_mode=None,
    )
    assert snapshot["label"] is None and snapshot["value"] == "extensive"


# --- x-optio-resumed's customControls: the selection a resumed run launches with ------


def test_the_selection_is_each_controls_id_and_derived_value():
    assert cc_controls.custom_controls_selection(
        [SPEED, ACCESS], catalog=CATALOG,
        model="sonnet", effort="medium", permission_mode="acceptEdits",
    ) == [{"id": "speed", "value": "fast"}, {"id": "access", "value": "edits"}]
    # No option matching (a manual change) is null; without a catalog only
    # the same value matches.
    assert cc_controls.custom_controls_selection(
        [SPEED], catalog=[], model="haiku", effort="medium", permission_mode=None,
    ) == [{"id": "speed", "value": None}]
    assert cc_controls.custom_controls_selection(
        [SPEED], catalog=[], model="opus", effort="xhigh", permission_mode=None,
    ) == [{"id": "speed", "value": "extensive"}]
    assert cc_controls.custom_controls_selection(
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
    ``fail`` when set; an applied one is answered with an
    x-optio-control-update, as the session's hook does. ``log``: the type of
    every event fanned out and each apply_settings call, in order."""

    def __init__(self):
        super().__init__()
        self.applied: list[dict] = []
        self.fail: Exception | None = None
        self.busy = None
        self.log: list[str] = []
        self.listener: ConversationListener | None = None

    def fire(self, event):
        self.log.append(event.get("type"))
        super().fire(event)

    async def apply_settings(self, model=None, effort=None, permission_mode=None, *, busy=None):
        self.busy = busy
        self.log.append("apply_settings")
        if self.closed:
            raise ConversationClosed("closed")
        if self.fail is not None:
            raise self.fail
        self.applied.append({"model": model, "effort": effort, "permission_mode": permission_mode})
        self.emit_event({"type": "x-optio-control-update"})

    async def set_control(self, control_id, value):
        raise AssertionError(f"set_control({control_id!r}, {value!r})")


@pytest.fixture
async def custom():
    conv = _Conversation()
    refreshed: list[int] = []
    lst = conv.listener = ConversationListener(
        conv, password="pw",
        allowed_controls=[],                      # the controls bar is off
        custom_controls=[SPEED, ACCESS],
        refresh_controls=lambda: refreshed.append(1),
    )
    port = await lst.start("127.0.0.1")
    yield conv, f"http://127.0.0.1:{port}", refreshed
    await lst.stop()


def _pick_marks(conv: _Conversation) -> list[dict]:
    """The x-optio-custom-pick events in the listener's replay buffer."""
    return [e for _, e in conv.listener._buffer if e.get("type") == "x-optio-custom-pick"]


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


async def test_a_custom_pick_is_marked_in_the_event_stream_right_before_the_switch(custom):
    # Switch bands: the widget tells its own pick from a model change it did
    # not make by this marker, which precedes the switch and everything that
    # answers it, and is buffered (so replayed and persisted).
    conv, url, _ = custom
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (200, {"ok": True})
    assert conv.log == ["x-optio-custom-pick", "apply_settings", "x-optio-control-update"]
    assert [e for _, e in conv.listener._buffer] == [
        {"type": "x-optio-custom-pick", "id": "speed", "value": "fast"},
        {"type": "x-optio-control-update"},
    ]
    assert [e for _, e in conv.listener.export_buffer()][0] == {
        "type": "x-optio-custom-pick", "id": "speed", "value": "fast"}
    # Also a pick claude may not change anything for (the same option again).
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (200, {"ok": True})
    assert len(_pick_marks(conv)) == 2


@pytest.mark.parametrize("error", [
    ControlRejected("apply_flag_settings: no can do"),
    ValueError("reasoning effort 'xhigh' is not offered for claude-opus-4-8"),
    cc_conversation.SwitchBusy("a turn is running"),
])
async def test_a_pick_refused_by_the_switch_itself_was_marked_before_it(custom, error):
    # The marker goes out before apply_settings is called, so a refusal the
    # switch raises follows it; the widget learns the outcome from the
    # running values that are re-sent then (x-optio-control-update).
    conv, url, refreshed = custom
    conv.fail = error
    assert (await _post(url, {"id": "custom:speed", "value": "fast"}))[0] == 409
    assert conv.log == ["x-optio-custom-pick", "apply_settings"]
    assert _pick_marks(conv) == [{"type": "x-optio-custom-pick", "id": "speed", "value": "fast"}]
    assert refreshed == [1]


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
    assert _pick_marks(conv) == []                     # no switch: no marker


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
    assert _pick_marks(conv) == []                        # refused before the switch: no marker
    conv.fire({"type": "system", "subtype": "session_state_changed", "state": "idle"})
    assert (await _post(url, {"id": "custom:speed", "value": "fast"}))[0] == 200
    assert conv.applied == [{"model": "sonnet", "effort": "medium", "permission_mode": None}]
    assert len(_pick_marks(conv)) == 1


async def test_a_pick_on_a_closed_conversation_is_refused(custom):
    conv, url, refreshed = custom
    conv.closed = True
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "closed"})
    assert conv.applied == [] and refreshed == []
    assert _pick_marks(conv) == []


async def test_a_conversation_closing_during_the_switch_is_closed(custom):
    conv, url, refreshed = custom
    conv.fail = ConversationClosed("process ended")
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "closed"})
    assert refreshed == []


async def test_the_switch_asks_the_listeners_own_turn_check_again(custom):
    # The listener's busy check is the one source of truth: the conversation
    # asks it again under its switch lock, right before each write.
    conv, url, _ = custom
    assert (await _post(url, {"id": "custom:speed", "value": "fast"}))[0] == 200
    assert conv.busy is not None and not conv.busy()
    conv.pending = True                                   # a message awaits its result
    assert conv.busy()
    conv.pending = False
    conv.fire({"type": "system", "subtype": "session_state_changed", "state": "running"})
    assert conv.busy()


async def test_a_switch_refused_as_busy_under_the_lock_is_409_busy(custom):
    conv, url, refreshed = custom
    conv.fail = cc_conversation.SwitchBusy("a turn is running")
    assert await _post(url, {"id": "custom:speed", "value": "fast"}) == (
        409, {"ok": False, "reason": "busy"})
    assert conv.applied == [] and refreshed == [1]


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


# --- the resume marker: the selection the resumed run launches with ----------------------


def test_the_resume_marker_carries_the_selection_the_session_passes_in():
    selection = [{"id": "speed", "value": "fast"}]
    restored = [(1, {"type": "user"}), (2, {"type": "result"})]
    lst = ConversationListener(FakeConversation(), password="pw", initial_events=restored,
                               resumed_custom_controls=selection)
    assert lst._buffer[-1] == (3, {"type": "x-optio-resumed",
                                   "customControls": [{"id": "speed", "value": "fast"}]})
    selection[0]["value"] = "extensive"                 # a copy: the marker stays
    assert lst._buffer[-1][1]["customControls"] == [{"id": "speed", "value": "fast"}]
    assert lst.export_buffer()[-1][1]["customControls"] == [{"id": "speed", "value": "fast"}]
    # A selection of none (no option matches the running values) is null.
    lst = ConversationListener(FakeConversation(), password="pw", initial_events=restored,
                               resumed_custom_controls=[{"id": "speed", "value": None}])
    assert lst._buffer[-1][1]["customControls"] == [{"id": "speed", "value": None}]


def test_without_custom_controls_the_resume_marker_has_no_selection_key():
    lst = ConversationListener(FakeConversation(), password="pw",
                               initial_events=[(1, {"type": "user"})])
    assert lst._buffer[-1] == (2, {"type": "x-optio-resumed"})


def test_a_fresh_start_has_no_resume_marker_even_with_a_selection():
    lst = ConversationListener(FakeConversation(), password="pw",
                               resumed_custom_controls=[{"id": "speed", "value": "fast"}])
    assert list(lst._buffer) == []


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
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("opus", "xhigh")
    with pytest.raises(ValueError):                    # one it does not name still is not
        await conv.apply_settings(model="claude-fable-9")
    await _done(handle, reader)


# --- a turn starting while the pick waits: refused before claude sees it ----------------


async def test_a_turn_starting_while_the_pick_asks_for_the_model_list_refuses_it():
    """Review Minor 1 (busy race): the pick passed the listener's busy check,
    then the switch yields before writing (here: claude is asked for its
    model list first) and a turn starts meanwhile. Claude would answer the
    model change only after that turn while the widget shows the new option:
    the switch is refused under its lock instead (409 busy, the running
    values re-sent) and nothing reaches claude."""
    conv, handle, reader = _conv(model="opus", effort="xhigh")
    conv.models_offered = None
    conv.task_models = cc_controls.task_models([SPEED])
    refreshed: list[int] = []
    lst = ConversationListener(
        conv, password="pw", allowed_controls=[], custom_controls=[SPEED],
        refresh_controls=lambda: refreshed.append(1),
    )
    port = await lst.start("127.0.0.1")
    try:
        pick = asyncio.create_task(_post(f"http://127.0.0.1:{port}", {"id": "custom:speed", "value": "fast"}))
        init = await _next_request(handle)
        assert init["request"] == {"subtype": "initialize"}
        # A turn starts (claude's own running state), then the list comes in.
        handle.stdout.feed({"type": "system", "subtype": "session_state_changed", "state": "running"})
        handle.stdout.feed({"type": "control_response", "response": {
            "subtype": "success", "request_id": init["request_id"],
            "response": {"models": CLI_MODELS}}})
        written = asyncio.create_task(handle.stdin.lines.get())
        done, _ = await asyncio.wait({pick, written}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
        assert written not in done, f"reached claude: {written.result()}"
        written.cancel()
        assert pick in done
        assert pick.result() == (409, {"ok": False, "reason": "busy"})
        assert refreshed == [1]
        assert (conv.model, conv.effort) == ("opus", "xhigh")
    finally:
        await lst.stop()
        await _done(handle, reader)


# --- the pick marker on a real conversation: ahead of what claude answers ---------------

# How Claude Code 2.1.296 echoes a model change made with apply_flag_settings
# (no turn around it).
_MODEL_ECHO = {"type": "user", "isReplay": True, "message": {
    "role": "user", "content": "<local-command-stdout>Set model to `claude-sonnet-5`</local-command-stdout>"}}


async def test_the_pick_marker_is_buffered_before_anything_claude_answers_the_switch_with():
    """Switch bands: the marker enters the conversation's own event stream
    before the request is written, so it precedes claude's echo of the model
    change and its answer in the replay buffer; the widget draws one band for
    the pick and none for the echo."""
    conv, handle, reader = _conv(model="opus", effort="xhigh")
    conv.task_models = cc_controls.task_models([SPEED])
    lst = ConversationListener(conv, password="pw", allowed_controls=[], custom_controls=[SPEED])
    port = await lst.start("127.0.0.1")
    try:
        pick = asyncio.create_task(
            _post(f"http://127.0.0.1:{port}", {"id": "custom:speed", "value": "fast"}))
        req = await _next_request(handle)
        assert req["request"] == {"subtype": "apply_flag_settings",
                                  "settings": {"model": "sonnet", "effortLevel": "medium"}}
        handle.stdout.feed(_MODEL_ECHO)
        _answer(handle, req)
        await _answer_readback(handle)
        assert await asyncio.wait_for(pick, 60) == (200, {"ok": True})
        # Dispatched in order: once this is buffered, so is all before it.
        conv.emit_event({"type": "x-test-mark"})
        for _ in range(6000):
            if any(e.get("type") == "x-test-mark" for _, e in lst._buffer):
                break
            await asyncio.sleep(0.01)
        assert [e for _, e in lst._buffer] == [
            {"type": "x-optio-custom-pick", "id": "speed", "value": "fast"},
            _MODEL_ECHO,
            {"type": "control_response",
             "response": {"subtype": "success", "request_id": req["request_id"]}},
            {"type": "x-test-mark"},
        ]
    finally:
        await lst.stop()
        await _done(handle, reader)
