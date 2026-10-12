"""Live model and reasoning-effort switching (custom session controls, Part B).

A model or effort change reaches the running claude as ONE stream-json control
request ``apply_flag_settings`` carrying both values, no restart; a permission
mode change as ``set_permission_mode``. Claude Code 2.1.296 (spike, Task 1):
the request answers success or error, keys left out or null fall back to the
account's defaults (not the launch flags), and it accepts unknown models and
silently drops unknown effort levels, so optio checks each value against the
CLI's own model list first.
"""

import asyncio

import pytest

from optio_agents.conversation import ConversationClosed
from optio_claudecode import models as cc_models
from optio_claudecode import conversation as cc_conversation
from optio_claudecode.conversation import ClaudeCodeConversation, ControlRejected

from .fake_claude import CLI_MODELS
from .test_conversation_driver import _FakeHandle

ALL = ["low", "medium", "high", "xhigh", "max"]
CATALOG = cc_models.parse_cli_models(CLI_MODELS) + [
    {"id": "claude-opus-4-8", "label": "Opus 4.8", "resolved": "claude-opus-4-8",
     "effort": ["low", "medium", "high"]},
]


def _conv(*, model=None, effort=None, modes=("default", "plan", "acceptEdits")):
    handle = _FakeHandle()
    conv = ClaudeCodeConversation(permission_gate=True)
    conv.permission_modes_offered = list(modes)
    conv.attach(handle)
    conv.models_offered = CATALOG
    conv.model, conv.effort = model, effort
    reader = asyncio.create_task(conv.run_reader())
    return conv, handle, reader


async def _next_request(handle) -> dict:
    line = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert line["type"] == "control_request", line
    return line


def _answer(handle, req: dict, *, error: str | None = None) -> None:
    response = ({"subtype": "error", "request_id": req["request_id"], "error": error}
                if error is not None else
                {"subtype": "success", "request_id": req["request_id"]})
    handle.stdout.feed({"type": "control_response", "response": response})


async def _answer_readback(handle, *, model=None, effort=None, error=None) -> None:
    """Answer the get_settings an applied switch asks right after it (what
    claude runs: ``applied.model`` / ``applied.effort``; None leaves one out,
    so it is not compared)."""
    req = await _next_request(handle)
    assert req["request"] == {"subtype": "get_settings"}, req
    if error is not None:
        _answer(handle, req, error=error)
        return
    applied = {k: v for k, v in (("model", model), ("effort", effort)) if v is not None}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": req["request_id"],
        "response": {"effective": {}, "sources": [], "applied": applied}}})


async def _done(handle, reader) -> None:
    handle.stdout.eof()
    await reader


class _Hook:
    """conversation.on_settings_change: awaited after every switch claude
    answered, with whether it took it; records the values it saw then."""

    def __init__(self, conv):
        self.calls: list[tuple] = []
        self._conv = conv

    async def __call__(self, applied: bool) -> None:
        self.calls.append((applied, self._conv.model, self._conv.effort))


# --- one request carrying both values ------------------------------------------

async def test_a_model_pick_sends_one_apply_request_carrying_model_and_effort():
    conv, handle, reader = _conv(model="opus[1m]", effort="xhigh")
    hook = conv.on_settings_change = _Hook(conv)
    switch = asyncio.create_task(conv.set_control("model", "sonnet"))
    req = await _next_request(handle)
    assert req["request"] == {"subtype": "apply_flag_settings",
                              "settings": {"model": "sonnet", "effortLevel": "xhigh"}}
    assert not switch.done()                       # waits for claude's answer
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("sonnet", "xhigh")
    assert hook.calls == [(True, "sonnet", "xhigh")]
    assert handle.stdin.lines.empty()              # nothing else: no restart, no set_model
    assert not conv.closed
    await _done(handle, reader)


