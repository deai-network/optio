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

KimiCodeConversation.set_control("model", <id>) fires session/set_model directly
(see conversation.py); the session body needs NO model_change_requested restart
loop (that is claudecode's mechanism). ``set_control`` also carries kimi's
thinking + mode controls via ``session/set_config_option`` (see
``parse_all_controls`` below and conversation.py).

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


def parse_all_controls(session_config_options, default_model=None):
    """Project kimi's ACP ``configOptions`` surface into ``SessionControl[]``.

    kimi advertises its live pickers as a unified ``configOptions`` list
    (PLAN D11, kimi-code fork ``packages/acp-adapter/src/config-options.ts``),
    NOT grok/cursor's ``models`` block. Each option is projected by its id:

      * ``model``    -> ``select``    (category ``model``) — the model picker.
      * ``thinking`` -> ``segmented`` (category ``thought_level``) — a 2-entry
        ``off`` / ``on`` toggle. **VERIFIED against the fork**
        (``config-options.ts:buildThinkingOption``): the wire shape is a
        2-entry SELECT (``off``/``on``), NOT a graded effort list, and the
        option is present only when the current model is ``thinkingSupported``.
        Rendered as a 2-level ``segmented`` so its string value round-trips
        unchanged to ``session/set_config_option`` (the server compares
        ``value === 'on'``).
      * ``mode``     -> ``select``    (category ``mode``) — the 4-mode taxonomy.

    Unknown option ids fall back to a generic ``boolean``/``select`` by their
    ACP ``type``. The ``default_model`` argument (fed from ``config.model``)
    overrides the model control's initial value; otherwise the live
    ``currentValue`` is shown. Missing/malformed input yields an empty list.
    """
    from optio_agents.session_controls import (
        SINGLE_OPTION_REASON,
        ControlOption,
        SessionControl,
    )

    # An always-thinking model advertises thinking as a single 'on' option (the
    # runtime cannot disable it); surface that as a disabled control + reason.
    ALWAYS_THINKING_REASON = "This model always thinks; thinking can't be turned off."

    controls: list = []
    for opt in (session_config_options or []):
        if not isinstance(opt, dict):
            continue
        oid = opt.get("id")
        options = [
            ControlOption(
                value=o.get("value"),
                label=o.get("name", o.get("value")),
                description=o.get("description"),
            )
            for o in (opt.get("options") or [])
            if isinstance(o, dict)
        ]
        cur = opt.get("currentValue")
        locked = len(options) <= 1  # nothing to switch to -> unchangeable
        if oid == "model":
            controls.append(SessionControl(
                id="model", kind="select", label="Model", category="model",
                value=(default_model or cur or ""), options=options,
                disabled=locked,
                why_disabled=SINGLE_OPTION_REASON if locked else None,
            ))
        elif oid == "thinking":
            # off/on wire (see docstring) -> a 2-level segmented; the levels ARE
            # the option values so the segmented value maps 1:1 to configId's
            # accepted string. An always-thinking model collapses this to a
            # single 'on' -> disabled with a thinking-specific reason.
            levels = [o.value for o in options]
            controls.append(SessionControl(
                id="thinking", kind="segmented", label="Thinking",
                category="thought_level",
                value=(cur or (levels[0] if levels else "")),
                levels=levels,
                disabled=locked,
                why_disabled=ALWAYS_THINKING_REASON if locked else None,
            ))
        elif oid == "mode":
            controls.append(SessionControl(
                id="mode", kind="select", label="Mode", category="mode",
                value=(cur or ""), options=options,
                disabled=locked,
                why_disabled=SINGLE_OPTION_REASON if locked else None,
            ))
        elif opt.get("type") == "boolean":
            controls.append(SessionControl(
                id=oid or "", kind="boolean", label=(oid or "").title(),
                value=bool(cur),
            ))
        else:
            controls.append(SessionControl(
                id=oid or "", kind="select", label=(oid or "").title(),
                value=(cur or ""), options=options,
                disabled=locked,
                why_disabled=SINGLE_OPTION_REASON if locked else None,
            ))
    return controls
