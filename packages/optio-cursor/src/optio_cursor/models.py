"""Available-model list for the cursor conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM — Stage 7 Task 0 record.

Working assumption: **INLINE** via ACP ``session/set_model`` — grok's exact
mechanism, LIVE-pinned there (grok 0.2.81, see optio_grok/models.py):

    --> {"method": "session/set_model",
         "params": {"sessionId": <sid>, "modelId": <id>}}

Provenance for cursor: [grok-pinned, cursor runtime-unverified].
  * ``session/set_model`` is present in the cursor binary [cursor-verified,
    unauthenticated handshake probe — design doc §"Verified handshake"].
  * A live authed probe was NOT possible: host ``cursor-agent status`` =
    "Not logged in" (probed 2026-07-02, cursor-agent 2026.07.01-41b2de7).
    Runtime confirmation deferred to the demo stage (design doc §7 item 4).

FALLBACK if the live probe ever disproves inline switching: restart-based
swap, claudecode-style — relaunch ``cursor-agent --resume <chatId>
--model <m>`` (both ``--resume`` and ``create-chat`` exist in the binary),
driven by a model_change_requested restart loop in the session body.

So CursorConversation.set_control("model", ...) fires session/set_model
directly (see conversation.py); the session body needs NO restart loop under
the working assumption.

------------------------------------------------------------------------
MODEL LIST source precedence (mirrors grok):
  1. The ACP ``session/new`` response's ``models`` block
     ({currentModelId, availableModels:[{modelId, name}]}) captured by
     CursorConversation at bootstrap — authed, exact ids that set_model
     accepts. [grok-pinned, cursor runtime-unverified]
  2. The ``cursor-agent models`` CLI (also ``--list-models``). AUTH-GATED:
     on a logged-out host it prints "Error: Authentication required." —
     output format runtime-unverified, parsed leniently.
  3. A static fallback list (current cursor catalogue: composer / gpt /
     sonnet / opus families, per `cursor-agent --help` examples;
     runtime-unverified ids — vendor strings change).
"""
from __future__ import annotations

import logging
import re
import shlex

_LOG = logging.getLogger(__name__)

# Common ids shown when neither the ACP session nor `cursor-agent models`
# yields a list. Runtime-unverified (auth-gated catalogue); harmless if stale —
# cursor rejects unknown ids and the live ACP block wins whenever present.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "composer-1", "label": "Composer 1", "disabled": False},
        {"id": "gpt-5", "label": "GPT-5", "disabled": False},
        {"id": "sonnet-4.5", "label": "Sonnet 4.5", "disabled": False},
        {"id": "sonnet-4.5-thinking", "label": "Sonnet 4.5 Thinking", "disabled": False},
        {"id": "opus-4.5", "label": "Opus 4.5", "disabled": False},
    ],
    "default": "composer-1",
}


def parse_acp_models(session_models: "dict | None") -> dict:
    """Map an ACP ``models`` block ({currentModelId, availableModels:[{modelId,
    name}]}) to the widget shape {models:[{id,label,disabled}], default}.

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


def parse_cursor_models_text(text: str) -> dict:
    """Parse the ``cursor-agent models`` CLI output to the widget shape.

    The real (auth-gated) format is runtime-unverified; this targets grok's
    shape leniently::

        Default model: composer-1

        Available models:
          * composer-1 (default)
          - gpt-5
          sonnet-4.5-thinking

    Bulleted (``*``/``-``) and bare-id lines after the "Available models"
    header are both accepted. An unrecognized format yields an empty model
    list, which fetch_available_models treats as "use the fallback".
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
            m2 = re.match(r"(?:[*-]\s+)?(\S+)", line)
            if m2:
                mid = m2.group(1)
                models.append({"id": mid, "label": mid, "disabled": False})
    return {"models": models, "default": default}


async def fetch_available_models(
    session_models: "dict | None" = None,
    *,
    host=None,
    cursor_path: "str | None" = None,
) -> dict:
    """Best-effort model list for the picker. Never raises.

    Source precedence: the live ACP session block (preferred — authed, exact
    ids) → the ``cursor-agent models`` CLI on the host (auth-gated) → the
    static fallback list.
    """
    if isinstance(session_models, dict) and session_models.get("availableModels"):
        return _sorted_models(parse_acp_models(session_models))
    if host is not None and cursor_path:
        try:
            result = await host.run_command(f"{shlex.quote(cursor_path)} models")
            if getattr(result, "exit_code", 1) == 0:
                parsed = parse_cursor_models_text(getattr(result, "stdout", "") or "")
                if parsed["models"]:
                    return _sorted_models(parsed)
        except Exception:  # noqa: BLE001 — best-effort; fall through to fallback
            _LOG.info(
                "cursor model list: `cursor-agent models` failed; using fallback",
                exc_info=True,
            )
    return _sorted_models(_copy_fallback())


def _sorted_models(result: dict) -> dict:
    """Order the picker list alphabetically by label (case-insensitive); every
    cursor source emits models in an arbitrary order. ``default`` is a separate
    field and is unaffected by the reorder."""
    result["models"] = sorted(
        result["models"],
        key=lambda m: (m.get("label") or m.get("id") or "").lower(),
    )
    return result


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
