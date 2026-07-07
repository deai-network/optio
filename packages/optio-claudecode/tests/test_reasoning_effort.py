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
    parse_models,
    _effort_tiers,
)
from optio_claudecode.host_actions import build_claude_flags
from optio_agents.session_controls import effort_control, model_control


def _cfg(**kw):
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


# --- REAL GET /v1/models capabilities.effort fixtures -------------------
#
# The exact per-model shape GET /v1/models returns under
# ``capabilities.effort``. A verbatim captured claude-opus-4-8 entry:
#   {"supported": true,
#    "low":  {"supported": true}, "medium": {"supported": true},
#    "high": {"supported": true}, "xhigh":  {"supported": true},
#    "max":  {"supported": true}}
# A tier with ``"supported": false`` is unavailable (sonnet-4-6 lacks xhigh);
# a model whose top-level ``effort.supported`` is false has no graded effort
# at all (haiku-4-5). These fixtures reproduce that wire shape exactly.

_ALL = ("low", "medium", "high", "xhigh", "max")
_NO_XHIGH = ("low", "medium", "high", "max")


def _effort_cap(*supported_tiers: str) -> dict:
    """capabilities.effort in the real wire shape: a top-level ``supported``
    flag plus a per-tier ``{supported: bool}`` entry for every EFFORT_LEVELS
    tier (the tiers listed are supported, the rest carry supported: false)."""
    cap: dict = {"supported": bool(supported_tiers)}
    for lvl in EFFORT_LEVELS:
        cap[lvl] = {"supported": lvl in supported_tiers}
    return cap


# The real GET /v1/models `data` array (ids + per-model capabilities.effort).
# Supported tiers per the real 2026-07 response:
#   opus-4-8 / sonnet-5 / fable-5 / opus-4-7 -> all five tiers
#   sonnet-4-6 / opus-4-6                    -> no xhigh
#   haiku-4-5 / sonnet-4-5 / opus-4-1        -> effort.supported: false
REAL_MODELS_API = {"data": [
    {"id": "claude-opus-4-8",   "display_name": "Claude Opus 4.8",   "capabilities": {"effort": _effort_cap(*_ALL)}},
    {"id": "claude-opus-4-7",   "display_name": "Claude Opus 4.7",   "capabilities": {"effort": _effort_cap(*_ALL)}},
    {"id": "claude-opus-4-6",   "display_name": "Claude Opus 4.6",   "capabilities": {"effort": _effort_cap(*_NO_XHIGH)}},
    {"id": "claude-opus-4-1",   "display_name": "Claude Opus 4.1",   "capabilities": {"effort": _effort_cap()}},
    {"id": "claude-sonnet-5",   "display_name": "Claude Sonnet 5",   "capabilities": {"effort": _effort_cap(*_ALL)}},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "capabilities": {"effort": _effort_cap(*_NO_XHIGH)}},
    {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5", "capabilities": {"effort": _effort_cap()}},
    {"id": "claude-fable-5",    "display_name": "Claude Fable 5",    "capabilities": {"effort": _effort_cap(*_ALL)}},
    {"id": "claude-haiku-4-5",  "display_name": "Claude Haiku 4.5",  "capabilities": {"effort": _effort_cap()}},
]}

# Decluttered catalog (latest per family) carrying captured per-model effort.
REAL_CATALOG = parse_models(REAL_MODELS_API)["models"]


# --- A. config field + validation ---------------------------------------


def test_reasoning_effort_defaults_none():
    assert _cfg().reasoning_effort is None


@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_reasoning_effort_accepts_valid_levels(level):
    assert _cfg(reasoning_effort=level).reasoning_effort == level


def test_reasoning_effort_rejects_unknown_level():
    with pytest.raises(ValueError, match="reasoning_effort"):
        _cfg(reasoning_effort="turbo")


# --- B. per-model graded-effort capability (REAL /v1/models shape) --------


def test_effort_tiers_from_literal_real_capability():
    # Verbatim capabilities.effort blocks as GET /v1/models returns them.
    opus = {"supported": True,
            "low": {"supported": True}, "medium": {"supported": True},
            "high": {"supported": True}, "xhigh": {"supported": True},
            "max": {"supported": True}}
    assert _effort_tiers({"effort": opus}) == ["low", "medium", "high", "xhigh", "max"]
    # sonnet-4-6: xhigh unsupported -> dropped, order preserved.
    sonnet46 = {"supported": True,
                "low": {"supported": True}, "medium": {"supported": True},
                "high": {"supported": True}, "xhigh": {"supported": False},
                "max": {"supported": True}}
    assert _effort_tiers({"effort": sonnet46}) == ["low", "medium", "high", "max"]
    # haiku-4-5: effort unsupported at the top -> no graded effort.
    assert _effort_tiers({"effort": {"supported": False}}) is None
    # capabilities without an effort block at all -> no graded effort.
    assert _effort_tiers({}) is None


def test_parse_models_captures_effort_tiers_per_model():
    by_id = {m["id"]: m for m in REAL_CATALOG}
    # declutter keeps the latest per family (opus-4-8, sonnet-5, fable-5, haiku-4-5).
    assert set(by_id) == {
        "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5", "claude-haiku-4-5",
    }
    assert by_id["claude-opus-4-8"]["effort"] == list(_ALL)
    assert by_id["claude-sonnet-5"]["effort"] == list(_ALL)
    assert by_id["claude-fable-5"]["effort"] == list(_ALL)
    # haiku: effort.supported false -> no effort key attached at all.
    assert "effort" not in by_id["claude-haiku-4-5"]


