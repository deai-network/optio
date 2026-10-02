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
# This is the canonical *universe* of tiers; each model advertises which of
# them it actually supports (see _effort_tiers / parse_models).
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# Default effort preselected on the slider when the caller sets no
# reasoning_effort (mid-high, matching Claude Code's own default posture).
DEFAULT_EFFORT = "high"


def _effort_tiers(capabilities: dict) -> list[str] | None:
    """Ordered supported effort tiers from a model's ``capabilities.effort``.

    GET /v1/models carries per-model effort capability under
    ``capabilities.effort = {supported: bool, <tier>: {supported: bool}, ...}``.
    ``effort.supported`` false (or the field being absent) means the model has
    no graded effort → return ``None``; otherwise return the ``EFFORT_LEVELS``-
    ordered subset whose per-tier ``supported`` flag is true (a tier with
    ``supported: false`` is unavailable, e.g. sonnet-4-6 lacks ``xhigh``)."""
    effort = (capabilities or {}).get("effort")
    if not isinstance(effort, dict) or not effort.get("supported"):
        return None
    tiers = [
        lvl for lvl in EFFORT_LEVELS
        if isinstance(effort.get(lvl), dict) and effort[lvl].get("supported")
    ]
    return tiers or None


def model_effort(
    model_id: str, catalog: list[dict],
) -> tuple[list[str] | None, str | None]:
    """Graded reasoning-effort capability for a model id, read from the fetched
    catalog (``parse_models`` captured ``capabilities.effort`` per model).

    Returns ``(levels, default)`` — ``levels`` a fresh copy of the model's
    supported tiers, ``default`` the preselected slider value — when the model's
    catalog entry advertises supported effort tiers, else ``(None, None)`` so the
    caller omits the effort control. Robust to runtime/variant ids (e.g.
    ``claude-opus-4-8[1m]``): the trailing ``[..]`` suffix is stripped before the
    catalog lookup. A model absent from the catalog (or without captured effort)
    yields ``(None, None)``."""
    base = model_id.split("[", 1)[0]
    entry = next((m for m in catalog if m.get("id") == base), None)
    tiers = (entry or {}).get("effort")
    if not tiers:
        return (None, None)
    default = DEFAULT_EFFORT if DEFAULT_EFFORT in tiers else tiers[-1]
    return (list(tiers), default)

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

# Version is non-greedy so a trailing -YYYYMMDD lands in the date group.
# A greedy version would swallow the date (claude-haiku-4-5-20251001 becomes
# version 4.5.20251001) and sort newer than the undated alias claude-haiku-4-5.
_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d+)*?)(?:-(\d{8}))?$")


def _parse_id(model_id: str) -> tuple[str, tuple[int, ...], bool]:
    """(family, version-tuple, has_date) for a model id. Unparseable ids map to
    (id, (), False) so they survive declutter as their own family."""
    m = _ID_RE.match(model_id)
    if not m:
        return (model_id, (), False)
    family, ver, date = m.group(1), m.group(2), m.group(3)
    return (family, tuple(int(x) for x in ver.split("-")), bool(date))


def model_from_transcript(text: str) -> str | None:
    """Model id the conversation ended on, from a Claude jsonl transcript.

    Each assistant turn records ``message.model`` (a runtime id, so a
    ``[variant]`` suffix is kept). The last such value wins. Lines that are
    not JSON, and turns that name no model, are skipped. ``None`` when the
    text names no model."""
    found: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001 — a tail read may start mid-line
            continue
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        model = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(model, str) and model:
            found = model
    return found


def resume_launch_model(
    *, pinned: str | None, transcript_model: str | None, catalog: list[dict],
) -> str | None:
    """Catalog id to pass as ``--model`` when continuing a conversation.

    The family comes from ``pinned`` when the task names a model, otherwise
    from the model the transcript ended on. The result is the newest enabled
    catalog id in that family (same ordering as ``declutter``: higher version,
    then an undated id over a dated snapshot). A ``[variant]`` suffix on the
    source id is ignored.

    ``None`` means "do not pass ``--model``": there is no source model, the id
    is not a ``claude-<family>-<version>`` id, the catalog has no enabled
    sibling, or the newest sibling is older than the model already in use
    (a stale fallback catalog must not downgrade the session)."""
    source = pinned or transcript_model
    if not source:
        return None
    base = source.split("[", 1)[0]
    if _ID_RE.match(base) is None:
        return None
    family, prev_ver, prev_dated = _parse_id(base)
    prev_key = (prev_ver, not prev_dated)
    best_key: tuple | None = None
    best_id: str | None = None
    for item in catalog:
        if item.get("disabled"):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or _ID_RE.match(mid) is None:
            continue
        fam, ver, has_date = _parse_id(mid)
        if fam != family:
            continue
        key = (ver, not has_date)
        if best_key is None or key > best_key:
            best_key = key
            best_id = mid
    if best_id is None or best_key is None or best_key < prev_key:
        return None
    return best_id


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
    """Map GET /v1/models to a decluttered ``{models:[{id,label,effort?}], default}``
    shape (no availability yet).

    Each ``data`` entry carries ``{id, display_name, capabilities:{effort,...}}``;
    we capture the per-model supported effort tiers (``_effort_tiers``) onto the
    ``effort`` key when present, so ``model_effort`` can grade the reasoning
    slider from real capability data rather than a family guess. The key is
    omitted entirely for models with no graded effort."""
    out = []
    for m in api_json.get("data", []):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            entry = {"id": mid, "label": m.get("display_name") or mid}
            tiers = _effort_tiers(m.get("capabilities") or {})
            if tiers is not None:
                entry["effort"] = tiers
            out.append(entry)
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
