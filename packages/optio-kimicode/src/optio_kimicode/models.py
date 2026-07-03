"""Available-model catalog for the kimi conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM
============================================================================

Decision: **INLINE** (opencode-style), NOT restart (claudecode-style). kimi
changes model mid-conversation over ACP with a single JSON-RPC request — no
process relaunch, no --continue resume:

    --> {"method": "session/set_model",
         "params": {"sessionId": <sid>, "modelId": "kimi-k2-thinking"}}
    <-- {"result": {}}

KimiCodeConversation.request_model_change() fires session/set_model directly
(see conversation.py); the session body needs NO model_change_requested restart
loop (that is claudecode's mechanism).

------------------------------------------------------------------------
MODEL LIST source. **KIMI DELTA**: unlike grok/cursor's ``models`` block, kimi
advertises the picker as the ACP unified ``configOptions`` list (PLAN D11,
config-options.ts / model-catalog.ts in .kimi-src). The picker is the single
``model`` select option:

    result.configOptions = [
      {"type": "select", "id": "model", "name": "Model", "category": "model",
       "currentValue": "kimi-k2",
       "options": [
         {"value": "kimi-k2",          "name": "Kimi K2"},
         {"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"},
       ]},
      {"type": "select", "id": "thinking", ...},   # orthogonal — ignored here
      {"type": "select", "id": "mode",     ...},   # orthogonal — ignored here
    ]

KimiCodeConversation captures this list at bootstrap
(``session_config_options``), so the primary source is the live ACP session
(already authed, the exact ids ``session/set_model`` accepts). kimi model
values are ALIASES (``kimi-k2``, ``kimi-for-coding``, …), not raw provider ids.
A static alias list is the last resort so the picker never falsely empties.
"""
from __future__ import annotations

# Model ALIASES shown when the live ACP session advertises no configOptions
# model surface (a degenerate handshake). kimi values are aliases, not raw ids.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "kimi-k2", "label": "Kimi K2", "disabled": False},
        {"id": "kimi-k2-thinking", "label": "Kimi K2 Thinking", "disabled": False},
        {"id": "kimi-for-coding", "label": "Kimi for Coding", "disabled": False},
    ],
    "default": "kimi-k2",
}

# Ordered reasoning-effort levels kimi accepts via ``--effort`` at launch
# (mirrors types.Effort / _VALID_EFFORTS). Exposed as an ordered surface for
# the widget selector / consumers; effort is a launch flag, not a live switch.
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def parse_config_options(session_config_options: "list | None") -> dict:
    """Map the ACP ``configOptions`` list to the widget shape
    ``{models:[{id,label,disabled}], default}``.

    Locates the ``model`` select option and projects its ``options``
    (``[{value, name}]``) into picker rows; ``currentValue`` becomes the
    default. Missing / malformed input returns the static fallback (never
    falsely empties the picker)."""
    if not isinstance(session_config_options, list):
        return _copy_fallback()
    model_opt = None
    for opt in session_config_options:
        if isinstance(opt, dict) and opt.get("id") == "model":
            model_opt = opt
            break
    if model_opt is None:
        return _copy_fallback()
    options = model_opt.get("options")
    if not isinstance(options, list):
        return _copy_fallback()
    out: list[dict] = []
    for m in options:
        if not isinstance(m, dict):
            continue
        value = m.get("value")
        if isinstance(value, str) and value:
            out.append({"id": value, "label": m.get("name") or value, "disabled": False})
    if not out:
        return _copy_fallback()
    return {"models": out, "default": model_opt.get("currentValue")}


def available_models(session_config_options: "list | None") -> dict:
    """Best-effort model list for the picker. Never raises.

    Source precedence: the live ACP ``configOptions`` model option (preferred —
    authed, exact ids ``session/set_model`` accepts) → the static alias
    fallback."""
    if isinstance(session_config_options, list) and session_config_options:
        return parse_config_options(session_config_options)
    return _copy_fallback()


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