def test_parse_models_drops_unsupported_tier():
    # A family whose latest model advertises no xhigh keeps only supported tiers.
    catalog = parse_models({"data": [
        {"id": "claude-opus-4-6", "capabilities": {"effort": _effort_cap(*_NO_XHIGH)}},
    ]})["models"]
    assert catalog[0]["effort"] == ["low", "medium", "high", "max"]


def test_model_effort_reads_catalog_all_tiers():
    levels, default = model_effort("claude-opus-4-8", REAL_CATALOG)
    assert levels == list(_ALL)
    assert levels is not REAL_CATALOG[0]["effort"]     # fresh copy, not the stored list
    assert default == DEFAULT_EFFORT


def test_model_effort_no_xhigh_from_catalog():
    catalog = parse_models({"data": [
        {"id": "claude-sonnet-4-6", "capabilities": {"effort": _effort_cap(*_NO_XHIGH)}},
    ]})["models"]
    levels, default = model_effort("claude-sonnet-4-6", catalog)
    assert levels == ["low", "medium", "high", "max"]
    assert default == DEFAULT_EFFORT                   # high still supported


def test_model_effort_incapable_model_from_catalog():
    assert model_effort("claude-haiku-4-5", REAL_CATALOG) == (None, None)


def test_model_effort_model_absent_from_catalog():
    # A model not present in the fetched catalog has no known capability.
    assert model_effort("claude-nonesuch-9", REAL_CATALOG) == (None, None)


def test_model_effort_strips_runtime_variant_suffix():
    # The stream reports runtime/variant ids (real system/init: claude-opus-4-8[1m]);
    # the [..] suffix must be stripped before the catalog lookup.
    levels, default = model_effort("claude-opus-4-8[1m]", REAL_CATALOG)
    assert levels == list(_ALL)
    assert default == DEFAULT_EFFORT


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


def _build_controls(catalog, model, effort):
    """Mirror of session.py's build_controls closure so the presence gate is
    unit-testable without a live host — reads per-model effort from the catalog."""
    ctrls = [model_control(models=catalog, current=model)]
    levels, default = model_effort(model, catalog) if model else (None, None)
    if levels:
        ctrls.append(effort_control(levels=levels, current=effort or default))
    return [c.to_dict() for c in ctrls]


def test_controls_include_effort_for_capable_model():
    ctrls = _build_controls(REAL_CATALOG, model="claude-opus-4-8", effort=None)
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["kind"] == "slider"
    assert effort_ctrl["levels"] == list(_ALL)
    assert effort_ctrl["value"] == DEFAULT_EFFORT          # default preselected


def test_controls_omit_effort_for_incapable_model():
    ctrls = _build_controls(REAL_CATALOG, model="claude-haiku-4-5", effort=None)
    assert not any(c["id"] == "reasoning_effort" for c in ctrls)


def test_controls_omit_effort_when_model_unknown():
    # current model None (no --model): can't derive capability server-side.
    ctrls = _build_controls(REAL_CATALOG, model=None, effort=None)
    assert not any(c["id"] == "reasoning_effort" for c in ctrls)


def test_controls_reflect_configured_effort_value():
    ctrls = _build_controls(REAL_CATALOG, model="claude-opus-4-8", effort="max")
    effort_ctrl = next(c for c in ctrls if c["id"] == "reasoning_effort")
    assert effort_ctrl["value"] == "max"


# --- C. runtime model from system/init drives effort presence -----------


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


def _adopt_runtime_model(catalog, runtime_model, current_model, current_effort):
    """Mirror session.py's system/init arm: strip the [..] variant suffix,
    adopt the base id, and rebuild controls when it differs from the current
    model. Returns (new_current_model, rebuilt_controls_or_None)."""
    base = (runtime_model or "").split("[", 1)[0]
    if base and base != current_model:
        return base, _build_controls(catalog, base, current_effort)
    return current_model, None


def test_runtime_model_makes_effort_slider_appear_for_default_session():
    # Default-model session: config.model is None, so the INITIAL controls carry
    # no effort slider. The real system/init model "claude-opus-4-8[1m]" is
    # folded in -> the reasoning_effort slider appears for the actual model.
    initial = _build_controls(REAL_CATALOG, model=None, effort=None)
    assert not any(c["id"] == "reasoning_effort" for c in initial)

    model, controls = _adopt_runtime_model(
        REAL_CATALOG, "claude-opus-4-8[1m]", current_model=None, current_effort=None,
    )
    assert model == "claude-opus-4-8"                       # suffix stripped
    effort_ctrl = next(c for c in controls if c["id"] == "reasoning_effort")
    assert effort_ctrl["kind"] == "slider"
    assert effort_ctrl["levels"] == list(_ALL)
    assert effort_ctrl["value"] == DEFAULT_EFFORT
    # the model select now reflects the real running model too.
    model_ctrl = next(c for c in controls if c["id"] == "model")
    assert model_ctrl["value"] == "claude-opus-4-8"


def test_runtime_model_matching_current_model_needs_no_re_emit():
    # When the runtime model equals what we already track (an explicit --model
    # session), the arm is a no-op: no redundant control re-emit.
    model, controls = _adopt_runtime_model(
        REAL_CATALOG, "claude-opus-4-8[1m]",
        current_model="claude-opus-4-8", current_effort="high",
    )
    assert model == "claude-opus-4-8"
    assert controls is None


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
