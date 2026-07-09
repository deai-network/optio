"""Best-effort Grok (xAI) account summary for seeded grok-cli logins.

The per-engine ``analyze_account`` seam every wrapper implements: the live grok
``creds`` dict in, a vendor-agnostic ``optio_agents.account.AccountInfo`` out.
Fail-soft: any error (bad token, HTTP/parse error, unexpected shape) degrades --
never raises -- so a failure here never disturbs seed capture, verify, or launch.
Account analysis is informational (it feeds the ``on_seed_saved`` summary + a
stamped ``metadata.account``), never load-bearing.

Grok is **accounts-only**: identity + plan are read-only GET-capturable, but
usage / rate-limits is UNREACHABLE with the CLI's OAuth token
(``POST grok.com/rest/rate-limits`` → 403 ``oauth2-auth-forbidden`` -- a
web-session-only surface). So ``windows`` is ALWAYS the empty tuple and
``is_limited`` is always False for grok. No usage/rate-limit call is attempted.

``creds`` is the grok auth.json inner dict (``store["<issuer>::<client>"]``): the
access token is ``creds["key"]`` and identity is already on it
(``email``/``first_name``/``user_id``/``team_id``). Three read-only Bearer GETs
enrich it, each fail-soft:

  * ``GET https://auth.x.ai/oauth2/userinfo``   → ``name``/``email``/``sub``
  * ``GET https://api.x.ai/v1/me``              → ``user_id``/``team_id``
  * ``GET https://grok.com/rest/subscriptions`` → active row ``tier``/``status``

Zero-network fallback: ``creds.{email,first_name,user_id}`` already give
name/email/account_id with no HTTP, so even if every GET fails the analyzer
degrades to identity-from-creds -- never EMPTY when creds carry them. Only truly
empty creds yield EMPTY. See docs/2026-07-09-grok-account-research.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import replace
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo

_LOG = logging.getLogger(__name__)

# Read-only identity + plan surfaces (all Bearer <creds.key>, all GET, verified
# live 2026-07-09). NB no usage/rate-limit URL -- unreachable with this token.
_USERINFO_URL = "https://auth.x.ai/oauth2/userinfo"
_V1_ME_URL = "https://api.x.ai/v1/me"
_SUBSCRIPTIONS_URL = "https://grok.com/rest/subscriptions"

_USER_AGENT = "optio-grok/account (external)"
_HTTP_TIMEOUT_S = 15

# The active-subscription enum prefix stripped when prettifying the plan name.
_TIER_PREFIX = "SUBSCRIPTION_TIER_"
_ACTIVE_STATUS = "SUBSCRIPTION_STATUS_ACTIVE"

# Live-workdir credentials (isolated HOME) -- the same file grok-cli writes,
# still on disk at capture time (pre-cleanup). Mirrors verify._AUTH_MEMBER.
_AUTH_RELPATH = "home/.grok/auth.json"


# --- synchronous HTTP (run in an executor; no host, no grok) -----------------


def _get_sync(url: str, token: str) -> "dict | None":
    """Bearer-authed read-only GET → parsed JSON dict, or None on any HTTP/parse
    error (fail-soft)."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _fetch(url: str, token: str) -> "dict | None":
    """Async wrapper around the sync Bearer GET (urllib in an executor). This is
    the monkeypatch seam the tests replace to avoid network."""
    return await asyncio.get_event_loop().run_in_executor(None, _get_sync, url, token)


async def _try_fetch(url: str, token: str) -> "dict | None":
    """``_fetch`` with an individual guard so a raising fetcher degrades to None
    (identity then falls back to creds) rather than aborting the whole analysis."""
    try:
        return await _fetch(url, token)
    except Exception:  # noqa: BLE001 -- per-GET fail-soft, never EMPTY when creds carry identity
        return None


# --- normalized AccountInfo mapping ------------------------------------------


def _prettify_tier(tier) -> "str | None":
    """``SUBSCRIPTION_TIER_GROK_PRO`` → ``"Grok Pro"``: strip the enum prefix,
    then title-case the ``_``-split tokens. None when absent/blank."""
    if not isinstance(tier, str) or not tier.strip():
        return None
    body = tier.strip()
    if body.startswith(_TIER_PREFIX):
        body = body[len(_TIER_PREFIX):]
    tokens = [tok.capitalize() for tok in body.split("_") if tok]
    return " ".join(tokens) or None


