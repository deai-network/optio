"""Host-free grok seed verify + refresh via the xAI OIDC token endpoint.

No grok process and no model inference: read the seed's ``auth.json``, and when
the access token is expired (or a userinfo liveness check fails) perform a
standard OIDC ``refresh_token`` grant against xAI's token endpoint, writing the
rotated tokens back into the seed. Mirrors optio-claudecode's direct-endpoint
``oauth.py`` (there: Anthropic; here: xAI OIDC discovered from the seed's
``oidc_issuer``).

xAI OIDC facts (``auth.x.ai/.well-known/openid-configuration``, 2026-07-03):
``token_endpoint = https://auth.x.ai/oauth2/token``; ``refresh_token`` grant is
supported; the CLI is a **public client** (``token_endpoint_auth_method`` includes
``none`` → ``client_id`` only, no secret). The seed's ``key`` is the **access
token** (its JWT carries a ``scope`` claim, which id-tokens do not); a refresh
rotates ``key`` + ``refresh_token`` and resets ``expires_at``. Identity fields
(``user_id``/``email``/``principal_*``/``team_id``/``oidc_*``) are unchanged by a
refresh, so they are preserved verbatim.

CONFIRMED end-to-end against a live seed on 2026-07-03: OIDC discovery, a userinfo
validate, and a real ``refresh_token`` grant (form-encoded, public client,
``client_id`` only) all succeed; the endpoint returns
``access_token``/``refresh_token``/``expires_in`` (access tokens live ~6h) and
``key``←``access_token`` round-trips + saves back. A malformed request would fail
CLOSED anyway (a 4xx marks the seed dead; a network error is inconclusive and never
retires a healthy seed).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

from optio_agents import seeds
from optio_grok.seed_manifest import GROK_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

_AUTH_MEMBER = ".grok/auth.json"
_HTTP_TIMEOUT_S = 20
_USER_AGENT = "optio-grok-seed-verify/1"

# Sentinel: the refresh endpoint returned a 4xx (invalid_grant) — the refresh
# token lineage is definitively spent/revoked → mark the seed dead. Distinct
# from ``None`` (a network/transport failure → inconclusive, never mark dead).
_DEAD = "__dead__"


# --- synchronous HTTP (run in an executor; no host, no grok) ----------------

def _discover_sync(issuer: str) -> "dict | None":
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


def _refresh_sync(token_endpoint: str, refresh_token: str, client_id: str) -> "dict | str | None":
    """OIDC refresh_token grant. Returns the token response dict on success,
    ``_DEAD`` on a 4xx (dead lineage), or ``None`` on a transport error."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        token_endpoint, data=body, method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError:
        return _DEAD  # invalid_grant / 4xx → the refresh token is spent
    except (URLError, OSError, ValueError):
        return None  # network/transport → inconclusive


def _validate_sync(userinfo_endpoint: str, access_token: str) -> bool:
    req = urllib.request.Request(
        userinfo_endpoint, method="GET",
        headers={"User-Agent": _USER_AGENT, "Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (HTTPError, URLError, OSError, ValueError):
        return False


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


# --- helpers ----------------------------------------------------------------

def _parse_expiry(value) -> "datetime | None":
    """Parse grok's ``expires_at`` — an RFC3339 string (possibly nanosecond
    precision + ``Z``) or an epoch number. None when unparseable (→ treated as
    expired, i.e. refresh)."""
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # nanoseconds → microseconds
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _read_auth(blob_plain: bytes) -> "tuple[dict, str, dict] | None":
    """(full auth dict, top key, creds) from the seed's .grok/auth.json, or None
    if absent/malformed. Shape: ``{ "<issuer>::<client>": { ...creds... } }``."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_AUTH_MEMBER)
            if f is None:
                return None
            auth = json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(auth, dict) or not auth:
        return None
    top = next(iter(auth))
    creds = auth[top]
    return (auth, top, creds) if isinstance(creds, dict) else None


# --- public API -------------------------------------------------------------

async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = GROK_SEED_SUFFIX,
    seed_id: str,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> dict:
    """Verify a grok seed host-free and refresh its rotating OIDC token in place.

    Reads the seed's ``auth.json``; if the access token is expired (or a userinfo
    liveness check fails) performs an OIDC ``refresh_token`` grant against the xAI
    token endpoint (discovered from the seed's ``oidc_issuer``) and writes the
    rotated ``key``/``refresh_token``/``expires_at`` back into the seed. Returns
    {alive, account} where alive is True iff the seed is alive (token still valid,
    or a successful refresh) and account is always None (no analyze_account yet).

    Never raises for a dead seed. Marks pool status ``dead`` ONLY on a definitive
    dead signal (no refresh token, malformed auth, or a 4xx invalid_grant);
    a transport/discovery failure is inconclusive and leaves status untouched.
    No grok process, no model inference (mirrors optio-claudecode's oauth.py).

    Call only on a FREE seed or one whose lease the caller holds: a refresh
    rotates the single-use refresh token, stranding any live session on that
    seed. This function does not acquire or check leases.
    """
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "account": None}

    async def _finish(alive: bool, *, mark_dead: bool) -> dict:
        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {"alive": alive, "checkedAt": datetime.now(timezone.utc)}},
        )
        if alive:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="alive")
        elif mark_dead:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="dead")
        return {"alive": alive, "account": None}

    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    parsed = _read_auth(dec(buf.getvalue()))
    if parsed is None:
        return await _finish(False, mark_dead=True)
    auth, top, creds = parsed

    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        return await _finish(False, mark_dead=True)

    issuer = creds.get("oidc_issuer") or (top.split("::", 1)[0] if "::" in top else None)
    client_id = creds.get("oidc_client_id") or (top.split("::", 1)[1] if "::" in top else None)
    if not issuer or not client_id:
        return await _finish(False, mark_dead=True)

    disco = await _in_executor(_discover_sync, issuer)
    if not isinstance(disco, dict) or not disco.get("token_endpoint"):
        _LOG.warning("seed %s: OIDC discovery failed (%s) — inconclusive", seed_id, issuer)
        return await _finish(False, mark_dead=False)
    token_endpoint = disco["token_endpoint"]
    userinfo_endpoint = disco.get("userinfo_endpoint")

    now = datetime.now(timezone.utc)
    exp = _parse_expiry(creds.get("expires_at"))
    need_refresh = exp is None or exp <= now
    if not need_refresh and userinfo_endpoint:
        # Not expired: a cheap liveness check catches a revoked-but-unexpired
        # token; refresh only if it fails.
        if not await _in_executor(_validate_sync, userinfo_endpoint, creds.get("key") or ""):
            need_refresh = True
    if not need_refresh:
        return await _finish(True, mark_dead=False)

    resp = await _in_executor(_refresh_sync, token_endpoint, refresh_token, client_id)
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # transport → inconclusive

    creds["key"] = resp["access_token"]
    if resp.get("refresh_token"):
        creds["refresh_token"] = resp["refresh_token"]
    expires_in = resp.get("expires_in")
    if isinstance(expires_in, (int, float)):
        creds["expires_at"] = (now + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
    auth[top] = creds
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_AUTH_MEMBER, content=json.dumps(auth).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed auth save-back failed", seed_id)
    return await _finish(True, mark_dead=False)
