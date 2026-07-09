"""Best-effort Cursor account summary for seeded cursor-agent logins.

The per-engine ``analyze_account`` seam: a live Cursor session ``accessToken``
in, a vendor-agnostic ``optio_agents.account.AccountInfo`` out. Fail-soft: any
error (bad token, network/HTTP failure, unexpected shape) yields ``EMPTY`` --
account analysis is informational (it feeds the optional ``on_seed_saved``
summary + a stamped ``metadata.account``), never load-bearing, so a failure
here must never disturb seed capture, verify, or launch.

Cursor exposes no official personal usage/profile API; the dashboard's internal
``cursor.com/api/*`` endpoints are used instead, authenticated with the browser
session **cookie** (a Bearer header returns 204 -- the cookie is load-bearing):

    Cookie: WorkosCursorSessionToken=<jwt.sub>%3A%3A<accessToken>

where ``jwt.sub`` is the ``sub`` claim of the ``accessToken`` itself (a WorkOS
session JWT; decoded, not verified). Three read-only GETs then map into
``AccountInfo``: ``/api/auth/me`` (name/email/id), ``/api/auth/stripe`` (plan),
``/api/usage-summary`` (three plan-bucket usage windows + monthly reset).

Cursor meters plan buckets (total / auto-model / named-API), not per-model, so
every ``UsageWindow.model`` is ``None`` and all share one ``resets_at`` (the
billing-cycle end). See docs/2026-07-09-cursor-account-research.md.

These endpoints are **unofficial** dashboard internals with no stability
guarantee -- fail-soft is mandatory.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.request
from datetime import datetime
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo, UsageWindow

_LOG = logging.getLogger(__name__)

# Credentials live under the isolated HOME (HOME=<workdir>/home), the flat
# ``{"accessToken","refreshToken"}`` file cursor-agent writes; it is in the seed
# manifest include list, so at capture time (before workdir cleanup) it is still
# on disk.
_AUTH_RELPATH = "home/.config/cursor/auth.json"

_ME_URL = "https://cursor.com/api/auth/me"
_STRIPE_URL = "https://cursor.com/api/auth/stripe"
_USAGE_URL = "https://cursor.com/api/usage-summary"

# A plain UA suffices (verified); no custom header is required.
_USER_AGENT = "optio-cursor/account (external)"

# Prettify the well-known membership tokens; anything else passes through.
_PLAN_TOKENS = {"free": "Free", "pro": "Pro", "business": "Business"}


def _decode_jwt_sub(access_token: str) -> str | None:
    """The ``sub`` claim from the accessToken's middle JWT segment (base64url,
    **unverified** -- just b64-decode + json). This is the cookie userId (the
    ``google-oauth2|user_01…`` form), not the bare ``user_01…`` account id."""
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        seg = parts[1]
        seg += "=" * (-len(seg) % 4)  # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(seg).decode("utf-8"))
        sub = claims.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:  # noqa: BLE001 -- malformed token → no cookie → EMPTY
        return None


def _cookie(access_token: str) -> str | None:
    """The ``WorkosCursorSessionToken`` cookie value: ``<jwt.sub>::<token>`` with
    the ``::`` separator URL-encoded (``%3A%3A``). Returns None if the token has
    no decodable ``sub``."""
    sub = _decode_jwt_sub(access_token)
    if not sub:
        return None
    return f"WorkosCursorSessionToken={sub}%3A%3A{access_token}"


def _get_sync(url: str, cookie: str) -> dict | None:
    """Cookie-authed read-only GET → parsed JSON, or None on any HTTP/parse
    error (fail-soft)."""
    req = urllib.request.Request(
        url, headers={"Cookie": cookie, "User-Agent": _USER_AGENT}, method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _fetch(url: str, cookie: str) -> dict | None:
    """Async wrapper around the sync cookie GET (urllib in an executor). This is
    the monkeypatch seam the tests replace to avoid network."""
    return await asyncio.get_event_loop().run_in_executor(None, _get_sync, url, cookie)


def _parse_reset(ra) -> datetime | None:
    """ISO-8601 (``…Z``) → tz-aware datetime; None if absent/unparseable."""
    if not isinstance(ra, str) or not ra:
        return None
    try:
        return datetime.fromisoformat(ra.replace("Z", "+00:00"))
    except ValueError:
        return None


def _prettify_plan(raw) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    return _PLAN_TOKENS.get(raw.lower(), raw)


def _windows_from_usage(usage: dict | None) -> list[UsageWindow]:
    """Three plan-bucket windows from ``individualUsage.plan`` -- total / auto /
    api percentages against one monthly cycle. All ``model=None`` (cursor meters
    plan buckets, not per-model); all share ``resets_at = billingCycleEnd``."""
    if not isinstance(usage, dict):
        return []
    resets_at = _parse_reset(usage.get("billingCycleEnd"))
    plan = ((usage.get("individualUsage") or {}).get("plan")) or {}
    if not isinstance(plan, dict):
        return []
    out = []
    for label, key in (
        ("total", "totalPercentUsed"),
        ("auto", "autoPercentUsed"),
        ("api", "apiPercentUsed"),
    ):
        pct = plan.get(key)
        if not isinstance(pct, (int, float)) or isinstance(pct, bool):
            continue
        out.append(UsageWindow(
            label=label, pct=float(pct), resets_at=resets_at, model=None,
        ))
    return out


def _info_from(me: dict, stripe: dict | None, usage: dict | None) -> AccountInfo:
    plan_raw = None
    if isinstance(stripe, dict):
        plan_raw = stripe.get("membershipType")
    if not plan_raw and isinstance(usage, dict):
        plan_raw = usage.get("membershipType")  # fallback when stripe GET failed
    return AccountInfo(
        name=me.get("name") or None,
        email=me.get("email") or None,
        plan=_prettify_plan(plan_raw),
        account_id=me.get("sub") or None,  # bare WorkOS user_01… (not the JWT sub)
        windows=tuple(_windows_from_usage(usage)),
        raw={"me": me, "stripe": stripe, "usage": usage},
    )


async def analyze_account(creds: str) -> AccountInfo:
    """Best-effort cursor ``AccountInfo`` from a live session ``accessToken``.
    Never raises → ``EMPTY`` on any failure (bad token, HTTP/parse error,
    unexpected shape). Read-only GETs only -- no token refresh."""
    try:
        cookie = _cookie(creds)
        if not cookie:
            return EMPTY
        me = await _fetch(_ME_URL, cookie)
        if not isinstance(me, dict):
            return EMPTY
        stripe = await _fetch(_STRIPE_URL, cookie)
        usage = await _fetch(_USAGE_URL, cookie)
        return _info_from(
            me,
            stripe if isinstance(stripe, dict) else None,
            usage if isinstance(usage, dict) else None,
        )
    except Exception:  # noqa: BLE001 -- fail-soft
        return EMPTY


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME's ``auth.json``
    ``accessToken``, then ``analyze_account``. Fail-soft → ``EMPTY`` on any
    failure (no creds file, no token, analysis error).

    No token refresh: the operator just authed, so the token is fresh; an
    expired/invalid token simply yields ``EMPTY`` (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_AUTH_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
        token = data.get("accessToken")
    except Exception:  # noqa: BLE001 -- missing/unreadable/malformed creds → EMPTY
        return EMPTY
    if not isinstance(token, str) or not token:
        return EMPTY
    return await analyze_account(token)
