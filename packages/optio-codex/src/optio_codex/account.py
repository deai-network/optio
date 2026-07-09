"""Best-effort Codex (OpenAI/ChatGPT) account summary for seeded logins.

The per-engine ``analyze_account`` seam every wrapper implements: live OAuth
credentials in, a vendor-agnostic ``optio_agents.account.AccountInfo`` out.
Fail-soft: any error (missing field, HTTP/parse error, malformed token) yields
``EMPTY`` -- account analysis is informational (it feeds the ``on_seed_saved``
2nd arg + a ``metadata.account`` stamp), never load-bearing, so a failure here
must not disturb seed capture, verify, or launch.

Divergence from claudecode: codex ``creds`` is the whole ``auth.json`` **tokens
dict** (``{id_token, access_token, account_id}``), not a bare access token.
Rationale --

  * **Identity is OFFLINE.** ``tokens.id_token`` is a JWT whose middle segment
    b64url-decodes to the identity claims (``name``, ``email``,
    ``chatgpt_plan_type``, ``chatgpt_account_id``). No network is needed for
    ``name``/``email``/``plan``/``account_id``; this is also the fail-soft
    identity fallback when the live usage GET fails.
  * **account_id** = ``tokens.account_id`` (the ChatGPT account uuid), needed
    both for the ``ChatGPT-Account-Id`` request header and for
    ``AccountInfo.account_id``. NB the ``usage.account_id`` field is the *user*
    id string, not the uuid -- do NOT use it.
  * **Usage windows** come from a read-only, non-billable
    ``GET https://chatgpt.com/backend-api/wham/usage`` (the same endpoint the
    codex TUI polls). chatgpt.com serves a Cloudflare challenge to a blank UA,
    so a real ``codex_cli_rs/<ver>`` User-Agent is REQUIRED.

See ``docs/2026-07-09-codex-account-research.md`` for the captured payloads and
the full field mapping.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo, UsageWindow

_LOG = logging.getLogger(__name__)

# The ChatGPT backend (NOT api.openai.com). Read-only, non-billable.
_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# A real UA is REQUIRED -- chatgpt.com serves a Cloudflare HTML challenge to a
# blank/curl User-Agent. Any real codex_cli_rs version string works.
_USER_AGENT = "codex_cli_rs/0.142.5"
_HTTP_TIMEOUT_S = 15

# id_token claim namespace carrying the ChatGPT identity block.
_AUTH_CLAIM = "https://api.openai.com/auth"

# Live-workdir credentials (isolated HOME) -- the same file codex itself writes,
# still on disk at capture time (pre-cleanup). Mirrors verify._AUTH_RELPATH.
_AUTH_RELPATH = "home/.codex/auth.json"

# Prettify the well-known plan tokens; unknown tokens pass through title-cased.
# Only ``free`` is confirmed against a live seed; paid spellings are from codex
# source (chatgpt_plan_type) and want a paid-seed confirmation.
_PLAN_NAMES = {
    "free": "Free",
    "plus": "ChatGPT Plus",
    "pro": "ChatGPT Pro",
    "team": "ChatGPT Team",
    "business": "ChatGPT Business",
    "enterprise": "ChatGPT Enterprise",
    "edu": "ChatGPT Edu",
}


# --- synchronous HTTP (run in an executor; no host, no codex) ----------------


def _usage_sync(access_token: str, account_id: str) -> "dict | None":
    """GET the wham/usage window source. Fail-soft -> None on any error."""
    req = urllib.request.Request(
        _USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-Id": account_id,
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _fetch_usage(access_token: str, account_id: str) -> "dict | None":
    """Async wrapper around the sync wham/usage fetcher (executor)."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _usage_sync, access_token, account_id
    )


# --- offline id_token decode -------------------------------------------------


def _decode_id_token(id_token) -> dict:
    """Decode the JWT id_token's claims OFFLINE (b64url the middle segment; no
    signature check, no network). Returns ``{}`` on any malformation -- never
    raises -- so a bad id_token degrades to the usage-only identity path."""
    if not isinstance(id_token, str) or id_token.count(".") < 2:
        return {}
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)  # restore b64url padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


# --- normalized AccountInfo mapping -----------------------------------------


def _format_plan(plan_type) -> "str | None":
    """``chatgpt_plan_type`` -> display name. ``free`` -> ``"Free"``; unknown
    tokens title-case with ``_`` -> space. None when absent."""
    if not isinstance(plan_type, str) or not plan_type.strip():
        return None
    key = plan_type.strip().lower()
    return _PLAN_NAMES.get(key, plan_type.replace("_", " ").title())