async def test_an_effort_pick_sends_the_running_model_with_it():
    # Nothing configured or picked: claude runs its default, which the request
    # names explicitly (a missing key would fall back to the account default).
    conv, handle, reader = _conv(model=None, effort=None)
    switch = asyncio.create_task(conv.set_control("reasoning_effort", "low"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "default", "effortLevel": "low"}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == (None, "low")
    await _done(handle, reader)


async def test_apply_settings_sets_model_and_effort_in_one_request():
    conv, handle, reader = _conv(model="opus[1m]", effort="xhigh")
    switch = asyncio.create_task(conv.apply_settings(model="sonnet", effort="medium"))
    req = await _next_request(handle)
    assert req["request"] == {"subtype": "apply_flag_settings",
                              "settings": {"model": "sonnet", "effortLevel": "medium"}}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("sonnet", "medium")
    assert handle.stdin.lines.empty()
    await _done(handle, reader)


async def test_the_effort_gives_way_to_the_new_models_default_when_it_lacks_the_level():
    conv, handle, reader = _conv(model="opus[1m]", effort="xhigh")
    switch = asyncio.create_task(conv.apply_settings(model="claude-opus-4-8"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "claude-opus-4-8", "effortLevel": "high"}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.effort == "high"
    await _done(handle, reader)


async def test_no_effort_known_sends_the_level_the_slider_shows():
    conv, handle, reader = _conv(model=None, effort=None)
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "sonnet", "effortLevel": cc_models.DEFAULT_EFFORT}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    await _done(handle, reader)


async def test_a_model_without_effort_levels_keeps_the_running_level():
    # Haiku lists no levels: the level stays, for when a model that has it is picked.
    conv, handle, reader = _conv(model="sonnet", effort="low")
    switch = asyncio.create_task(conv.apply_settings(model="haiku"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "haiku", "effortLevel": "low"}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    await _done(handle, reader)


async def test_switches_run_one_at_a_time_so_the_second_carries_the_first():
    # Both values ride on every request: an effort pick computed while a model
    # pick is unanswered would carry the old model and undo the pick.
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    first = asyncio.create_task(conv.apply_settings(model="sonnet"))
    first_req = await _next_request(handle)
    second = asyncio.create_task(conv.apply_settings(effort="low"))
    _answer(handle, first_req)
    await _answer_readback(handle)
    await asyncio.wait_for(first, 60)
    second_req = await _next_request(handle)
    assert second_req["request"]["settings"] == {"model": "sonnet", "effortLevel": "low"}
    _answer(handle, second_req)
    await _answer_readback(handle)
    await asyncio.wait_for(second, 60)
    assert (conv.model, conv.effort) == ("sonnet", "low")
    await _done(handle, reader)


# --- values the session does not offer never reach claude -----------------------

@pytest.mark.parametrize("kw", [
    {"model": "no-such-model"},
    {"model": ""},
    {"model": 3},
    {"effort": "turbo"},
    {"model": "claude-opus-4-8", "effort": "xhigh"},   # that model lists low/medium/high
    {"permission_mode": "bypassPermissions"},          # not offered by this session
])
async def test_a_value_not_offered_is_refused_before_claude_sees_it(kw):
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    hook = conv.on_settings_change = _Hook(conv)
    with pytest.raises(ValueError):
        await conv.apply_settings(**kw)
    assert handle.stdin.lines.empty()
    assert (conv.model, conv.effort) == ("opus[1m]", "high")
    assert hook.calls == []
    await _done(handle, reader)


@pytest.mark.parametrize("cid", ["model", "reasoning_effort", "permission_mode"])
@pytest.mark.parametrize("value", [None, "", 3])
async def test_set_control_refuses_a_value_that_is_not_a_string(cid, value):
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    with pytest.raises(ValueError):
        await conv.set_control(cid, value)
    assert handle.stdin.lines.empty()
    await _done(handle, reader)


async def test_the_running_model_is_accepted_even_when_the_list_lacks_it():
    conv, handle, reader = _conv(model="claude-fable-9", effort=None)
    switch = asyncio.create_task(conv.apply_settings(effort="max"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "claude-fable-9", "effortLevel": "max"}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    await _done(handle, reader)


# --- a refused switch: nothing changes, the error surfaces (Review Focus 4) -------

async def test_a_refused_switch_leaves_the_values_and_raises_claudes_message():
    conv, handle, reader = _conv(model="opus[1m]", effort="xhigh")
    conv._route({"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"})
    hook = conv.on_settings_change = _Hook(conv)
    switch = asyncio.create_task(conv.apply_settings(model="sonnet", effort="medium"))
    req = await _next_request(handle)
    _answer(handle, req, error="apply_flag_settings: no can do")
    with pytest.raises(ControlRejected, match="no can do"):
        await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("opus[1m]", "xhigh")
    assert conv.runtime_model == "claude-opus-5[1m]"
    # The owning body re-shows the unchanged values (the widget patched its
    # select optimistically) and keeps the picks as they were.
    assert hook.calls == [(False, "opus[1m]", "xhigh")]
    assert handle.stdin.lines.empty()              # nothing applied: nothing read back
    await _done(handle, reader)


async def test_a_refused_switch_after_a_mode_switch_puts_the_mode_back():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv.permission_mode = "default"
    switch = asyncio.create_task(conv.apply_settings(
        model="sonnet", effort="medium", permission_mode="plan"))
    mode_req = await _next_request(handle)
    assert mode_req["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    _answer(handle, mode_req)
    flags_req = await _next_request(handle)
    assert flags_req["request"]["subtype"] == "apply_flag_settings"
    _answer(handle, flags_req, error="refused")
    back = await _next_request(handle)
    assert back["request"] == {"subtype": "set_permission_mode", "mode": "default"}
    _answer(handle, back)
    with pytest.raises(ControlRejected, match="refused"):
        await asyncio.wait_for(switch, 60)
    assert conv.permission_mode == "default"
    assert (conv.model, conv.effort) == ("opus[1m]", "high")
    await _done(handle, reader)


async def test_a_switch_on_a_closed_conversation_raises_closed():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    await _done(handle, reader)
    with pytest.raises(ConversationClosed):
        await conv.apply_settings(model="sonnet")


async def test_a_switch_whose_process_ends_first_raises_closed_and_changes_nothing():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    await _next_request(handle)
    await _done(handle, reader)
    with pytest.raises(ConversationClosed):
        await asyncio.wait_for(switch, 60)
    assert conv.model == "opus[1m]"


# --- the permission mode -----------------------------------------------------------

async def test_a_permission_mode_alone_goes_through_set_permission_mode():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    hook = conv.on_settings_change = _Hook(conv)
    switch = asyncio.create_task(conv.apply_settings(permission_mode="plan"))
    req = await _next_request(handle)
    assert req["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    _answer(handle, req)
    await asyncio.wait_for(switch, 60)
    assert conv.permission_mode == "plan"
    assert handle.stdin.lines.empty()              # no apply_flag_settings
    assert hook.calls == []                        # the mode has its own path (permission_mode_observed)
    await _done(handle, reader)


async def test_mode_model_and_effort_together():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    switch = asyncio.create_task(conv.apply_settings(
        model="sonnet", effort="medium", permission_mode="acceptEdits"))
    mode_req = await _next_request(handle)
    assert mode_req["request"] == {"subtype": "set_permission_mode", "mode": "acceptEdits"}
    _answer(handle, mode_req)
    flags_req = await _next_request(handle)
    assert flags_req["request"] == {"subtype": "apply_flag_settings",
                                    "settings": {"model": "sonnet", "effortLevel": "medium"}}
    _answer(handle, flags_req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort, conv.permission_mode) == ("sonnet", "medium", "acceptEdits")
    assert handle.stdin.lines.empty()
    await _done(handle, reader)


# --- what the running claude reports --------------------------------------------------

async def test_a_model_switch_forgets_the_old_runtime_model_until_the_next_turn_names_it():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv._route({"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"})
    switch = asyncio.create_task(conv.apply_settings(effort="low"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.runtime_model == "claude-opus-5[1m]"   # effort only: same model
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.runtime_model is None
    assert not conv.runtime_model_observed.is_set()
    conv._route({"type": "system", "subtype": "init", "model": "claude-sonnet-5"})
    assert conv.runtime_model == "claude-sonnet-5"
    await _done(handle, reader)


async def test_without_a_model_list_the_first_switch_asks_claude_for_one():
    conv, handle, reader = _conv(model=None, effort=None)
    conv.models_offered = None
    switch = asyncio.create_task(conv.apply_settings(model="sonnet", effort="low"))
    init = await _next_request(handle)
    assert init["request"] == {"subtype": "initialize"}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"],
        "response": {"models": CLI_MODELS}}})
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "sonnet", "effortLevel": "low"}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.models_offered == cc_models.parse_cli_models(CLI_MODELS)
    await _done(handle, reader)


async def test_while_claude_cannot_list_its_models_a_model_is_taken_as_given():
    conv, handle, reader = _conv(model=None, effort=None)
    conv.models_offered = None
    switch = asyncio.create_task(conv.apply_settings(model="claude-opus-4-8"))
    init = await _next_request(handle)
    assert init["request"] == {"subtype": "initialize"}
    _answer(handle, init, error="Unsupported control request subtype: initialize")
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "claude-opus-4-8",
                                          "effortLevel": cc_models.DEFAULT_EFFORT}
    _answer(handle, req)
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.model == "claude-opus-4-8"
    assert conv.models_offered is None               # still unknown: the next switch asks again
    await _done(handle, reader)


async def test_a_hook_that_raises_does_not_turn_an_applied_switch_into_an_error():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")

    async def _boom(applied):
        raise RuntimeError("hook bug")
    conv.on_settings_change = _boom
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.model == "sonnet"
    await _done(handle, reader)


# --- what claude runs, read back after a switch (get_settings) ----------------------

class _Mismatches:
    """conversation.on_settings_mismatch: records each text."""

    def __init__(self):
        self.texts: list[str] = []

    def __call__(self, text: str) -> None:
        self.texts.append(text)


async def test_an_applied_switch_asks_claude_what_it_runs_and_keeps_the_answer_private():
    conv, handle, reader = _conv(model="opus[1m]", effort="xhigh")
    mismatches = conv.on_settings_mismatch = _Mismatches()
    events: list[dict] = []
    conv.on_event(events.append)
    switch = asyncio.create_task(conv.apply_settings(model="sonnet", effort="medium"))
    _answer(handle, await _next_request(handle))
    readback = await _next_request(handle)
    assert readback["request"] == {"subtype": "get_settings"}
    assert not switch.done()                       # the switch waits for the readback
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": readback["request_id"],
        "response": {"effective": {"model": "sonnet", "effortLevel": "medium"},
                     "applied": {"model": "claude-sonnet-5", "effort": "medium"}}}})
    await asyncio.wait_for(switch, 60)
    assert mismatches.texts == []                  # sonnet resolves to claude-sonnet-5
    await _done(handle, reader)
    assert not any(e.get("type") == "control_response"
                   and e["response"]["request_id"] == readback["request_id"] for e in events)


