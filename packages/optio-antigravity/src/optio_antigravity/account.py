"""Best-effort Antigravity (Google Antigravity / ``agy``) account summary.

The per-engine ``analyze_account`` seam every wrapper implements: a live Google
OAuth access token in, a vendor-agnostic ``optio_agents.account.AccountInfo``
out. Fail-soft: account analysis is informational (it feeds the optional
``on_seed_saved`` summary + a stamped ``metadata.account``), never load-bearing,
so a failure here must never disturb seed capture, verify, or launch.

agy authenticates only against Google (consumer OIDC). ``creds`` is the opaque
``ya29.…`` Google OAuth access token from the seed's token store
(``store["token"]["access_token"]``) — NOT a JWT, so it carries no identity
claims: every field is a read-only network call. Three vendor GET/POSTs, each in
an executor, each fail-soft:

  1. ``GET https://www.googleapis.com/oauth2/v1/userinfo`` (Bearer) → identity
     (``name`` / ``email`` / ``id``). This is the identity FLOOR: if it fails
     there is nothing to stamp → ``EMPTY``.
  2. ``POST …/v1internal:loadCodeAssist`` (Bearer, ``{"metadata":{ideType,
     platform,pluginType}}``) → plan (``currentTier.id``, e.g. ``"free-tier"`` →
     ``"Free"``). Metadata accepts ONLY those three keys (extras 400).
  3. ``POST …/v1internal:retrieveUserQuotaSummary`` (Bearer, body ``{}``) →
     per-model usage windows (one ``UsageWindow`` per bucket).

Plan + quota are best-effort: a failure there degrades to an identity-only
``AccountInfo`` (still populated name/email/id), NEVER ``EMPTY``. Only a total
identity failure yields ``EMPTY``.

NEVER calls ``streamGenerateContent`` or any generate verb (those consume quota).
No token refresh (the operator/verify just produced a fresh token; a refresh
without save-back strands the seed). See
``docs/2026-07-09-antigravity-account-research.md`` for the captured payloads and
the VALIDATED field mapping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from datetime import datetime
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo, UsageWindow

from optio_antigravity.seed_manifest import _TOKEN_STORE_RELPATH

_LOG = logging.getLogger(__name__)

# Google identity (opaque ya29 bearer). Read-only.
_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"
# Google Cloud Code Assist — agy's plan/quota backend. POST-only v1internal verbs.
_LOAD_CODE_ASSIST_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
_QUOTA_SUMMARY_URL = (
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
)

# VALIDATED: the ClientMetadata object accepts ONLY these three keys here; extras
# (extensionName/locale/ideName) are rejected with HTTP 400.
_LOAD_CODE_ASSIST_BODY = {
    "metadata": {
        "ideType": "ANTIGRAVITY",
        "platform": "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }
}

_HTTP_TIMEOUT_S = 15
_USER_AGENT = "optio-antigravity/account (external)"

# Live-workdir token store (isolated HOME=<workdir>/home) — the same file agy
# writes, still on disk at capture time (pre-cleanup). Derived from the seed
# manifest SSOT so the live path can never diverge from the captured member.
_TOKEN_RELPATH = f"home/{_TOKEN_STORE_RELPATH}"


# --- synchronous HTTP (run in an executor; no host, no agy) ------------------


def _userinfo_sync(access_token: str) -> "dict | None":
    """GET the Google userinfo identity. Fail-soft → None on any error."""
    req = urllib.request.Request(
        _USERINFO_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="GET",
    )
    return _read_json(req)


def _post_sync(url: str, access_token: str, body: dict) -> "dict | None":
    """Bearer POST a JSON body to a v1internal verb. Fail-soft → None."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    return _read_json(req)


def _read_json(req: "urllib.request.Request") -> "dict | None":
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (HTTPError, URLError, OSError, ValueError):
        return None


# --- async fetch seams (the tests monkeypatch these) -------------------------


async def _fetch_userinfo(access_token: str) -> "dict | None":
    return await asyncio.get_event_loop().run_in_executor(
        None, _userinfo_sync, access_token
    )


async def _fetch_load_code_assist(access_token: str) -> "dict | None":
    return await asyncio.get_event_loop().run_in_executor(
        None, _post_sync, _LOAD_CODE_ASSIST_URL, access_token, _LOAD_CODE_ASSIST_BODY
    )


async def _fetch_quota_summary(access_token: str) -> "dict | None":
    return await asyncio.get_event_loop().run_in_executor(
        None, _post_sync, _QUOTA_SUMMARY_URL, access_token, {}
    )


