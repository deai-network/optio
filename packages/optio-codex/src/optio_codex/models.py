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

# Shown when the live model/list is unavailable (fake agents, offline, error
# response). Version-sensitive vendor strings; update alongside the pinned
# codex-cli version.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "gpt-5.5", "label": "GPT-5.5", "disabled": False},
        {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "disabled": False},
    ],
    "default": "gpt-5.5",
}


def parse_model_list(result: "dict | None") -> dict:
    """Map a raw ``model/list`` result to the widget shape
    ``{models:[{id,label,disabled}], default}``.

    Hidden models are omitted; ``default`` is the ``isDefault`` entry's id
    (None when absent). Missing / malformed input returns the static
    fallback (never raises, never falsely empties the picker).
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
        out.append({"id": mid, "label": m.get("displayName") or mid, "disabled": False})
        if m.get("isDefault"):
            default = mid
    if not out:
        return _copy_fallback()
    return {"models": out, "default": default}


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
