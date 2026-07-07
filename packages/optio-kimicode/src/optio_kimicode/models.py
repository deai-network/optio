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


def parse_all_controls(session_config_options, default_model=None, default_effort=None):
    """Project kimi's ACP ``configOptions`` surface into ``SessionControl[]``.

    kimi advertises its live pickers as a unified ``configOptions`` list
    (PLAN D11, kimi-code fork ``packages/acp-adapter/src/config-options.ts``),
    NOT grok/cursor's ``models`` block. Each option is projected by its id:

      * ``model``    -> ``select`` (category ``model``) — the model picker.
      * ``thinking`` -> ``slider`` (id ``reasoning_effort``, category
        ``thought_level``) — the GRADED reasoning-effort control. **Requires
        the fork ``kimi-code >= 0.23.1-csillag.2`` / ``csillag/acp-graded-thinking``**,
        which upgrades the former 2-entry ``off``/``on`` thinking toggle into an
        ordered effort list. Two wire shapes:
          - ``options = [off, <graded…>]`` (``off`` present) → an ENABLED slider
            whose ordered levels ARE the option values, so the chosen level
            round-trips unchanged to ``session/set_config_option`` (configId
            ``thinking``; see conversation.py, which maps the ``reasoning_effort``
            control id back to configId ``thinking``).
          - ``options`` WITHOUT ``off`` → an always-thinking model (the runtime
            cannot disable reasoning) → a DISABLED slider + ``ALWAYS_THINKING_REASON``.
        Note the projected control ``id`` is ``reasoning_effort`` while the ACP
        ``configId`` stays ``thinking`` — the two are bridged in set_control.
      * ``mode``     -> ``select`` (category ``mode``) — the 4-mode taxonomy.

    Unknown option ids fall back to a generic ``boolean``/``select`` by their
    ACP ``type``. The ``default_model`` argument (fed from ``config.model``)
    overrides the model control's initial value; ``default_effort`` (fed from
    ``config.reasoning_effort``) overrides the reasoning-effort slider's initial
    value — otherwise each control's live ``currentValue`` is shown.
    Missing/malformed input yields an empty list.
    """
    from optio_agents.session_controls import (
        SINGLE_OPTION_REASON,
        ControlOption,
        SessionControl,
        effort_control,
    )

    # An always-thinking model advertises no 'off' level (the runtime cannot
    # disable reasoning); surface that as a disabled control + reason.
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
            # Graded reasoning-effort slider (fork >= 0.23.1-csillag.2). The
            # ordered option values ARE the slider levels, so the chosen level
            # round-trips 1:1 to configId 'thinking'. A model that can disable
            # reasoning advertises an 'off' level -> enabled slider; an
            # always-thinking model omits 'off' -> disabled + reason.
            levels = [o.value for o in options]
            always_thinking = "off" not in levels
            controls.append(effort_control(
                levels=levels,
                current=(default_effort or cur),
                disabled=always_thinking,
                why_disabled=ALWAYS_THINKING_REASON if always_thinking else None,
                label="Thinking",
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
