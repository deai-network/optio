"""Host-free Claude Code OAuth + seed verification.

Works from a decrypted seed blob (no session host): validate / refresh-and-
save-back / fetch account + usage / stamp raw results as seed metadata. Reused
by the excavator `gimme` provider (per checkout) and the verify-free action.

OAuth facts verified 2026-06-05 (see the seed-maintenance specs): refresh tokens
rotate single-use; all calls need the claude-cli User-Agent.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from optio_agents import seeds

from optio_claudecode.account import format_account_summary
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
USER_AGENT = "claude-cli/2.1.165 (external, cli)"
_BETA = "oauth-2025-04-20"

_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_VALIDATE_URL = "https://platform.claude.com/api/oauth/validate"
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"

_CRED_MEMBER = ".claude/.credentials.json"


def _req(url, *, method, access_token=None, body=None):
    headers = {"User-Agent": USER_AGENT, "anthropic-beta": _BETA}
    data = None
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    return urllib.request.Request(url, headers=headers, data=data, method=method)


def _validate_sync(access_token: str) -> bool:
    try:
        with urllib.request.urlopen(
            _req(_VALIDATE_URL, method="POST", access_token=access_token, body={}), timeout=15,
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("valid"))
    except HTTPError:
        return False
    except (URLError, OSError, ValueError):
        return False


def _usage_sync(access_token: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            _req(_USAGE_URL, method="GET", access_token=access_token), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


def _refresh_sync(refresh_token: str) -> dict | None:
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    try:
        with urllib.request.urlopen(
            _req(_TOKEN_URL, method="POST", body=body), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError:
        return None  # invalid_grant / 4xx -> dead lineage
    except (URLError, OSError, ValueError):
        return None


def _profile_sync(access_token: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            _req(_PROFILE_URL, method="GET", access_token=access_token), timeout=15,
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


async def validate_token(access_token: str) -> bool:
    return await _in_executor(_validate_sync, access_token)


async def fetch_usage(access_token: str) -> dict | None:
    return await _in_executor(_usage_sync, access_token)


async def refresh_oauth_token(refresh_token: str) -> dict | None:
    return await _in_executor(_refresh_sync, refresh_token)


async def summarize_profile(access_token: str) -> dict | None:
    profile = await _in_executor(_profile_sync, access_token)
    if not isinstance(profile, dict):
        return None
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    uuid = account.get("uuid")
    return {"uuid": uuid, "summary": format_account_summary(profile)}


def _read_seed_creds(blob_plain: bytes) -> dict | None:
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_CRED_MEMBER)
            if f is None:
                return None
            return json.loads(f.read().decode("utf-8")).get("claudeAiOauth")
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None


def _build_creds_json(oauth: dict, token_resp: dict) -> bytes:
    """New .credentials.json bytes from a refresh response, preserving scopes/
    subscription where the response omits them."""
    new = dict(oauth)
    new["accessToken"] = token_resp["access_token"]
    new["refreshToken"] = token_resp["refresh_token"]
    expires_in = token_resp.get("expires_in") or 0
    # server clock not available here; expiry is advisory (claude re-checks).
    new["expiresAt"] = int(datetime.now(timezone.utc).timestamp() * 1000) + expires_in * 1000
    if token_resp.get("scope"):
        new["scopes"] = token_resp["scope"].split()
    return json.dumps({"claudeAiOauth": new}).encode("utf-8")


async def verify_and_refresh_seed(
    db, *, prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id, encrypt, decrypt,
) -> dict:
    """Verify a seed host-free; refresh + save back if needed; stamp raw usage +
    account as metadata. Returns {alive, usage, account}. Never raises for a
    dead/limited seed -- a dead lineage is alive=False."""
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "usage": None, "account": None}
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    oauth = _read_seed_creds(dec(buf.getvalue()))
    if not oauth or not oauth.get("refreshToken"):
        return {"alive": False, "usage": None, "account": None}

    access = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt") or 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    need_refresh = expires_at <= now_ms
    if not need_refresh:
        need_refresh = not await validate_token(access)

    if need_refresh:
        resp = await refresh_oauth_token(oauth["refreshToken"])
        if resp is None:
            return {"alive": False, "usage": None, "account": None}
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_CRED_MEMBER, content=_build_creds_json(oauth, resp),
            encrypt=encrypt, decrypt=decrypt,
        )
        access = resp["access_token"]

    usage = await fetch_usage(access)
    account = await summarize_profile(access)
    await seeds.declare_metadata(
        db, prefix=prefix, suffix=suffix, seed_id=seed_id,
        metadata={
            "usage": usage,
            "usageFetchedAt": datetime.now(timezone.utc),
            "account": account,
        },
    )
    return {"alive": True, "usage": usage, "account": account}


def usage_limited(usage: dict | None, now, models_required: list[str] | None = None) -> bool:
    """True if a relevant usage bucket is maxed (utilization >= 100) and its
    window has not reset yet (resets_at in the future). `now` is a timezone-aware
    datetime. Gates global five_hour + seven_day always, plus seven_day_<model>
    for each model in `models_required`. `usage` is the raw /api/oauth/usage
    JSON (utilization is a percentage 0-100)."""
    from datetime import datetime

    if not isinstance(usage, dict):
        return False
    keys = ["five_hour", "seven_day"]
    for m in models_required or []:
        keys.append(f"seven_day_{m}")
    for k in keys:
        bucket = usage.get(k)
        if not isinstance(bucket, dict):
            continue
        util = bucket.get("utilization")
        if not isinstance(util, (int, float)) or util < 100:
            continue
        resets_at = bucket.get("resets_at")
        if not resets_at:
            return True  # maxed, no reset time -> treat as limited
        try:
            reset_dt = datetime.fromisoformat(resets_at)
        except (ValueError, TypeError):
            return True
        if reset_dt > now:
            return True
    return False
