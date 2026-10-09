"""The permission_mode session control and the session_controls allowlist."""

import asyncio
import base64

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed
from optio_claudecode.controls import (
    build_controls,
    canonical_permission_mode,
    permission_mode_options,
    settable_permission_modes,
)
from optio_claudecode.conversation import ClaudeCodeConversation, ControlRejected
from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.host_actions import build_claude_flags
from optio_claudecode.types import ClaudeCodeTaskConfig

from .test_conversation_driver import _FakeHandle
from .test_conversation_listener import FakeConversation

CATALOG = [
    {"id": "claude-opus-4-8", "label": "Opus", "effort": ["low", "medium", "high"]},
    {"id": "claude-haiku-4-5", "label": "Haiku"},
]


# --- the permission modes a session lists -------------------------------------

ALL_MODES = ["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"]


def _opts(**kw):
    return {o.value: o for o in permission_mode_options(**kw)}


def test_every_session_lists_all_six_modes_in_claude_codes_order():
    for gate in (True, False):
        for bypass in (True, False):
            opts = permission_mode_options(permission_gate=gate, bypass_allowed=bypass)
            assert [o.value for o in opts] == ALL_MODES
            # Manual is Claude Code's current name for the wire value "default".
            assert opts[0].label == "Manual"
            assert all(o.label and o.description for o in opts)


def test_with_the_gate_every_mode_but_a_disallowed_bypass_is_enabled():
    opts = _opts(permission_gate=True, bypass_allowed=False)
    assert [m for m in ALL_MODES if not opts[m].disabled] == ALL_MODES[:-1]
    assert opts["bypassPermissions"].why_disabled


def test_without_the_gate_the_modes_that_ask_are_disabled_with_a_reason():
    # Manual asks before acting and Plan asks to approve its plan: without the
    # permission gate nobody can answer, so they are listed but disabled.
    opts = _opts(permission_gate=False, bypass_allowed=True)
    for mode in ("default", "plan"):
        assert opts[mode].disabled and "permission gate" in opts[mode].why_disabled
    for mode in ("acceptEdits", "auto", "dontAsk", "bypassPermissions"):
        assert not opts[mode].disabled and opts[mode].why_disabled is None


def test_settable_modes_are_the_enabled_ones():
    opts = permission_mode_options(permission_gate=False, bypass_allowed=False)
    assert settable_permission_modes(opts) == ["acceptEdits", "auto", "dontAsk"]


def test_manual_is_reported_as_default():
    assert canonical_permission_mode("manual") == "default"
    assert canonical_permission_mode("auto") == "auto"
    assert canonical_permission_mode(None) is None


# --- the controls snapshot ----------------------------------------------------

def _by_id(ctrls):
    return {c["id"]: c for c in ctrls}


def test_controls_carry_a_permission_mode_select():
    ctrls = _by_id(build_controls(
        catalog=CATALOG, model="claude-opus-4-8", effort=None,
        permission_mode="acceptEdits",
        permission_options=permission_mode_options(permission_gate=False, bypass_allowed=False),
    ))
    pm = ctrls["permission_mode"]
    assert pm["kind"] == "select" and pm["category"] == "mode"
    assert pm["value"] == "acceptEdits"
    assert [o["value"] for o in pm["options"]] == ALL_MODES
    opt = {o["value"]: o for o in pm["options"]}
    assert opt["plan"]["disabled"] is True and opt["plan"]["whyDisabled"]
    assert opt["auto"]["disabled"] is False and opt["auto"]["description"]
    assert pm["disabled"] is False
    # model and effort are unchanged
    assert ctrls["model"]["value"] == "claude-opus-4-8"
    assert ctrls["reasoning_effort"]["levels"] == ["low", "medium", "high"]


def test_a_manual_start_shows_as_the_manual_option():
    pm = _by_id(build_controls(
        catalog=CATALOG, model=None, effort=None, permission_mode="manual",
        permission_options=permission_mode_options(permission_gate=True, bypass_allowed=False),
    ))["permission_mode"]
    assert pm["value"] == "default"


def test_no_permission_options_means_no_permission_control():
    ctrls = _by_id(build_controls(
        catalog=CATALOG, model=None, effort=None, permission_mode=None, permission_options=[],
    ))
    assert "permission_mode" not in ctrls


def test_the_allowlist_narrows_the_snapshot():
    ctrls = build_controls(
        catalog=CATALOG, model="claude-opus-4-8", effort=None,
        permission_mode="default",
        permission_options=permission_mode_options(permission_gate=True, bypass_allowed=False),
        allowed=["permission_mode"],
    )
    assert [c["id"] for c in ctrls] == ["permission_mode"]


# --- config -------------------------------------------------------------------

def _cfg(**kw):
    base = dict(consumer_instructions="do things", fs_isolation=False, mode="conversation",
                permission_gate=True, conversation_ui=True)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_the_controls_settings_come_from_the_shared_mixin():
    from optio_agents.config_types import SessionControlsConfigMixin
    assert issubclass(ClaudeCodeTaskConfig, SessionControlsConfigMixin)
    assert _cfg(show_session_controls=True, session_controls=["model"]).settable_controls == ["model"]


def test_session_controls_defaults_to_offering_everything():
    assert _cfg(show_session_controls=True).session_controls is None


def test_session_controls_narrows_shown_controls():
    cfg = _cfg(show_session_controls=True, session_controls=["model", "permission_mode"])
    assert cfg.session_controls == ["model", "permission_mode"]


def test_bypass_follows_the_starting_mode_unless_set():
    # None (default): bypass is reachable only for a task that starts in it.
    assert _cfg(permission_mode="bypassPermissions").bypass_allowed is True
    assert _cfg(permission_mode="acceptEdits").bypass_allowed is False
    assert _cfg(permission_mode="manual", allow_bypass_permissions=True).bypass_allowed is True