async def test_an_effort_claude_did_not_take_is_logged_and_reported_and_the_values_stay(caplog):
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    mismatches = conv.on_settings_mismatch = _Mismatches()
    hook = conv.on_settings_change = _Hook(conv)
    switch = asyncio.create_task(conv.apply_settings(effort="xhigh"))
    _answer(handle, await _next_request(handle))
    with caplog.at_level("WARNING", logger="optio_claudecode.conversation"):
        await _answer_readback(handle, model="claude-opus-5[1m]", effort="high")
        await asyncio.wait_for(switch, 60)
    text = "Claude Code reports it runs effort=high (optio set xhigh)"
    assert mismatches.texts == [text]
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
        f"conversation: {text}"]
    # The recorded values (and so the selection derived from them) stay.
    assert (conv.model, conv.effort) == ("opus[1m]", "xhigh")
    assert hook.calls == [(True, "opus[1m]", "xhigh")]
    await _done(handle, reader)


async def test_a_model_claude_does_not_run_is_reported_naming_both():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    mismatches = conv.on_settings_mismatch = _Mismatches()
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle, model="claude-haiku-4-5-20251001", effort="low")
    await asyncio.wait_for(switch, 60)
    assert mismatches.texts == [
        "Claude Code reports it runs model=claude-haiku-4-5-20251001 (optio set sonnet), "
        "effort=low (optio set high)"]
    await _done(handle, reader)


