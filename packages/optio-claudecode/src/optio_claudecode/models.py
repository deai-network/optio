"""Fetch the account-available Claude model list for the conversation widget.

Claude Code exposes no programmatic model list, so we call the Anthropic Models
API (GET /v1/models) using the OAuth access token in the seeded
home/.claude/.credentials.json. We then mirror what Claude Code's own /model
dialog does:

  * Declutter: collapse the catalog to the latest model per family (opus,
    sonnet, haiku, fable, …) — dropping superseded/dated snapshots — so the
    picker shows a clean curated set instead of nine ids.
  * Availability: GET /v1/models lists models the account *cannot* use (e.g.
    Fable) with no flag, so we probe the uncertain ones the way Claude Code does
    — a 1-token POST /v1/messages; a ``not_found_error`` means the model is
    unavailable for this account and is marked ``disabled`` (greyed in the UI).
    Standard families (opus/sonnet/haiku) are known-good and skip the probe;
    only exotic families (fable, …) cost a probe (and only one token).

Best-effort throughout: any failure falls back to the common aliases / leaves a
model enabled rather than falsely disabling it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex

_LOG = logging.getLogger(__name__)

# Families that are always available for any account that can run Claude Code —
# no probe needed (mirrors Claude Code's known-good fast path).
KNOWN_GOOD_FAMILIES = {"opus", "sonnet", "haiku"}

# Graded reasoning-effort levels (ordered low→max) claude's `--effort` flag
# accepts. The live control (id="reasoning_effort") is a slider over these.
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# Families whose current models expose graded reasoning effort. GET /v1/models
# carries no effort-capability field, so this is a static per-family table
# (mirroring how Claude Code's own /model dialog knows which models grade
# effort). A family absent here → no effort control for that model (the slider
# is omitted, not disabled). haiku has no graded effort.
_EFFORT_FAMILIES = {"opus", "sonnet"}

# Default effort preselected on the slider when the caller sets no
# reasoning_effort (mid-high, matching Claude Code's own default posture).
DEFAULT_EFFORT = "high"


def model_effort(model_id: str) -> tuple[list[str] | None, str | None]:
    """Graded reasoning-effort capability for a model id.

    Returns ``(levels, default)`` when the model's family supports graded
    effort (levels is a fresh copy of ``EFFORT_LEVELS``), else ``(None, None)``
    so the caller omits the effort control. Robust to runtime/variant ids
    (e.g. ``claude-opus-4-8[1m]``): ``_parse_id`` ignores a trailing suffix it
    cannot match by falling back to the family token."""
    family, _, _ = _parse_id(model_id.split("[", 1)[0])
    if family in _EFFORT_FAMILIES:
        return (list(EFFORT_LEVELS), DEFAULT_EFFORT)
    return (None, None)

# Common aliases shown when the live fetch fails (offline, no creds, API change).
_FALLBACK_LIST: list[dict] = [
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
    {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
]
# The fetch-failure return value: the aliases, all enabled.
FALLBACK_MODELS: dict = {
    "models": [{**m, "disabled": False} for m in _FALLBACK_LIST],
    "default": None,
}

_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d+)*)(?:-(\d{8}))?$")


def _parse_id(model_id: str) -> tuple[str, tuple[int, ...], bool]:
    """(family, version-tuple, has_date) for a model id. Unparseable ids map to
    (id, (), False) so they survive declutter as their own family."""
    m = _ID_RE.match(model_id)
    if not m:
        return (model_id, (), False)
    family, ver, date = m.group(1), m.group(2), m.group(3)
    return (family, tuple(int(x) for x in ver.split("-")), bool(date))


def declutter(models: list[dict]) -> list[dict]:
    """Keep only the latest model per family (highest version; on a tie prefer
    the non-dated alias). Family order follows first appearance."""
    best: dict[str, tuple[tuple, dict]] = {}
    order: list[str] = []
    for item in models:
        family, ver, has_date = _parse_id(item["id"])
        if family not in best:
            order.append(family)
        cand = (ver, not has_date)  # higher version, then non-dated wins
        cur = best.get(family)
        if cur is None or cand > cur[0]:
            best[family] = (cand, item)
    return [best[f][1] for f in order]


def parse_models(api_json: dict) -> dict:
    """Map GET /v1/models ({data:[{id, display_name}]}) to a decluttered
    {models:[{id,label}], default} shape (no availability yet)."""
    out = []
    for m in api_json.get("data", []):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            out.append({"id": mid, "label": m.get("display_name") or mid})
    out = declutter(out)
    if not out:
        return {"models": list(_FALLBACK_LIST), "default": None}
    return {"models": out, "default": None}


def _read_oauth_token(creds_json: str) -> str | None:
    """Extract the Claude Code OAuth access token from a .credentials.json blob."""
    try:
        data = json.loads(creds_json)
    except Exception:  # noqa: BLE001
        return None
    oauth = data.get("claudeAiOauth") or data.get("oauth") or {}
    return oauth.get("accessToken") or oauth.get("access_token") or data.get("accessToken")


def _probe_cmd(token: str, model_id: str) -> str:
    payload = json.dumps(
        {"model": model_id, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
    )
    return (
        "curl -sS -X POST https://api.anthropic.com/v1/messages "
        f"-H 'authorization: Bearer {token}' "
        "-H 'anthropic-version: 2023-06-01' -H 'anthropic-beta: oauth-2025-04-20' "
        "-H 'content-type: application/json' "
        f"-d {shlex.quote(payload)}"
    )


async def _probe_disabled(host, token: str, model_id: str) -> bool:
    """True iff a 1-token probe says the model is unavailable for this account.
    Only a ``not_found_error`` disables — a rate_limit (429) or any other
    outcome leaves the model enabled (it exists, just throttled)."""
    try:
        result = await host.run_command(_probe_cmd(token, model_id))
        body = json.loads(result.stdout)
        return body.get("error", {}).get("type") == "not_found_error"
    except Exception:  # noqa: BLE001
        return False  # best-effort: never falsely disable


async def fetch_available_models(host, *, home_dir: str) -> dict:
    """Best-effort decluttered, availability-probed model list. Never raises."""
    try:
        creds = (
            await host.fetch_bytes_from_host(f"{home_dir}/.claude/.credentials.json")
        ).decode("utf-8")
    except Exception:  # noqa: BLE001
        _LOG.info("model list: no credentials file; using fallback")
        return FALLBACK_MODELS
    token = _read_oauth_token(creds)
    if not token:
        return FALLBACK_MODELS
    try:
        result = await host.run_command(
            "curl -fsS https://api.anthropic.com/v1/models "
            f"-H 'authorization: Bearer {token}' "
            "-H 'anthropic-version: 2023-06-01' "
            "-H 'anthropic-beta: oauth-2025-04-20'"
        )
        if result.exit_code != 0:
            _LOG.info("model list: live fetch failed (exit %s); fallback", result.exit_code)
            return FALLBACK_MODELS
        parsed = parse_models(json.loads(result.stdout))
    except Exception:  # noqa: BLE001
        _LOG.info("model list: live fetch failed; fallback", exc_info=True)
        return FALLBACK_MODELS

    async def _annotate(item: dict) -> dict:
        family, _, _ = _parse_id(item["id"])
        if family in KNOWN_GOOD_FAMILIES:
            return {**item, "disabled": False}
        return {**item, "disabled": await _probe_disabled(host, token, item["id"])}

    annotated = await asyncio.gather(*(_annotate(m) for m in parsed["models"]))
    return {"models": list(annotated), "default": parsed.get("default")}