# --- normalized AccountInfo mapping -----------------------------------------


def _prettify_plan(raw) -> "str | None":
    """``currentTier.id`` → display name. ``"free-tier"`` → ``"Free"`` (strip the
    ``-tier`` suffix, title-case). None when absent/blank."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("-tier"):
        s = s[: -len("-tier")]
    return s.replace("-", " ").replace("_", " ").strip().title() or None


def _parse_reset(ra) -> "datetime | None":
    """ISO-8601 (``…Z``) → tz-aware datetime; None if absent/unparseable."""
    if not isinstance(ra, str) or not ra:
        return None
    try:
        return datetime.fromisoformat(ra.replace("Z", "+00:00"))
    except ValueError:
        return None


def _windows_from_quota(quota: "dict | None") -> "list[UsageWindow]":
    """One ``UsageWindow`` per ``groups[].buckets[]``. per-model scope:
    ``model = bucketId``. ``pct = (1 - remainingFraction) * 100`` (a missing
    ``remainingFraction`` means a fully-restored bucket → treat as 1.0 → 0%)."""
    if not isinstance(quota, dict):
        return []
    out: list[UsageWindow] = []
    for group in quota.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            frac = bucket.get("remainingFraction")
            if not isinstance(frac, (int, float)) or isinstance(frac, bool):
                frac = 1.0  # omitted → bucket fully restored
            bucket_id = bucket.get("bucketId")
            out.append(
                UsageWindow(
                    label=bucket.get("displayName") or bucket_id or "",
                    pct=(1.0 - float(frac)) * 100.0,
                    resets_at=_parse_reset(bucket.get("resetTime")),
                    model=bucket_id or None,
                )
            )
    return out


def _info_from(
    userinfo: dict, load: "dict | None", quota: "dict | None"
) -> AccountInfo:
    plan = None
    if isinstance(load, dict):
        tier = load.get("currentTier")
        if isinstance(tier, dict):
            plan = _prettify_plan(tier.get("id"))
    return AccountInfo(
        name=userinfo.get("name") or None,
        email=userinfo.get("email") or None,
        plan=plan,
        account_id=userinfo.get("id") or None,
        windows=tuple(_windows_from_quota(quota)),
        raw={"userinfo": userinfo, "loadCodeAssist": load, "quotaSummary": quota},
    )


async def analyze_account(access_token: str) -> AccountInfo:
    """Best-effort antigravity ``AccountInfo`` from a live ``ya29`` access token.

    Identity (userinfo) is the floor: if it fails there is nothing to stamp →
    ``EMPTY``. Plan (loadCodeAssist) + usage windows (retrieveUserQuotaSummary)
    are best-effort — a failure there degrades to an identity-only ``AccountInfo``
    (NEVER ``EMPTY``). Read-only calls only; no token refresh."""
    try:
        if not isinstance(access_token, str) or not access_token:
            return EMPTY
        userinfo = await _fetch_userinfo(access_token)
        if not isinstance(userinfo, dict):
            return EMPTY
    except Exception:  # noqa: BLE001 — identity failure → EMPTY (fail-soft)
        return EMPTY

    # Identity acquired. Plan + quota degrade gracefully: a failed POST yields an
    # identity-only AccountInfo, never EMPTY.
    try:
        load = await _fetch_load_code_assist(access_token)
    except Exception:  # noqa: BLE001
        load = None
    try:
        quota = await _fetch_quota_summary(access_token)
    except Exception:  # noqa: BLE001
        quota = None

    return _info_from(
        userinfo,
        load if isinstance(load, dict) else None,
        quota if isinstance(quota, dict) else None,
    )


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME's agy token store,
    extract ``store["token"]["access_token"]``, then ``analyze_account``.
    Fail-soft → ``EMPTY`` on any failure (no token store, no access token,
    analysis error).

    No token refresh: the operator just authed, so the token is fresh; an
    expired/invalid token simply yields ``EMPTY`` (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_TOKEN_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        store = json.loads(raw.decode("utf-8"))
        token_node = store.get("token") if isinstance(store, dict) else None
        access_token = token_node.get("access_token") if isinstance(token_node, dict) else None
    except Exception:  # noqa: BLE001 — missing/unreadable/malformed store → EMPTY
        return EMPTY
    if not isinstance(access_token, str) or not access_token:
        return EMPTY
    return await analyze_account(access_token)
