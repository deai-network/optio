"""Unit tests for conversation-mode reasoning-effort control (Spec B, T6).

File-disjoint units that need no live claude: config validation, the per-model
effort levels (from the CLI's own model list), the `--effort` argv flag, the
conversation's live set_control, and the synthetic control re-emit. The live
switch end to end is exercised in test_conversation_ui_session.py.
"""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_claudecode.models import (
    EFFORT_LEVELS,
    DEFAULT_EFFORT,
    model_effort,
    parse_cli_models,
)
from optio_claudecode.host_actions import build_claude_flags

from .fake_claude import CLI_MODELS


def _cfg(**kw):
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


_ALL = ["low", "medium", "high", "xhigh", "max"]

# The CLI's own list (initialize, real 2.1.268 shape): default / opus[1m] /
# sonnet list all five levels, haiku none.
CATALOG = parse_cli_models(CLI_MODELS)


# --- A. config field + validation ---------------------------------------


def test_reasoning_effort_defaults_none():
    assert _cfg().reasoning_effort is None


@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_reasoning_effort_accepts_valid_levels(level):
    assert _cfg(reasoning_effort=level).reasoning_effort == level


def test_reasoning_effort_rejects_unknown_level():
    with pytest.raises(ValueError, match="reasoning_effort"):
        _cfg(reasoning_effort="turbo")


# --- B. per-model effort levels (the CLI's supportedEffortLevels) ------------


def test_model_effort_reads_the_cli_levels():
    levels, default = model_effort("opus[1m]", CATALOG)
    assert levels == _ALL
    assert levels is not CATALOG[1]["effort"]          # fresh copy, not the stored list
    assert default == DEFAULT_EFFORT


def test_model_effort_preselects_the_highest_when_high_is_not_listed():
    catalog = [{"id": "m", "label": "M", "effort": ["low", "medium"]}]
    assert model_effort("m", catalog) == (["low", "medium"], "medium")


def test_model_effort_none_for_a_model_without_levels():
    assert model_effort("haiku", CATALOG) == (None, None)


def test_model_effort_none_for_a_model_not_listed():
    assert model_effort("claude-nonesuch-9", CATALOG) == (None, None)


def test_model_effort_takes_the_alias_as_is():
    # "opus[1m]" is an alias of its own, not "opus" with a variant suffix
    assert model_effort("opus", CATALOG) == (None, None)


# --- effort flag applied at (re)launch ----------------------------------


def test_build_claude_flags_emits_effort():
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
        model="opus[1m]", effort="high",
    )
    assert "--effort" in flags
    assert flags[flags.index("--effort") + 1] == "high"


def test_build_claude_flags_omits_effort_when_none():
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert "--effort" not in flags


# --- control presence follows the model (session build_controls logic) ---


def _build_controls(catalog, model, effort):
    """The session's controls snapshot (controls.build_controls) without a
    permission mode: the model select and the effort slider's presence gate."""
    from optio_claudecode.controls import build_controls
    return build_controls(catalog=catalog, model=model, effort=effort,
                          permission_mode=None, permission_options=[])


def test_controls_include_effort_for_capable_model():
    ctrls = _build_controls(CATALOG, model="opus[1m]", effort=None)
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["kind"] == "slider"
    assert effort_ctrl["levels"] == _ALL
    assert effort_ctrl["value"] == DEFAULT_EFFORT          # default preselected


def test_controls_omit_effort_for_incapable_model():
    ctrls = _build_controls(CATALOG, model="haiku", effort=None)
    assert not any(c["id"] == "reasoning_effort" for c in ctrls)


def test_controls_reflect_configured_effort_value():
    ctrls = _build_controls(CATALOG, model="opus[1m]", effort="max")
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["value"] == "max"


# --- C. the runtime model system/init reports (see test_cli_models) ------


@pytest.mark.asyncio
async def test_system_init_captures_runtime_model():
    # A real system/init line for a default-model session: the running model is
    # named (base id + [variant] suffix) even though no --model was passed.
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    conv._route({
        "type": "system", "subtype": "init", "session_id": "fake-session-0000",
        "model": "claude-opus-4-8[1m]", "cwd": "/w",
    })
    assert conv.runtime_model == "claude-opus-4-8[1m]"
    assert conv.runtime_model_observed.is_set()


# --- D. set_control switches live (test_live_switch.py has the rest) -----


@pytest.mark.asyncio
async def test_set_control_effort_switches_live_keeping_the_model():
    import asyncio
    from optio_claudecode.conversation import ClaudeCodeConversation
    from .test_conversation_driver import _FakeHandle
    from .test_live_switch import _answer_readback
    handle = _FakeHandle()
    conv = ClaudeCodeConversation()
    conv.attach(handle)
    conv.models_offered = CATALOG
    conv.model, conv.effort = "sonnet", "medium"
    reader = asyncio.create_task(conv.run_reader())
    switch = asyncio.create_task(conv.set_control("reasoning_effort", "high"))
    req = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    # an effort change carries the running model along, unchanged
    assert req["request"] == {"subtype": "apply_flag_settings",
                              "settings": {"model": "sonnet", "effortLevel": "high"}}
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": req["request_id"]}})
    await _answer_readback(handle)              # then claude is asked what it runs
    await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("sonnet", "high")
    handle.stdout.eof()
    await reader


# --- E. control re-emit after a switch -----------------------------------


@pytest.mark.asyncio
async def test_emit_control_update_fans_out_full_snapshot():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    snapshot = [{"id": "reasoning_effort", "kind": "slider", "value": "high"}]
    conv.emit_control_update(snapshot)
    ev = conv._event_queue.get_nowait()
    assert ev == {"type": "x-optio-control-update", "controls": snapshot}
