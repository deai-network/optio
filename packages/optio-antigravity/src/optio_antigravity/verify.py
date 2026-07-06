"""Host-free antigravity seed verify + refresh via Google's OIDC token endpoint.

No ``agy`` process and no model inference: read the seed's token store
(``.gemini/oauth_creds.json``), and when the access token is expired (or a
userinfo liveness check fails) perform a standard OIDC ``refresh_token`` grant
against Google's token endpoint, writing the rotated tokens back into the seed.
Mirrors optio-grok's ``verify.py`` (there: xAI OIDC discovered from the seed's
``oidc_issuer``; here: Google — a fixed, well-known issuer).

Google OIDC facts (``accounts.google.com/.well-known/openid-configuration``):
``token_endpoint = https://oauth2.googleapis.com/token``; the ``refresh_token``
grant is supported. Google's installed-app clients are **public** but the grant
still requires the (non-secret) ``client_id`` + ``client_secret`` pair baked into
the CLI. The seed's token store is agy's flat Google ``oauth_creds.json``
(``access_token`` / ``refresh_token`` / ``expiry_date`` epoch-millis / ``scope`` /
``id_token`` / ``token_type``); a refresh rotates ``access_token`` (and *may*
rotate ``refresh_token``) and resets ``expiry_date``. Identity/scope fields are
unchanged by a refresh, so they are preserved verbatim.

Fail-closed: a 4xx (``invalid_grant``) marks the seed dead; a network/discovery
error is inconclusive and NEVER retires a healthy seed. Host-free, non-billable.

============================================================================
TODO(S1): the real credential-location spike (plan Task S1) has NOT run yet.
Every credential assumption below is the design's **likely-outcome** (§2 option 1:
a keyring file-fallback OAuth token store at ``.gemini/oauth_creds.json``) and is
flagged for reconciliation once S1 records the empirical login diff:
  * the token-store relpath + flat Google shape (``_TOKEN_MEMBER``);
  * that Google (fixed issuer) is the OIDC provider (``_GOOGLE_ISSUER``);
  * the public CLI ``client_id`` / ``client_secret`` — the store does not carry
    them, so we fall back to the well-known Gemini-CLI public values
    (``_GEMINI_CLI_CLIENT_ID`` / ``_GEMINI_CLI_CLIENT_SECRET``); S1 must confirm
    antigravity uses the same OAuth client (record the real id/secret there).
The fail-closed *contract* (dead only on a definitive dead signal; inconclusive
on transport) is invariant across the S1 outcomes.
============================================================================
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

from optio_agents import seeds
from optio_antigravity.seed_manifest import ANTIGRAVITY_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# The seed's token store (in-tar path; home_subdir="home" is stripped at capture
# and re-added at extract, mirroring the seed manifest's include relpath).
# TODO(S1): reconcile the relpath/shape with the real-login spike.
_TOKEN_MEMBER = ".gemini/oauth_creds.json"

# Google is a fixed, well-known OIDC issuer (unlike grok, the seed does not carry
# an ``oidc_issuer`` — agy authenticates only against Google). TODO(S1): confirm.
_GOOGLE_ISSUER = "https://accounts.google.com"

# The public Gemini-CLI OAuth client (a Google "installed app": the client_secret
# is published, not confidential). The flat ``oauth_creds.json`` does not carry
# these, so they are the fallback when the store omits them.
# TODO(S1): confirm antigravity shares this OAuth client; record the real values.
_GEMINI_CLI_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
_GEMINI_CLI_CLIENT_SECRET = "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"

_HTTP_TIMEOUT_S = 20
_USER_AGENT = "optio-antigravity-seed-verify/1"

# Sentinel: the refresh endpoint returned a 4xx (invalid_grant) — the refresh
# token lineage is definitively spent/revoked → mark the seed dead. Distinct from
# ``None`` (a network/transport failure → inconclusive, never mark dead).
_DEAD = "__dead__"


# --- synchronous HTTP (run in an executor; no host, no agy) -----------------

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


def _refresh_sync(
    token_endpoint: str, refresh_token: str, client_id: str, client_secret: str,
) -> "dict | str | None":
    """OIDC refresh_token grant against Google. Returns the token response dict on
    success, ``_DEAD`` on a 4xx (dead lineage), or ``None`` on a transport error."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
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
    """Parse Google's ``expiry_date`` — epoch milliseconds (an int/float, the
    google-auth default) or, defensively, an epoch-seconds number. None when
    unparseable (→ treated as expired, i.e. refresh)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    return None


def _read_token_store(blob_plain: bytes) -> "dict | None":
    """The flat Google OAuth creds dict from the seed's token store, or None if
    absent/malformed. Shape: ``{ access_token, refresh_token, expiry_date, ... }``
    (unlike grok's ``{issuer::client: creds}`` wrapper)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_TOKEN_MEMBER)
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
    suffix: str = ANTIGRAVITY_SEED_SUFFIX,
    seed_id: str,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> bool:
    """Verify an antigravity seed host-free and refresh its rotating OAuth token.

    Reads the seed's ``oauth_creds.json``; if the access token is expired (or a
    userinfo liveness check fails) performs an OIDC ``refresh_token`` grant against
    Google's token endpoint (discovered from the fixed Google issuer) and writes
    the rotated ``access_token``/``refresh_token``/``expiry_date`` back into the
    seed. Returns True iff the seed is alive (token still valid, or a successful
    refresh).

    Never raises for a dead seed. Marks pool status ``dead`` ONLY on a definitive
    dead signal (no refresh token, malformed store, or a 4xx invalid_grant); a
    transport/discovery failure is inconclusive and leaves status untouched. No
    ``agy`` process, no model inference (mirrors optio-grok's verify.py).

    Call only on a FREE seed or one whose lease the caller holds: a refresh may
    rotate a single-use refresh token, stranding any live session on that seed.
    This function does not acquire or check leases.
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
    creds = _read_token_store(dec(buf.getvalue()))
    if creds is None:
        return await _finish(False, mark_dead=True)

    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        return await _finish(False, mark_dead=True)

    # TODO(S1): the store does not carry client_id/secret — fall back to the
    # public Gemini-CLI OAuth client. S1 must confirm antigravity shares it.
    client_id = creds.get("client_id") or _GEMINI_CLI_CLIENT_ID
    client_secret = creds.get("client_secret") or _GEMINI_CLI_CLIENT_SECRET

    disco = await _in_executor(_discover_sync, _GOOGLE_ISSUER)
    if not isinstance(disco, dict) or not disco.get("token_endpoint"):
        _LOG.warning("seed %s: OIDC discovery failed (%s) — inconclusive", seed_id, _GOOGLE_ISSUER)
        return await _finish(False, mark_dead=False)
    token_endpoint = disco["token_endpoint"]
    userinfo_endpoint = disco.get("userinfo_endpoint")

    now = datetime.now(timezone.utc)
    exp = _parse_expiry(creds.get("expiry_date"))
    need_refresh = exp is None or exp <= now
    if not need_refresh and userinfo_endpoint:
        # Not expired: a cheap liveness check catches a revoked-but-unexpired
        # token; refresh only if it fails.
        if not await _in_executor(_validate_sync, userinfo_endpoint, creds.get("access_token") or ""):
            need_refresh = True
    if not need_refresh:
        return await _finish(True, mark_dead=False)

    resp = await _in_executor(
        _refresh_sync, token_endpoint, refresh_token, client_id, client_secret,
    )
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # transport → inconclusive

    creds["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):  # Google usually omits this; keep the old one
        creds["refresh_token"] = resp["refresh_token"]
    expires_in = resp.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        creds["expiry_date"] = int((now.timestamp() + expires_in) * 1000)
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_TOKEN_MEMBER, content=json.dumps(creds).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed token save-back failed", seed_id)
    return await _finish(True, mark_dead=False)
