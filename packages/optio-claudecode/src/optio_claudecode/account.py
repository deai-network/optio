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
import urllib.request
from urllib.error import URLError

_LOG = logging.getLogger(__name__)

_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
_BETA_HEADER = "oauth-2025-04-20"
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


def format_account_summary(profile: dict) -> str | None:
    """``profile`` dict → ``"Plan: <plan> for <full_name> <<email>>"`` or None.

    Requires a resolvable plan tier and an email; ``full_name`` is optional
    (omitted form: ``"Plan: <plan> for <<email>>"``)."""
    if not isinstance(profile, dict):
        return None
    plan = _format_plan(profile)
    account = profile.get("account")
    account = account if isinstance(account, dict) else {}
    email = account.get("email")
    full_name = account.get("full_name")
    if not plan or not isinstance(email, str) or not email:
        return None
    if isinstance(full_name, str) and full_name:
        return f"Plan: {plan} for {full_name} <{email}>"
    return f"Plan: {plan} for <{email}>"


def _fetch_profile_sync(access_token: str) -> dict:
    req = urllib.request.Request(
        _PROFILE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": _BETA_HEADER,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_sync(access_token: str) -> str | None:
    try:
        profile = _fetch_profile_sync(access_token)
    except (URLError, ConnectionError, OSError, ValueError):
        return None
    return format_account_summary(profile)


async def resolve_account_summary(host) -> str | None:
    """Best-effort account summary from the seeded credentials; None on any
    failure (no creds file, no token, network/HTTP/parse error).

    Reads the OAuth access token from the isolated HOME's
    ``.claude/.credentials.json`` (``claudeAiOauth.accessToken``) and fetches
    the profile in an executor (sync urllib off the event loop). No token
    refresh: the operator just minted it, so it is fresh; an expired/invalid
    token simply yields None."""
    path = f"{host.workdir.rstrip('/')}/{_CREDENTIALS_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except Exception:  # noqa: BLE001 — missing/unreadable creds → no summary
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    except (ValueError, AttributeError):
        return None
    if not isinstance(token, str) or not token:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _resolve_sync, token)
