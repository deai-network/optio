"""Available-model list for the grok conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM — pinned by a LIVE PROBE of the real `grok agent stdio`
(grok 0.2.81). See docs Stage 7 Task 0.
============================================================================

Decision: **INLINE** (opencode-style), NOT restart (claudecode-style).

Grok changes model mid-conversation over ACP with a single JSON-RPC request —
no process relaunch, no --continue resume:

    --> {"method": "session/set_model",
         "params": {"sessionId": <sid>, "modelId": "grok-build"}}
    <-- {"result": {"_meta": {"model": {"Ok": "grok-build-0.1"}}}}

Evidence (probe transcript):
  * ``session/set_model`` {sessionId, modelId} -> result._meta.model.Ok.
  * ``session/setModel`` (camelCase)           -> -32601 Method not found.
  * param must be ``modelId`` (not ``model``)  -> -32602 "missing field modelId".
  * ``x.ai/set_model`` / ``session/select_model`` -> -32601.

So GrokConversation.set_control("model", …) fires session/set_model directly
(see conversation.py); the session body needs NO model_change_requested
restart loop (that is claudecode's mechanism).

------------------------------------------------------------------------
MODEL LIST source. Both the ``initialize`` and ``session/new`` responses carry
a model block:

    result.models = {
      "currentModelId": "grok-composer-2.5-fast",
      "availableModels": [
        {"modelId": "grok-composer-2.5-fast", "name": "Composer 2.5", ...},
        {"modelId": "grok-build",             "name": "Grok Build",  ...},
      ],
    }

GrokConversation captures this at bootstrap, so the primary source is the live
ACP session (already authed, exact ids that set_model accepts). The `grok
models` CLI text is a secondary source, and a static list is the last resort.
"""
from __future__ import annotations

import logging
import re
import shlex

_LOG = logging.getLogger(__name__)

# Common ids shown when neither the ACP session nor `grok models` yields a list.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "grok-composer-2.5-fast", "label": "Composer 2.5", "disabled": False},
        {"id": "grok-build", "label": "Grok Build", "disabled": False},
    ],
    "default": "grok-composer-2.5-fast",
}


def parse_acp_models(session_models: "dict | None") -> dict:
    """Map an ACP ``models`` block ({currentModelId, availableModels:[{modelId,
    name, _meta}]}) to the widget shape {models:[{id,label,disabled}], default}.

    NO live reasoning-effort capability is derived here. grok's ACP per-model
    ``_meta`` block carries ONLY {totalContextTokens, agentType} (verified
    against a live authed ``grok agent stdio`` session) — it advertises no
    ``supportsReasoningEffort`` / ``reasoningEfforts`` in any casing, so there
    is no reachable per-model reasoning-capability source and no live effort
    slider is surfaced. (Real per-model capability lives only in
    ``~/.grok/models_cache.json`` as snake_case ``supports_reasoning_effort``,
    which is currently false for every model on the account.) ``reasoning_effort``
    remains a LAUNCH-ONLY knob applied via ``--reasoning-effort`` (see
    types.GrokTaskConfig / host_actions.build_conversation_argv).

    Missing / malformed input returns the static fallback (never falsely
    empties the picker)."""
    if not isinstance(session_models, dict):
        return _copy_fallback()
    avail = session_models.get("availableModels")
    if not isinstance(avail, list):
        return _copy_fallback()
    out: list[dict] = []
    for m in avail:
        if not isinstance(m, dict):
            continue
        mid = m.get("modelId")
        if isinstance(mid, str) and mid:
            out.append({"id": mid, "label": m.get("name") or mid, "disabled": False})
    if not out:
        return _copy_fallback()
    return {"models": out, "default": session_models.get("currentModelId")}


def parse_grok_models_text(text: str) -> dict:
    """Parse the ``grok models`` CLI output to the widget shape.

    Example output::

        Default model: grok-composer-2.5-fast

        Available models:
          * grok-composer-2.5-fast (default)
          - grok-build
    """
    default: str | None = None
    models: list[dict] = []
    in_list = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = re.match(r"Default model:\s*(\S+)", line)
        if m:
            default = m.group(1)
            continue
        if line.lower().startswith("available models"):
            in_list = True
            continue
        if in_list:
            m2 = re.match(r"[*-]\s+(\S+)", line)
            if m2:
                mid = m2.group(1)
                models.append({"id": mid, "label": mid, "disabled": False})
    return {"models": models, "default": default}


async def fetch_available_models(
    session_models: "dict | None" = None,
    *,
    host=None,
    grok_path: "str | None" = None,
) -> dict:
    """Best-effort model list for the picker. Never raises.

    Source precedence: the live ACP session block (preferred — authed, exact
    ids) → the ``grok models`` CLI on the host → the static fallback list.
    """
    if isinstance(session_models, dict) and session_models.get("availableModels"):
        return parse_acp_models(session_models)
    if host is not None and grok_path:
        try:
            result = await host.run_command(f"{shlex.quote(grok_path)} models")
            if getattr(result, "exit_code", 1) == 0:
                parsed = parse_grok_models_text(getattr(result, "stdout", "") or "")
                if parsed["models"]:
                    return parsed
        except Exception:  # noqa: BLE001 — best-effort; fall through to fallback
            _LOG.info("grok model list: `grok models` failed; using fallback", exc_info=True)
    return _copy_fallback()


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