def _plan_from_subscriptions(subs) -> "str | None":
    """Prettified ``tier`` of the row whose ``status == SUBSCRIPTION_STATUS_ACTIVE``
    (there is exactly one in the live data). None if no active row / bad shape --
    a lapsed or absent subscription is simply no plan."""
    if not isinstance(subs, dict):
        return None
    rows = subs.get("subscriptions")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("status") == _ACTIVE_STATUS:
            return _prettify_tier(row.get("tier"))
    return None


def _info_from_payloads(userinfo, v1_me, subs) -> AccountInfo:
    """Map the three read-only surfaces into an ``AccountInfo`` — identity from
    the GETs only (no creds fallback; that is ``analyze_account``'s job)."""
    ui = userinfo if isinstance(userinfo, dict) else {}
    return AccountInfo(
        name=ui.get("name") or None,
        email=ui.get("email") or None,
        account_id=ui.get("sub") or None,
        plan=_plan_from_subscriptions(subs),
        windows=(),  # grok exposes no GET usage source → always empty
        raw={"userinfo": userinfo, "v1_me": v1_me, "subscriptions": subs},
    )


def _with_creds_fallback(info: AccountInfo, creds: dict) -> AccountInfo:
    """Fill any identity the GETs left blank from the zero-network creds fields
    (``first_name``/``email``/``user_id``), so a fully-failed fetch still yields
    creds-carried identity rather than EMPTY. Plan/windows/raw are untouched."""
    return replace(
        info,
        name=info.name or creds.get("first_name") or None,
        email=info.email or creds.get("email") or None,
        account_id=info.account_id or creds.get("user_id") or None,
    )


async def account_from_xai(access_token: str) -> AccountInfo:
    """Creds-form-agnostic xAI account map helper: an access token in, a
    normalized ``AccountInfo`` out. Enriches identity with three read-only Bearer
    GETs (userinfo / v1_me / subscriptions), each fail-soft; ``windows`` is always
    empty (grok exposes no reachable usage source). Never raises → EMPTY only on
    an unexpected backstop error. Identity comes purely from the GETs; the
    creds-carried zero-network fallback lives in the ``analyze_account`` wrapper."""
    try:
        userinfo = v1_me = subs = None
        if isinstance(access_token, str) and access_token:
            userinfo = await _try_fetch(_USERINFO_URL, access_token)
            v1_me = await _try_fetch(_V1_ME_URL, access_token)
            subs = await _try_fetch(_SUBSCRIPTIONS_URL, access_token)
        return _info_from_payloads(userinfo, v1_me, subs)
    except Exception:  # noqa: BLE001 -- fail-soft backstop, never disturbs the caller
        return EMPTY


async def analyze_account(creds) -> AccountInfo:
    """Best-effort grok ``AccountInfo`` from the auth.json inner ``creds`` dict
    (``store["<issuer>::<client>"]``; access token = ``creds["key"]``). Thin
    wrapper: extracts the token, calls the ``account_from_xai`` map helper, then
    overlays the zero-network creds identity fallback. Never raises → EMPTY only
    for truly empty creds; any HTTP failure degrades to the creds-carried
    identity, never EMPTY. ``windows`` is always empty (no reachable usage source)."""
    try:
        if not isinstance(creds, dict) or not creds:
            return EMPTY
        token = creds.get("key")
        info = await account_from_xai(token if isinstance(token, str) else "")
        return _with_creds_fallback(info, creds)
    except Exception:  # noqa: BLE001 -- fail-soft backstop, never disturbs the caller
        return EMPTY


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME's ``.grok/auth.json``
    (``{"<issuer>::<client>": {...creds...}}``), then ``analyze_account`` its
    inner creds dict. Fail-soft → EMPTY on any failure (no creds file,
    unreadable/malformed, empty store).

    No token refresh: the operator just authed, so the token is fresh; an
    expired/invalid token simply yields EMPTY (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_AUTH_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 -- missing/unreadable/malformed creds → EMPTY
        return EMPTY
    if not isinstance(data, dict) or not data:
        return EMPTY
    creds = next(iter(data.values()))
    if not isinstance(creds, dict):
        return EMPTY
    return await analyze_account(creds)
