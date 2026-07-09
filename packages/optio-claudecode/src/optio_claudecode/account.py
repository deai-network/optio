"""Best-effort Anthropic account summary for seeded Claude Code logins.

Gives ``on_seed_saved`` a human-readable 2nd arg like
``"Plan: Claude Max 20x for Jane Doe <jane@x.com>"``, derived from the OAuth
token the operator just saved into the seed. Mirrors the ``~/local/bin/
claude-usage`` tool: GET ``/api/oauth/profile`` with the OAuth bearer token +
beta header, then format the plan tier (``profile.organization.rate_limit_tier``,
the "first line of --human minus the date") plus
``profile.account.full_name`` / ``.email``.

Entirely best-effort: missing credentials, a network/HTTP error, or an
unexpected profile shape all yield ``None`` — the summary is informational
(it is the optional 2nd callback arg), never load-bearing, so a failure here
must not disturb seed capture.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from optio_agents.account import EMPTY, AccountInfo, UsageWindow

_LOG = logging.getLogger(__name__)

# Credentials live under the isolated HOME (HOME=<workdir>/home), the same
# file Claude Code itself writes; it is in the seed manifest's include list,
# so at on_seed_saved time (before workdir cleanup) it is still on disk.
_CREDENTIALS_RELPATH = "home/.claude/.credentials.json"

# Prettify the well-known rate-limit-tier tokens; everything else (e.g. "20x")
# passes through unchanged. Mirrors claude-usage's _format_plan_name.
_PLAN_TOKENS = {"claude": "Claude", "max": "Max", "pro": "Pro"}


def _format_plan(profile: dict) -> str | None:
    """Friendly plan name from ``profile.organization.rate_limit_tier``.

    ``"default_claude_max_20x"`` → ``"Claude Max 20x"``. Returns None if the
    tier is missing — this is the date-stripped form of the claude-usage
    ``--human`` plan header."""
    org = profile.get("organization")
    if not isinstance(org, dict):
        return None
    tier = org.get("rate_limit_tier")
    if not isinstance(tier, str) or not tier:
        return None
    if tier.startswith("default_"):
        tier = tier[len("default_"):]
    return " ".join(_PLAN_TOKENS.get(t, t) for t in tier.split("_"))


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME creds token, then
    ``analyze_account``. Fail-soft → ``EMPTY`` on any failure (no creds file,
    no token, analysis error).

    Reads the OAuth access token from the isolated HOME's
    ``.claude/.credentials.json`` (``claudeAiOauth.accessToken``). No token
    refresh: the operator just minted it, so it is fresh; an expired/invalid
    token simply yields ``EMPTY`` (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_CREDENTIALS_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
        token = (data.get("claudeAiOauth") or {}).get("accessToken")
    except Exception:  # noqa: BLE001 — missing/unreadable/malformed creds → EMPTY
        return EMPTY
    if not isinstance(token, str) or not token:
        return EMPTY
    return await analyze_account(token)


# --- normalized AccountInfo (shared cross-engine shape) --------------------
#
# ``analyze_account`` is the per-engine seam every wrapper implements: a live
# OAuth access token in, a vendor-agnostic ``optio_agents.account.AccountInfo``
# out. Fail-soft: any error → ``EMPTY`` (account analysis must never block seed
# capture or launch). The usage windows come from the authoritative ``limits[]``
# array of ``/api/oauth/usage`` (the legacy top-level ``seven_day_<model>`` keys
# are now ``null``); global windows have ``scope is None`` (model=None), per-model
# windows carry ``scope.model.id`` (falling back to the lowercased display name).


async def _fetch_profile(access_token: str) -> dict | None:
    """Async wrapper around oauth's sync ``/api/oauth/profile`` fetcher (shared
    request builder; lazy import avoids the account<->oauth import cycle)."""
    from optio_claudecode.oauth import _profile_sync

    return await asyncio.get_event_loop().run_in_executor(None, _profile_sync, access_token)


async def _fetch_usage(access_token: str) -> dict | None:
    """Async wrapper around oauth's sync ``/api/oauth/usage`` fetcher."""
    from optio_claudecode.oauth import _usage_sync

    return await asyncio.get_event_loop().run_in_executor(None, _usage_sync, access_token)


def _parse_reset(ra) -> datetime | None:
    if not isinstance(ra, str) or not ra:
        return None
    try:
        return datetime.fromisoformat(ra)
    except ValueError:
        return None


def _windows_from_usage(usage: dict) -> list[UsageWindow]:
    """Build windows from the authoritative ``limits[]`` array (current claude
    usage shape). Each limit → one UsageWindow; ``scope.model`` → per-model tag."""
    out = []
    for lim in (usage or {}).get("limits") or []:
        if not isinstance(lim, dict):
            continue
        pct = lim.get("percent")
        if not isinstance(pct, (int, float)):
            continue
        scope = lim.get("scope") or {}
        model_obj = scope.get("model") if isinstance(scope, dict) else None
        model = None
        if isinstance(model_obj, dict):
            model = model_obj.get("id") or (
                (model_obj.get("display_name") or "").lower() or None
            )
        out.append(UsageWindow(
            label=lim.get("kind") or lim.get("group") or "limit",
            pct=float(pct),
            resets_at=_parse_reset(lim.get("resets_at")),
            model=model,
        ))
    return out


def _info_from(profile: dict, usage: dict | None) -> AccountInfo:
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    return AccountInfo(
        name=account.get("full_name") or None,
        email=account.get("email") or None,
        plan=_format_plan(profile),
        account_id=account.get("uuid") or None,
        windows=tuple(_windows_from_usage(usage or {})),
        raw={"profile": profile, "usage": usage},
    )


async def analyze_account(access_token: str) -> AccountInfo:
    """Best-effort claude ``AccountInfo`` from a live OAuth access token. Never
    raises → ``EMPTY`` on any failure."""
    try:
        profile = await _fetch_profile(access_token)
        if not isinstance(profile, dict):
            return EMPTY
        usage = await _fetch_usage(access_token)
        return _info_from(profile, usage if isinstance(usage, dict) else None)
    except Exception:  # noqa: BLE001 — fail-soft
        return EMPTY