def test_starting_in_bypass_contradicts_disallowing_it():
    with pytest.raises(ValueError, match="allow_bypass_permissions"):
        _cfg(permission_mode="bypassPermissions", allow_bypass_permissions=False)


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_claude_codes_current_mode_names_are_valid_starting_modes(mode):
    assert _cfg(permission_mode=mode).permission_mode == mode


def test_auto_needs_no_permission_gate_to_start():
    # auto decides by itself (its classifier); headless, a blocked action just
    # doesn't run, so an ungated conversation may start in it.
    cfg = _cfg(permission_gate=False, permission_mode="auto")
    assert cfg.permission_mode == "auto"


def test_session_controls_needs_show_session_controls():
    with pytest.raises(ValueError, match="session_controls"):
        _cfg(session_controls=["model"])


# --- the conversation: live switch and tracking -------------------------------

@pytest.mark.asyncio
async def test_status_and_init_report_the_running_permission_mode():
    conv = ClaudeCodeConversation(permission_gate=True)
    conv._route({"type": "system", "subtype": "init", "session_id": "s",
                 "model": "claude-opus-4-8", "permissionMode": "default"})
    assert conv.permission_mode == "default"
    assert conv.permission_mode_observed.is_set()
    conv.permission_mode_observed.clear()
    # Claude switches by itself too (e.g. leaving plan mode): status says so.
    conv._route({"type": "system", "subtype": "status", "status": None,
                 "permissionMode": "acceptEdits"})
    assert conv.permission_mode == "acceptEdits"
    assert conv.permission_mode_observed.is_set()


@pytest.mark.asyncio
async def test_set_control_switches_the_mode_live_through_the_control_protocol():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation(permission_gate=True)
    conv.permission_modes_offered = ["default", "plan"]
    conv.attach(handle)
    reader = asyncio.create_task(conv.run_reader())
    switch = asyncio.create_task(conv.set_control("permission_mode", "plan"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert ctrl["type"] == "control_request"
    assert ctrl["request"] == {"subtype": "set_permission_mode", "mode": "plan"}
    assert not switch.done()                       # waits for Claude's answer
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"], "response": {"mode": "plan"}}})
    await asyncio.wait_for(switch, 60)
    assert conv.permission_mode == "plan"
    # a mode switch is live: no restart is requested
    assert not conv.model_change_requested.is_set()
    assert not conv.effort_change_requested.is_set()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_refuses_a_mode_the_session_does_not_offer():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation(permission_gate=True)
    conv.permission_modes_offered = ["default", "plan"]
    conv.attach(handle)
    with pytest.raises(ValueError):
        await conv.set_control("permission_mode", "bypassPermissions")
    assert handle.stdin.lines.empty()              # nothing reached Claude


@pytest.mark.asyncio
async def test_a_refused_switch_raises_and_asks_for_a_fresh_snapshot():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation(permission_gate=True)
    conv.permission_modes_offered = ["default", "plan"]
    conv.attach(handle)
    reader = asyncio.create_task(conv.run_reader())
    switch = asyncio.create_task(conv.set_control("permission_mode", "plan"))
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "error", "request_id": ctrl["request_id"], "error": "nope"}})
    with pytest.raises(ControlRejected, match="nope"):
        await asyncio.wait_for(switch, 60)
    # the widget patched its value optimistically: the body re-emits the real one
    assert conv.permission_mode_observed.is_set()
    handle.stdout.eof()
    await reader


# --- launch flags ---------------------------------------------------------------

def test_flags_allow_switching_back_into_bypass():
    flags = build_claude_flags(permission_mode="acceptEdits", allowed_tools=None,
                               disallowed_tools=None, allow_bypass=True)
    assert "--allow-dangerously-skip-permissions" in flags
    assert "--allow-dangerously-skip-permissions" not in build_claude_flags(
        permission_mode="acceptEdits", allowed_tools=None, disallowed_tools=None)


# --- /control enforcement -----------------------------------------------------

class _ControlConversation(FakeConversation):
    def __init__(self, error=None):
        super().__init__()
        self.controls_set = []
        self._error = error

    async def set_control(self, cid, value):
        if self._error is not None:
            raise self._error
        self.controls_set.append((cid, value))


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"optio:pw").decode()}


async def _post_control(conv, body, allowed=None):
    lst = ConversationListener(conv, password="pw", allowed_controls=allowed)
    port = await lst.start("127.0.0.1")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{port}/control", json=body,
                              headers=_auth()) as r:
                return r.status, await r.json()
    finally:
        await lst.stop()


@pytest.mark.asyncio
async def test_control_endpoint_refuses_an_id_outside_the_allowlist():
    conv = _ControlConversation()
    status, body = await _post_control(conv, {"id": "model", "value": "x"},
                                       allowed=["permission_mode"])
    assert status == 403 and body["reason"] == "not-allowed"
    assert conv.controls_set == []


@pytest.mark.asyncio
async def test_control_endpoint_passes_an_allowed_id_through():
    conv = _ControlConversation()
    status, _ = await _post_control(conv, {"id": "permission_mode", "value": "plan"},
                                    allowed=["permission_mode"])
    assert status == 200 and conv.controls_set == [("permission_mode", "plan")]


@pytest.mark.asyncio
async def test_control_endpoint_reports_a_bad_value_and_a_refusal():
    status, body = await _post_control(_ControlConversation(ValueError("x")),
                                       {"id": "permission_mode", "value": "zzz"})
    assert status == 400 and body["reason"] == "bad-value"
    status, body = await _post_control(_ControlConversation(ControlRejected("nope")),
                                       {"id": "permission_mode", "value": "plan"})
    assert status == 409 and body["reason"] == "rejected"
