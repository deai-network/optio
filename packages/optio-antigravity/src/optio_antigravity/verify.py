"""Host-free antigravity seed verify + refresh via Google's OIDC token endpoint.

No ``agy`` process and no model inference: read the seed's token store and, when
the access token is expired (or a userinfo liveness check fails), perform a
standard OIDC ``refresh_token`` grant against Google's token endpoint, writing
the rotated tokens back into the seed. Mirrors optio-grok's ``verify.py`` (there:
xAI OIDC discovered from the seed's ``oidc_issuer``; here: Google — a fixed,
well-known issuer).

Token store (S1 spike, real interactive Google login 2026-07-06). agy writes its
OAuth state to ``.gemini/antigravity-cli/antigravity-oauth-token`` (imported from
``seed_manifest._TOKEN_STORE_RELPATH`` — the single source of truth for the path,
so verify and the capture manifest can never diverge). The file is NESTED JSON::

    {"auth_method": "consumer",
     "token": {"access_token": "...", "token_type": "Bearer",
               "refresh_token": "1//0...", "expiry": "2026-07-06T15:17:07.684178639+02:00"}}

The access/refresh tokens and ``expiry`` live under ``token`` (not top-level).
``expiry`` is an ISO-8601 string with NANOSECOND fractional seconds and a timezone
offset (Go's RFC3339Nano) — ``datetime.fromisoformat`` accepts at most microseconds,
so the fractional part is truncated to <=6 digits before parsing. There is no
``expiry_date`` epoch-millis field. A refresh rotates ``access_token`` (and *may*
rotate ``refresh_token``) and resets ``expiry``; ``auth_method`` and any other
outer keys are preserved verbatim on write-back.

OAuth client (S1): agy's authorize URL carries ``code_challenge`` +
``code_challenge_method=S256``, so agy is a **PUBLIC PKCE client**. A public-client
``refresh_token`` grant uses ``client_id`` ONLY — no ``client_secret`` is sent.
The client_id is ``_AGY_CLIENT_ID`` below.

Google OIDC facts (``accounts.google.com/.well-known/openid-configuration``):
``token_endpoint = https://oauth2.googleapis.com/token``; the ``refresh_token``
grant is supported.

Fail-closed: the seed is marked ``dead`` ONLY on a definitive ``invalid_grant``
(the refresh-token lineage is spent/revoked). Any other 4xx/5xx (``invalid_client``,
``invalid_request``, a server error), a network/discovery error, or a save-back
failure is inconclusive and NEVER retires a healthy seed. Host-free, non-billable.
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
from optio_agents.account import EMPTY, AccountInfo, accounts_to_metadata
from optio_antigravity.account import analyze_account
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_SEED_SUFFIX,
    _TOKEN_STORE_RELPATH as _TOKEN_MEMBER,
)

_LOG = logging.getLogger(__name__)

# Google is a fixed, well-known OIDC issuer (unlike grok, the seed does not carry
# an ``oidc_issuer`` — agy authenticates only against Google).
_GOOGLE_ISSUER = "https://accounts.google.com"

# agy's PUBLIC PKCE OAuth client. A PKCE public-client refresh grant is
# authenticated by ``client_id`` alone — there is NO client_secret to send (S1:
# the authorize URL uses code_challenge/S256, so agy is a public client).
_AGY_CLIENT_ID = (
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
)

_HTTP_TIMEOUT_S = 20
_USER_AGENT = "optio-antigravity-seed-verify/1"

# Sentinel: the refresh endpoint returned a definitive ``invalid_grant`` — the
# refresh-token lineage is spent/revoked → mark the seed dead. Distinct from
# ``None`` (a transport error or a non-invalid_grant HTTP error → inconclusive).
_DEAD = "__dead__"

# Go RFC3339Nano emits up to 9 fractional-second digits; Python's fromisoformat
# accepts at most 6 (microseconds). Truncate the excess before parsing.
_FRAC_RE = re.compile(r"(\.\d{6})\d+")


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
    token_endpoint: str, refresh_token: str, client_id: str,
) -> "dict | str | None":
    """OIDC refresh_token grant against Google as a PUBLIC PKCE client (client_id
    only, no client_secret). Returns the token response dict on success, ``_DEAD``
    on a definitive ``invalid_grant`` (dead lineage), or ``None`` on any other HTTP
    error (inconclusive) or a transport error."""
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
    except HTTPError as exc:
        # Retire the seed ONLY on a definitive invalid_grant. Any other 4xx/5xx
        # (invalid_client, invalid_request, a server error) is inconclusive — it
        # may signal an imperfect client/PKCE assumption, not a dead seed.
        try:
            err = json.loads(exc.read().decode("utf-8")).get("error")
        except (ValueError, UnicodeDecodeError, OSError, AttributeError):
            err = None
        return _DEAD if err == "invalid_grant" else None
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

def _token_node(store: dict) -> dict:
    """The nested credential node holding access_token / refresh_token / expiry.

    agy's real store nests them under ``token``; return that dict so callers read
    and mutate it in place (write-back preserves ``auth_method`` and any other
    outer keys). Fall back to the top-level dict defensively for a hypothetical
    future build that flattens the store."""
    tok = store.get("token")
    return tok if isinstance(tok, dict) else store


def _parse_expiry(value) -> "datetime | None":
    """Parse agy's ``expiry`` into an aware UTC datetime.

    Accepts the real ISO-8601 string (nanosecond-tolerant, offset-aware → converted
    to UTC) and, defensively, an epoch-millis/seconds number. None when unparseable
    (→ treated as expired, i.e. refresh)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str) and value.strip():
        s = _FRAC_RE.sub(r"\1", value.strip())  # RFC3339Nano → <=6 frac digits
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _read_token_store(blob_plain: bytes) -> "dict | None":
    """The agy OAuth store dict from the seed's token member, or None if
    absent/malformed. Shape: ``{"auth_method": ..., "token": {access_token,
    refresh_token, expiry, token_type}}`` (nested — see module docstring)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_TOKEN_MEMBER)
            if f is None:
                return None
            store = json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None
    return store if isinstance(store, dict) and store else None


# --- public API -------------------------------------------------------------

async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = ANTIGRAVITY_SEED_SUFFIX,
    seed_id: str,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> dict:
    """Verify an antigravity seed host-free and refresh its rotating OAuth token.

    Reads the seed's nested token store; if the access token is expired (or a
    userinfo liveness check fails) performs an OIDC ``refresh_token`` grant against
    Google's token endpoint (discovered from the fixed Google issuer) as a public
    PKCE client (client_id only) and writes the rotated ``access_token`` /
    ``refresh_token`` / ``expiry`` back into ``store["token"]`` (preserving
    ``auth_method`` and any other outer keys). Returns ``{"alive": bool,
    "accounts": list[AccountInfo]}`` — ``alive`` is True iff the seed is alive
    (token still valid, or a successful refresh); on the alive path ``accounts``
    is the normalized ``optio_agents.account.AccountInfo`` analyzed from the
    live/rotated access token, wrapped in a 1-element list (agy has a single
    Google account) and stamped into ``metadata.accounts``. A fail-soft
    ``EMPTY`` analysis wraps to ``[]``. Dead/inconclusive paths return
    ``accounts=[]``.

    Never raises for a dead seed. Marks pool status ``dead`` ONLY on a definitive
    dead signal (no refresh token, malformed store, or a ``invalid_grant`` refresh
    response); a transport/discovery failure or any non-invalid_grant HTTP error is
    inconclusive and leaves status untouched. No ``agy`` process, no model inference
    (mirrors optio-grok's verify.py).

    Call only on a FREE seed or one whose lease the caller holds: a refresh may
    rotate a single-use refresh token, stranding any live session on that seed.
    This function does not acquire or check leases.
    """
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "accounts": []}

    async def _finish(
        alive: bool, *, mark_dead: bool, accounts: "list[AccountInfo] | None" = None,
    ) -> dict:
        accounts = accounts or []
        now = datetime.now(timezone.utc)
        metadata: dict = {"verify": {"alive": alive, "checkedAt": now}}
        # Stamp the normalized account(s) only on the alive path, so the pool
        # consistently carries metadata.accounts for every live seed (mirrors
        # optio-codex). Dead paths carry accounts=[] and no stamp.
        if alive and accounts:
            metadata["accounts"] = accounts_to_metadata(accounts)
            metadata["accountsFetchedAt"] = now
        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id, metadata=metadata,
        )
        if alive:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="alive")
        elif mark_dead:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="dead")
        return {"alive": alive, "accounts": accounts if alive else []}

    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    store = _read_token_store(dec(buf.getvalue()))
    if store is None:
        return await _finish(False, mark_dead=True)

    tok = _token_node(store)
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        return await _finish(False, mark_dead=True)

    disco = await _in_executor(_discover_sync, _GOOGLE_ISSUER)
    if not isinstance(disco, dict) or not disco.get("token_endpoint"):
        _LOG.warning("seed %s: OIDC discovery failed (%s) — inconclusive", seed_id, _GOOGLE_ISSUER)
        return await _finish(False, mark_dead=False)
    token_endpoint = disco["token_endpoint"]
    userinfo_endpoint = disco.get("userinfo_endpoint")

    now = datetime.now(timezone.utc)
    exp = _parse_expiry(tok.get("expiry"))
    need_refresh = exp is None or exp <= now
    if not need_refresh and userinfo_endpoint:
        # Not expired: a cheap liveness check catches a revoked-but-unexpired
        # token; refresh only if it fails.
        if not await _in_executor(_validate_sync, userinfo_endpoint, tok.get("access_token") or ""):
            need_refresh = True
    if not need_refresh:
        # Alive without a refresh: analyze the still-valid access token for the
        # metadata.accounts stamp (read-only GET/POSTs; no extra refresh). Fail-soft
        # → EMPTY, never disturbs the verify result. agy has a single Google
        # account → wrap the analyzer result in a 1-element list (EMPTY → []).
        info = await analyze_account(tok.get("access_token") or "")
        accounts = [info] if info is not None and info != EMPTY else []
        return await _finish(True, mark_dead=False, accounts=accounts)

    resp = await _in_executor(
        _refresh_sync, token_endpoint, refresh_token, _AGY_CLIENT_ID,
    )
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # inconclusive

    tok["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):  # Google usually omits this; keep the old one
        tok["refresh_token"] = resp["refresh_token"]
    expires_in = resp.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        tok["expiry"] = (now + timedelta(seconds=expires_in)).isoformat()
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_TOKEN_MEMBER, content=json.dumps(store).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed token save-back failed", seed_id)
    # Analyze the freshly-rotated access token for the metadata.accounts stamp
    # (read-only; no extra refresh). Fail-soft → EMPTY. agy has a single Google
    # account → wrap the analyzer result in a 1-element list (EMPTY → []).
    info = await analyze_account(tok.get("access_token") or "")
    accounts = [info] if info is not None and info != EMPTY else []
    return await _finish(True, mark_dead=False, accounts=accounts)
