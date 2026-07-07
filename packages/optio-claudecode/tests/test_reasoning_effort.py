"""Unit tests for conversation-mode reasoning-effort control (Spec B, T6).

File-disjoint units that need no live claude: config validation, the per-model
graded-effort capability table, the `--effort` argv flag, the conversation
set_control / restart-signal wiring, and the synthetic control re-emit. The full
restart loop (effort_task arm) is exercised manually / in T7.
"""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_claudecode.models import (
    EFFORT_LEVELS,
    DEFAULT_EFFORT,
    model_effort,
)
from optio_claudecode.host_actions import build_claude_flags
from optio_agents.session_controls import effort_control, model_control


def _cfg(**kw):
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


# --- A. config field + validation ---------------------------------------


def test_reasoning_effort_defaults_none():
    assert _cfg().reasoning_effort is None


@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_reasoning_effort_accepts_valid_levels(level):
    assert _cfg(reasoning_effort=level).reasoning_effort == level


def test_reasoning_effort_rejects_unknown_level():
    with pytest.raises(ValueError, match="reasoning_effort"):
        _cfg(reasoning_effort="turbo")


# --- B. per-model graded-effort capability ------------------------------


@pytest.mark.parametrize("mid", ["claude-opus-4-8", "claude-sonnet-4-6"])
def test_model_effort_capable_families(mid):
    levels, default = model_effort(mid)
    assert levels == EFFORT_LEVELS
    assert levels is not EFFORT_LEVELS          # fresh copy, not the module list
    assert default == DEFAULT_EFFORT


def test_model_effort_haiku_has_no_graded_effort():
    assert model_effort("claude-haiku-4-5") == (None, None)


def test_model_effort_ignores_runtime_variant_suffix():
    # The stream reports runtime/variant ids (e.g. claude-opus-4-8[1m]); the
    # suffix must not defeat the family lookup.
    levels, default = model_effort("claude-opus-4-8[1m]")
    assert levels == EFFORT_LEVELS and default == DEFAULT_EFFORT


# --- effort flag applied at (re)launch ----------------------------------


def test_build_claude_flags_emits_effort():
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
        model="claude-opus-4-8", effort="high",
    )
    assert "--effort" in flags
    assert flags[flags.index("--effort") + 1] == "high"


def test_build_claude_flags_omits_effort_when_none():
    flags = build_claude_flags(
        permission_mode=None, allowed_tools=None, disallowed_tools=None,
    )
    assert "--effort" not in flags


# --- control presence follows the model (session build_controls logic) ---


def _build_controls(models, model, effort):
    """Mirror of session.py's build_controls closure so the presence gate is
    unit-testable without a live host."""
    ctrls = [model_control(models=models, current=model)]
    levels, default = model_effort(model) if model else (None, None)
    if levels:
        ctrls.append(effort_control(levels=levels, current=effort or default))
    return [c.to_dict() for c in ctrls]


def test_controls_include_effort_for_capable_model():
    ctrls = _build_controls(
        [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}],
        model="claude-opus-4-8", effort=None,
    )
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["kind"] == "slider"
    assert effort_ctrl["levels"] == EFFORT_LEVELS
    assert effort_ctrl["value"] == DEFAULT_EFFORT          # default preselected


def test_controls_omit_effort_for_incapable_model():
    ctrls = _build_controls(
        [{"id": "claude-haiku-4-5"}], model="claude-haiku-4-5", effort=None,
    )
    assert not any(c["id"] == "reasoning_effort" for c in ctrls)


def test_controls_omit_effort_when_model_unknown():
    # current model None (no --model): can't derive capability server-side.
    ctrls = _build_controls([{"id": "claude-opus-4-8"}], model=None, effort=None)
    assert not any(c["id"] == "reasoning_effort" for c in ctrls)


def test_controls_reflect_configured_effort_value():
    ctrls = _build_controls(
        [{"id": "claude-opus-4-8"}], model="claude-opus-4-8", effort="max",
    )
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["value"] == "max"


# --- D. set_control routes to the restart signal ------------------------


@pytest.mark.asyncio
async def test_set_control_effort_fires_restart_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("reasoning_effort", "high")
    assert conv.requested_effort == "high"
    assert conv.effort_change_requested.is_set()
    # an effort change must NOT masquerade as a model change
    assert conv.requested_model is None
    assert not conv.model_change_requested.is_set()


@pytest.mark.asyncio
async def test_set_control_model_leaves_effort_untouched():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("model", "claude-opus-4-8")
    assert conv.requested_model == "claude-opus-4-8"
    assert conv.model_change_requested.is_set()
    assert conv.requested_effort is None
    assert not conv.effort_change_requested.is_set()


# --- E. control re-emit on relaunch -------------------------------------


@pytest.mark.asyncio
async def test_emit_control_update_fans_out_full_snapshot():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    snapshot = [{"id": "reasoning_effort", "kind": "slider", "value": "high"}]
    conv.emit_control_update(snapshot)
    ev = conv._event_queue.get_nowait()
    assert ev == {"type": "x-optio-control-update", "controls": snapshot}