@pytest.mark.parametrize("answer", [
    {"error": "Unsupported control request subtype: get_settings"},
    {},                                                    # no applied values
    {"model": "claude-opus-5-5"},                          # `opus`: the list does not resolve it
])
async def test_what_cannot_be_told_is_not_compared(answer):
    conv, handle, reader = _conv(model="sonnet", effort="medium")
    conv.task_models = frozenset({"opus"})
    mismatches = conv.on_settings_mismatch = _Mismatches()
    switch = asyncio.create_task(conv.apply_settings(model="opus"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle, **answer)
    await asyncio.wait_for(switch, 60)
    assert mismatches.texts == []
    assert conv.model == "opus"
    await _done(handle, reader)


async def test_check_settings_compares_the_launch_values_on_its_own():
    # The owning body calls it once after launch: no switch involved.
    conv, handle, reader = _conv(model=None, effort="xhigh")
    mismatches = conv.on_settings_mismatch = _Mismatches()
    check = asyncio.create_task(conv.check_settings())
    await _answer_readback(handle, model="claude-opus-5[1m]", effort="medium")
    text = "Claude Code reports it runs effort=medium (optio set xhigh)"
    assert await asyncio.wait_for(check, 60) == text
    assert mismatches.texts == [text]
    await _done(handle, reader)


async def test_a_mismatch_hook_that_raises_does_not_fail_the_switch():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")

    def _boom(text):
        raise RuntimeError("hook bug")
    conv.on_settings_mismatch = _boom
    switch = asyncio.create_task(conv.apply_settings(effort="xhigh"))
    _answer(handle, await _next_request(handle))
    await _answer_readback(handle, effort="high")
    await asyncio.wait_for(switch, 60)
    assert conv.effort == "xhigh"
    await _done(handle, reader)


# --- busy: asked under the switch lock, right before each write ----------------------

async def test_a_turn_starting_while_the_switch_asks_for_the_model_list_refuses_it():
    # The switch yields before writing (claude is asked for its model list
    # first); a turn starts meanwhile. Claude would answer a model change only
    # after that turn: nothing is written, the values stay.
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv.models_offered = None
    hook = conv.on_settings_change = _Hook(conv)
    turn = {"running": False}
    switch = asyncio.create_task(conv.apply_settings(
        model="sonnet", effort="medium", busy=lambda: turn["running"]))
    init = await _next_request(handle)
    assert init["request"] == {"subtype": "initialize"}
    turn["running"] = True
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": init["request_id"],
        "response": {"models": CLI_MODELS}}})
    with pytest.raises(cc_conversation.SwitchBusy):
        await asyncio.wait_for(switch, 60)
    assert handle.stdin.lines.empty()
    assert (conv.model, conv.effort) == ("opus[1m]", "high")
    assert hook.calls == []
    await _done(handle, reader)


async def test_busy_before_the_mode_write_switches_nothing():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv.permission_mode = "default"
    with pytest.raises(cc_conversation.SwitchBusy):
        await conv.apply_settings(model="sonnet", permission_mode="plan", busy=lambda: True)
    assert handle.stdin.lines.empty()
    assert (conv.model, conv.permission_mode) == ("opus[1m]", "default")
    await _done(handle, reader)


async def test_a_turn_starting_after_the_mode_switch_puts_the_mode_back():
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv.permission_mode = "default"
    turn = {"running": False}
    switch = asyncio.create_task(conv.apply_settings(
        model="sonnet", effort="medium", permission_mode="plan", busy=lambda: turn["running"]))
    mode_req = await _next_request(handle)
    assert mode_req["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    turn["running"] = True
    _answer(handle, mode_req)
    back = await _next_request(handle)                 # no apply_flag_settings
    assert back["request"] == {"subtype": "set_permission_mode", "mode": "default"}
    _answer(handle, back)
    with pytest.raises(cc_conversation.SwitchBusy):
        await asyncio.wait_for(switch, 60)
    assert handle.stdin.lines.empty()
    assert (conv.model, conv.effort, conv.permission_mode) == ("opus[1m]", "high", "default")
    await _done(handle, reader)
