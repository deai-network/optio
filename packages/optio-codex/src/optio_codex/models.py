"""Available-model list for the codex conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM — pinned from the app-server contract (codex-cli
0.142.5 schema dump + upstream README; see the conversation docstring).
============================================================================

Decision: **INLINE** (opencode/grok-style), NOT restart (claudecode-style) —
and codex needs NO dedicated set-model request at all: a ``model`` override
on ``turn/start`` "become[s] the default for subsequent turns" (README,
turn/start). So ``CodexConversation.set_control("model", …)`` just pins the
model sent with the next ``turn/start``; the session body needs no
model_change_requested restart loop.

MODEL LIST source: the ``model/list`` request, answered in-session
(``{data:[Model], nextCursor}``; ``Model`` carries ``id``, ``displayName``,
``hidden``, ``isDefault``). The conversation captures the raw result at
bootstrap; this module maps it to the widget shape. There is no CLI listing
tier (unlike grok) — live result → static fallback, nothing in between.
"""
from __future__ import annotations

from optio_agents.session_controls import (
    SessionControl,
    effort_control,
    model_control,
)

# Shown when the live model/list is unavailable (fake agents, offline, error
# response). Version-sensitive vendor strings; update alongside the pinned
# codex-cli version. ``efforts`` (per-model graded reasoning levels) is empty
# in the fallback — the effort control only appears once the live model/list
# advertises ``supportedReasoningEfforts``.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False,
         "efforts": [], "defaultEffort": None},
        {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False,
         "efforts": [], "defaultEffort": None},
    ],
    "default": "gpt-5.5",
}


def parse_model_list(result: "dict | None") -> dict:
    """Map a raw ``model/list`` result to the widget shape
    ``{models:[{id,label,disabled,efforts,defaultEffort}], default}``.

    Hidden models are omitted; ``default`` is the ``isDefault`` entry's id
    (None when absent). Each model also carries its graded reasoning capability
    read from the app-server ``Model`` schema: ``efforts`` =
    ``supportedReasoningEfforts`` (ordered; empty ⇒ no graded effort for that
    model) and ``defaultEffort`` = ``defaultReasoningEffort``. Missing /
    malformed input returns the static fallback (never raises, never falsely
    empties the picker).
    """
    if not isinstance(result, dict):
        return _copy_fallback()
    data = result.get("data")
    if not isinstance(data, list):
        return _copy_fallback()
    out: list[dict] = []
    default: str | None = None
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid or m.get("hidden"):
            continue
        efforts = m.get("supportedReasoningEfforts")
        out.append({
            "id": mid,
            "label": m.get("displayName") or mid,
            "disabled": False,
            "efforts": [e for e in efforts if isinstance(e, str)]
            if isinstance(efforts, list) else [],
            "defaultEffort": m.get("defaultReasoningEffort"),
        })
        if m.get("isDefault"):
            default = mid
    if not out:
        return _copy_fallback()
    return {"models": out, "default": default}


def effort_for_model(
    model_list: dict, model_id: "str | None"
) -> "tuple[list[str], str | None]":
    """Return ``(levels, default)`` graded-reasoning capability for
    ``model_id`` from a ``parse_model_list`` result. ``([], None)`` when the
    model is unknown or advertises no ``supportedReasoningEfforts``."""
    for m in model_list.get("models", []):
        if m.get("id") == model_id:
            return list(m.get("efforts") or []), m.get("defaultEffort")
    return [], None


def build_controls(
    model_list: dict,
    current_model: "str | None",
    requested_effort: "str | None",
) -> "list[SessionControl]":
    """Build codex's session-control set for the current model: the
    ``id="model"`` picker plus, WHEN the current model supports graded
    reasoning, the ``id="reasoning_effort"`` slider. An unsupported model
    yields no effort control (Spec-B model-dependent presence). The slider's
    current value is the requested effort when it is valid for the model, else
    the model's default. Re-derived on every model change so effort presence /
    levels follow the model."""
    controls: list[SessionControl] = [
        model_control(models=model_list["models"], current=current_model),
    ]
    levels, default = effort_for_model(model_list, current_model)
    if levels:
        current = requested_effort if requested_effort in levels else default
        controls.append(effort_control(levels=levels, current=current))
    return controls


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
