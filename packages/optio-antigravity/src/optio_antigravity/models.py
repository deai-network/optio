"""Available-model list for the Antigravity conversation widget's model picker.

============================================================================
MODEL-SWITCH MECHANISM — restart-with-new-model (claudecode-style).
See docs Stage 7 Task 7.1 + design §1 (Model selection).
============================================================================

Decision: **RESTART**, NOT inline (grok's ACP ``session/set_model``).

Antigravity's ``agy`` has **no live transport** (design §1) — no ACP, no
stream-json, no HTTP. A conversation is synthesised from repeated one-shot
``agy -p`` turns (conversation.py). Switching model is therefore trivial: set
the model applied to the *next* turn's ``--model`` flag. Because each turn
already resumes the captured conversation via ``--conversation <id>``, the new
model takes effect on the next turn without dropping conversation state — the
restart-with-new-model shape claudecode uses, adapted to the per-turn process
model. So ``AntigravityConversation.set_control("model", …)`` just records the
new model id (see conversation.py); there is no set-model wire request.

------------------------------------------------------------------------
MODEL LIST source. Unlike grok there is no ACP ``session/new`` model block, so
the only live source is the ``agy models`` subcommand (a plain id list). The
static ``FALLBACK_MODELS`` is the last resort when the CLI is unavailable.

TODO(S3): the exact ``agy models`` output format is pinned by the S3 CLI/
transcript spike (not yet run). ``parse_agy_models_text`` is written against the
most likely shape — one model id per line, optional ``(default)`` marker — and
must be reconciled once the real output is captured. The design records
Gemini-first + BYO Claude/GPT-OSS ids.
"""
from __future__ import annotations

import logging
import re
import shlex

_LOG = logging.getLogger(__name__)

# Common ids shown when `agy models` yields nothing (Gemini-first + BYO,
# design §1). TODO(S3): reconcile the exact default id set with the real CLI.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "disabled": False},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "disabled": False},
    ],
    "default": "gemini-2.5-pro",
}


def parse_agy_models_text(text: str) -> dict:
    """Parse the ``agy models`` CLI output to the widget shape
    ``{models:[{id,label,disabled}], default}``.

    Expected output — one model id per line, an optional ``(default)`` marker
    on the current model::

        gemini-2.5-pro
        gemini-2.5-flash (default)
        claude-sonnet-4
        gpt-oss-120b

    With no marker the first listed id is treated as the default so the picker
    always shows a selection. An empty body yields an empty list (the caller
    falls back to the static list).
    """
    default: str | None = None
    models: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # Strip a leading bullet ("* "/"- ") if the real CLI ever uses one.
        line = re.sub(r"^[*-]\s+", "", line)
        m = re.match(r"(\S+)(?:\s+\(default\))?$", line)
        if not m:
            continue
        mid = m.group(1)
        if "(default)" in line:
            default = mid
        models.append({"id": mid, "label": mid, "disabled": False})
    if models and default is None:
        default = models[0]["id"]
    return {"models": models, "default": default}


async def fetch_available_models(
    *,
    host=None,
    agy_path: "str | None" = None,
) -> dict:
    """Best-effort model list for the picker. Never raises.

    Source precedence: the ``agy models`` CLI on the host → the static fallback
    list. (Antigravity has no ACP session block to prefer, unlike grok.)
    """
    if host is not None and agy_path:
        try:
            result = await host.run_command(f"{shlex.quote(agy_path)} models")
            if getattr(result, "exit_code", 1) == 0:
                parsed = parse_agy_models_text(getattr(result, "stdout", "") or "")
                if parsed["models"]:
                    return parsed
        except Exception:  # noqa: BLE001 — best-effort; fall through to fallback
            _LOG.info(
                "antigravity model list: `agy models` failed; using fallback",
                exc_info=True,
            )
    return _copy_fallback()


def _copy_fallback() -> dict:
    return {
        "models": [dict(m) for m in FALLBACK_MODELS["models"]],
        "default": FALLBACK_MODELS["default"],
    }