def _reset_at(window: dict) -> "datetime | None":
    """Absolute reset time. Prefer ``reset_at`` (unix epoch seconds); fall back
    to ``now + reset_after_seconds`` (relative). None if neither is present."""
    ra = window.get("reset_at")
    if isinstance(ra, (int, float)):
        try:
            return datetime.fromtimestamp(ra, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    ras = window.get("reset_after_seconds")
    if isinstance(ras, (int, float)):
        return datetime.now(timezone.utc) + timedelta(seconds=ras)
    return None


def _window_from(label: str, window, *, model: "str | None" = None) -> "UsageWindow | None":
    """One vendor window dict -> UsageWindow. None when the dict is null/absent
    or carries no ``used_percent``."""
    if not isinstance(window, dict):
        return None
    pct = window.get("used_percent")
    if not isinstance(pct, (int, float)):
        return None
    return UsageWindow(
        label=label, pct=float(pct), resets_at=_reset_at(window), model=model
    )


def _windows_from_usage(usage: dict) -> list[UsageWindow]:
    """Build windows from ``usage.rate_limit``. Global windows (primary /
    secondary / code_review) carry ``model=None``; ``additional_rate_limits[]``
    are per-model (``model`` set from the entry). Null windows are skipped."""
    rl = usage.get("rate_limit")
    if not isinstance(rl, dict):
        return []
    out: list[UsageWindow] = []
    for label, key in (("primary", "primary_window"), ("secondary", "secondary_window")):
        w = _window_from(label, rl.get(key))
        if w is not None:
            out.append(w)
    crl = _window_from("code_review", rl.get("code_review_rate_limit"))
    if crl is not None:
        out.append(crl)
    additional = rl.get("additional_rate_limits")
    if isinstance(additional, list):
        for entry in additional:
            if not isinstance(entry, dict):
                continue
            label = entry.get("name") or entry.get("id") or entry.get("label") or "additional"
            model = entry.get("model") or entry.get("model_id") or entry.get("name")
            w = _window_from(str(label), entry, model=model)
            if w is not None:
                out.append(w)
    return out


def _info_from(claims: dict, usage: "dict | None", account_id: "str | None") -> AccountInfo:
    auth = claims.get(_AUTH_CLAIM)
    if not isinstance(auth, dict):
        auth = {}
    u = usage or {}
    plan = _format_plan(u.get("plan_type")) or _format_plan(auth.get("chatgpt_plan_type"))
    return AccountInfo(
        name=claims.get("name") or None,
        email=u.get("email") or claims.get("email") or None,
        plan=plan,
        # The account uuid: creds.account_id (== id_token chatgpt_account_id).
        # NOT usage.account_id (that field is the user-id string).
        account_id=account_id or auth.get("chatgpt_account_id") or None,
        windows=tuple(_windows_from_usage(u)),
        raw={"usage": usage, "id_token": claims},
    )


async def analyze_account(creds) -> AccountInfo:
    """Best-effort codex ``AccountInfo`` from the live ``auth.json`` tokens dict
    (``{id_token, access_token, account_id}``). Identity is decoded offline from
    the id_token; usage windows come from a read-only wham/usage GET. Never
    raises -> ``EMPTY`` on any failure."""
    try:
        if not isinstance(creds, dict):
            return EMPTY
        claims = _decode_id_token(creds.get("id_token"))
        access = creds.get("access_token")
        account_id = creds.get("account_id")
        usage = None
        if isinstance(access, str) and access and isinstance(account_id, str) and account_id:
            usage = await _fetch_usage(access, account_id)
        return _info_from(claims, usage if isinstance(usage, dict) else None, account_id)
    except Exception:  # noqa: BLE001 -- fail-soft, never disturbs the caller
        return EMPTY


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME's ``auth.json`` tokens,
    then ``analyze_account``. Fail-soft -> ``EMPTY`` on any failure (no auth
    file, no tokens, analysis error).

    No token refresh: the operator just authed, so the token is fresh; an
    expired/invalid token simply yields ``EMPTY`` (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_AUTH_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
        tokens = data.get("tokens")
    except Exception:  # noqa: BLE001 -- missing/unreadable/malformed auth -> EMPTY
        return EMPTY
    if not isinstance(tokens, dict):
        return EMPTY
    return await analyze_account(tokens)
