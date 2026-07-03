"""Host-free kimi seed verify + refresh via the kimi OAuth token endpoint.

No kimi process and no model inference (non-billable): read the seed's
``credentials/kimi-code.json``, and when the access token is within kimi's
dynamic refresh threshold perform a ``refresh_token`` grant against the kimi
OAuth token endpoint, writing the rotated tokens back into the seed. Mirrors
optio-grok's ``verify.py`` — but grok discovers its OIDC endpoints from the
seed's ``oidc_issuer``; kimi ships **no** ``.well-known/openid-configuration``,
so the token endpoint and public ``client_id`` are HARDCODED here (with a
``KIMI_CODE_OAUTH_HOST`` / ``KIMI_OAUTH_HOST`` env override, matching the CLI's
``packages/oauth/src/constants.ts``).

kimi OAuth facts (verified from ``.kimi-src/kimi-code/packages/oauth/src``,
2026-07-03):

* Token endpoint: ``POST {host}/api/oauth/token`` form-encoded, public client
  (``client_id`` only, no secret). Default host ``https://auth.kimi.com``.
* Refresh grant body: ``client_id`` + ``grant_type=refresh_token`` +
  ``refresh_token``. Response carries ``access_token`` / ``refresh_token`` /
  ``expires_in`` (+ ``scope`` / ``token_type``); the refresh token is
  **single-use and rotates** on every response.
* On-disk creds (``kimi-code.json``) is a FLAT snake_case dict:
  ``access_token`` / ``refresh_token`` / ``expires_at`` (unix SECONDS) /
  ``scope`` / ``token_type`` / ``expires_in``. ``expires_at`` is computed
  client-side as ``floor(now) + expires_in``.
* Refresh threshold (``oauth-manager.ts`` ``defaultRefreshThreshold`` /
  ``shouldRefreshToken``): refresh when remaining life <
  ``max(300, 0.5 * expires_in)``; a token with ``expires_at == 0`` is a revoked
  tombstone (never refreshed).
* Failure classification (``oauth.ts`` ``refreshAccessToken``): HTTP 401/403 or
  an ``invalid_grant`` error body is a hard unauthorized (spent/revoked lineage);
  429/5xx and transport errors are retryable/transient.

Fail-closed status: an ``invalid_grant`` / 401 / 403 marks the seed **dead**; a
transport error or any other HTTP failure is **inconclusive** and never retires a
possibly-healthy seed. Unlike grok there is NO userinfo liveness probe (kimi
exposes none), so a token outside the refresh threshold is confirmed alive
without any network call.

NOTE (tracked follow-up): a real-seed live-refresh confirmation — one opt-in
``refresh_token`` grant against ``auth.kimi.com`` proving the request shape
round-trips end-to-end — is deferred; this module is validated here only against
mocked HTTP.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tarfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

from optio_agents import seeds
from optio_kimicode.seed_manifest import KIMI_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# Creds member inside the seed tar (rooted at the manifest's ``home_subdir``):
# KIMI_CODE_HOME = <workdir>/home, creds dir ``credentials`` → this member.
_CRED_MEMBER = "credentials/kimi-code.json"
_HTTP_TIMEOUT_S = 30
_USER_AGENT = "optio-kimicode-seed-verify/1"

# Hardcoded public OAuth client (no discovery — kimi ships no OIDC metadata).
# Mirrors ``packages/oauth/src/constants.ts``.
_DEFAULT_OAUTH_HOST = "https://auth.kimi.com"
_TOKEN_PATH = "/api/oauth/token"
_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"

# Dynamic refresh threshold (``oauth-manager.ts``): remaining life below
# ``max(MIN, RATIO * expires_in)`` triggers a refresh.
_MIN_REFRESH_THRESHOLD_S = 300.0
_REFRESH_THRESHOLD_RATIO = 0.5

# Sentinel: the refresh endpoint returned a definitive unauthorized (401/403 or
# an ``invalid_grant`` body) — the refresh-token lineage is spent/revoked → mark
# the seed dead. Distinct from ``None`` (transport / other HTTP failure →
# inconclusive, never mark dead).
_DEAD = "__dead__"


# --- endpoint resolution ----------------------------------------------------

def _oauth_host() -> str:
    return (
        os.environ.get("KIMI_CODE_OAUTH_HOST")
        or os.environ.get("KIMI_OAUTH_HOST")
        or _DEFAULT_OAUTH_HOST
    )


def _token_endpoint() -> str:
    return _oauth_host().rstrip("/") + _TOKEN_PATH


# --- synchronous HTTP (run in an executor; no host, no kimi) ----------------

def _refresh_sync(token_endpoint: str, refresh_token: str, client_id: str) -> "dict | str | None":
    """kimi ``refresh_token`` grant. Returns the token response dict on success,
    ``_DEAD`` on a definitive unauthorized (401/403 or ``invalid_grant``), or
    ``None`` on a transport error / any other HTTP failure (inconclusive)."""
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
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
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) and data.get("access_token") else None
    except HTTPError as e:
        # 401/403 or an ``invalid_grant`` error body → dead lineage. Every other
        # HTTP status (400 without invalid_grant, 429, 5xx) is inconclusive.
        err = ""
        try:
            payload = json.loads(e.read().decode("utf-8"))
            if isinstance(payload, dict):
                err = payload.get("error") or ""
        except (ValueError, OSError, UnicodeDecodeError):
            pass
        if getattr(e, "code", None) in (401, 403) or err == "invalid_grant":
            return _DEAD
        return None
    except (URLError, OSError, ValueError):
        return None  # transport / decode → inconclusive


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


# --- helpers ----------------------------------------------------------------

def _refresh_threshold(expires_in) -> float:
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return max(_MIN_REFRESH_THRESHOLD_S, expires_in * _REFRESH_THRESHOLD_RATIO)
    return _MIN_REFRESH_THRESHOLD_S


def _parse_expiry(value) -> "datetime | None":
    """Parse kimi's ``expires_at`` — a unix-SECONDS number (tolerating a numeric
    string). None when unparseable (→ treated as expired, i.e. refresh)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromtimestamp(float(value.strip()), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    return None


def _read_creds(blob_plain: bytes) -> "dict | None":
    """The flat token dict from the seed's ``credentials/kimi-code.json``, or
    None if absent/malformed. Unlike grok's ``auth.json`` there is no
    ``{issuer::client: {...}}`` wrapper — kimi's creds file is the token dict."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_CRED_MEMBER)
            if f is None:
                return None
            creds = json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None
    return creds if isinstance(creds, dict) and creds else None


# --- public API -------------------------------------------------------------

async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = KIMI_SEED_SUFFIX,
    seed_id: str,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> bool:
    """Verify a kimi seed host-free and refresh its rotating token in place.

    Reads the seed's ``kimi-code.json``; if the access token is within kimi's
    dynamic refresh threshold (remaining life < ``max(300, 0.5*expires_in)``)
    performs a ``refresh_token`` grant against the (hardcoded, env-overridable)
    kimi OAuth token endpoint and writes the rotated
    ``access_token``/``refresh_token``/``expires_at`` back into the seed.
    Returns True iff the seed is alive (token outside the threshold, or a
    successful refresh).

    Never raises for a dead seed. Marks pool status ``dead`` ONLY on a definitive
    dead signal (no refresh token, malformed creds, or a 401/403/``invalid_grant``
    refresh rejection); a transport or other transient HTTP failure is
    inconclusive and leaves status untouched. No kimi process, no model
    inference, non-billable.

    Call only on a FREE seed or one whose lease the caller holds: a refresh
    rotates the single-use refresh token, stranding any live session on that
    seed. This function does not acquire or check leases.
    """
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return False

    async def _finish(alive: bool, *, mark_dead: bool) -> bool:
        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {"alive": alive, "checkedAt": datetime.now(timezone.utc)}},
        )
        if alive:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="alive")
        elif mark_dead:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="dead")
        return alive

    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    creds = _read_creds(dec(buf.getvalue()))
    if creds is None:
        return await _finish(False, mark_dead=True)

    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        # No (or empty) refresh token — includes kimi's revoked tombstone
        # (``access_token``/``refresh_token`` emptied). Definitively dead.
        return await _finish(False, mark_dead=True)

    now = datetime.now(timezone.utc)
    exp = _parse_expiry(creds.get("expires_at"))
    if exp is None:
        need_refresh = True
    else:
        remaining = (exp - now).total_seconds()
        need_refresh = remaining < _refresh_threshold(creds.get("expires_in"))
    if not need_refresh:
        # Outside the refresh window → confirmed alive. kimi exposes no userinfo
        # liveness endpoint, so there is nothing further to probe host-free.
        return await _finish(True, mark_dead=False)

    resp = await _in_executor(_refresh_sync, _token_endpoint(), refresh_token, _CLIENT_ID)
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # transport → inconclusive

    creds["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):
        creds["refresh_token"] = resp["refresh_token"]
    expires_in = resp.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
        creds["expires_in"] = expires_in
        creds["expires_at"] = int(now.timestamp()) + int(expires_in)
    if isinstance(resp.get("scope"), str):
        creds["scope"] = resp["scope"]
    if isinstance(resp.get("token_type"), str):
        creds["token_type"] = resp["token_type"]
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_CRED_MEMBER, content=json.dumps(creds).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed creds save-back failed", seed_id)
    return await _finish(True, mark_dead=False)
