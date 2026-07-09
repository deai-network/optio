"""Codex seed verify + refresh via OpenAI's OIDC token endpoint (host-free,
non-billable) — with the agent probe kept as a documented fallback.

Primary path: read the seed's ``auth.json`` and, for a ChatGPT-mode seed whose
token is stale (codex refreshes proactively after 8 days — TOKEN_REFRESH_INTERVAL),
perform a standard OIDC ``refresh_token`` grant against codex's hardcoded
refresh URL (_REFRESH_URL; NOT the OIDC discovery token_endpoint — see the facts
block below), writing the rotated tokens back into the seed. No codex process,
no model inference — mirrors optio-claudecode's direct-endpoint ``oauth.py`` and
optio-grok's ``verify.py`` (grok's discovery token_endpoint IS its refresh URL;
codex's is not — the one divergence in this path).

Fallback (codex-specific divergence from grok, which removed its probe): when
OIDC discovery is unreachable (no usable ``token_endpoint`` in the response —
used only to confirm reachability), fall back to the billable
agent probe (``codex exec --json … '<challenge>'``) — the previous behavior —
so a seed is still verifiable if the endpoint is unreachable.

OpenAI OIDC facts (pinned Task 0, 2026-07-03, codex-cli 0.142.5):
  issuer            = https://auth.openai.com
  discovery         = <issuer>/.well-known/openid-configuration
  discovery.token_endpoint = https://auth.openai.com/api/accounts/oauth/token
                      -- an account-management surface; NOT codex's refresh URL.
                      Used here ONLY as a reachability signal (discovery down
                      -> agent-probe fallback), never as the refresh endpoint.
  refresh_url       = https://auth.openai.com/oauth/token   (codex hardcodes
                      this; env override CODEX_REFRESH_TOKEN_URL_OVERRIDE)
  public client_id  = app_EMoamEEZ73f0CkXaXp7hrann   (login OAuth URL; no secret)
  auth.json shape   = {"OPENAI_API_KEY": null|str, "auth_mode": <str>,
                       "tokens": {"id_token","access_token","refresh_token",
                                  "account_id"} | null,
                       "last_refresh": <RFC3339 (nanosecond) / epoch>}
A refresh rotates tokens.access_token + tokens.refresh_token (+ tokens.id_token
if returned) and stamps last_refresh; account_id, auth_mode and OPENAI_API_KEY
are preserved (only tokens + last_refresh are mutated). API-key seeds
(OPENAI_API_KEY set, tokens null) carry no rotating token — alive-by-presence,
no refresh.

NOTE: endpoint/grant/public-client/shape are pinned above; the exact request
headers want one confirmation against a live seed (a wrong guess fails CLOSED:
a 4xx marks the seed dead; a network/discovery error is inconclusive and never
retires a healthy seed).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tarfile
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

from optio_host.paths import task_dir

from optio_agents import seeds
from optio_agents.account import EMPTY, AccountInfo
from optio_codex import host_actions
from optio_codex.account import analyze_account
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

_ISSUER = "https://auth.openai.com"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# Codex hardcodes its ChatGPT refresh URL (manager.rs) — it is NOT the OIDC
# discovery `token_endpoint` (…/api/accounts/oauth/token), which is a separate
# account-management surface. Honor codex's own env override.
_REFRESH_URL = os.environ.get(
    "CODEX_REFRESH_TOKEN_URL_OVERRIDE", "https://auth.openai.com/oauth/token"
)
_AUTH_RELPATH = "home/.codex/auth.json"
_AUTH_MEMBER = ".codex/auth.json"
_HTTP_TIMEOUT_S = 20
_USER_AGENT = "optio-codex-seed-verify/1"
# codex refreshes proactively after 8 days (manager.rs TOKEN_REFRESH_INTERVAL).
_REFRESH_AFTER = timedelta(days=8)

# Sentinel: the refresh endpoint returned a 4xx (invalid_grant) — the refresh
# token lineage is definitively spent/revoked → mark the seed dead. Distinct
# from ``None`` (a network/transport failure → inconclusive, never mark dead).
_DEAD = "__dead__"

# Agent-probe fallback (discovery unavailable) — the previous behavior. The
# answer token ("paris") must NOT appear in the prompt so a prompt-echoing error
# path can never false-positive.
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)


# --- synchronous HTTP (run in an executor; no host, no codex) ----------------

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


def _refresh_sync(refresh_url: str, refresh_token: str, client_id: str) -> "dict | str | None":
    """OIDC refresh_token grant against codex's hardcoded refresh URL (NOT the
    discovery token_endpoint — see module docstring). Returns the token response
    dict on success, ``_DEAD`` on a 4xx (dead lineage), or ``None`` on a
    transport error."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        refresh_url, data=body, method="POST",
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


async def _in_executor(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


# --- helpers -----------------------------------------------------------------

def _parse_last_refresh(value) -> "datetime | None":
    """Parse codex's ``last_refresh`` — an RFC3339 string (possibly ``Z`` /
    sub-second/nanosecond) or an epoch number. None when unparseable/absent (→
    treated as stale, i.e. refresh)."""
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


def _read_auth(blob_plain: bytes) -> "dict | None":
    """The codex auth.json dict from the seed tar, or None if absent/malformed."""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob_plain), mode="r:gz") as tar:
            f = tar.extractfile(_AUTH_MEMBER)
            if f is None:
                return None
            auth = json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError, UnicodeDecodeError):
        return None
    return auth if isinstance(auth, dict) else None


# --- public API --------------------------------------------------------------

async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = CODEX_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> dict:
    """Verify a codex seed host-free via OpenAI's OIDC token endpoint; refresh
    the rotating token in place. Falls back to the billable agent probe only
    when OIDC discovery is unavailable.

    Returns ``{"alive": bool, "account": AccountInfo | None}``: on the alive
    path ``account`` is the normalized ``optio_agents.account.AccountInfo``
    derived (fail-soft) from the seed's tokens and also stamped as
    ``metadata.account``; dead paths return ``account=None``. Never raises for a
    dead seed. Marks pool status ``dead`` ONLY on a definitive dead signal
    (no refresh token,
    malformed auth, or a 4xx invalid_grant); a transport/discovery failure is
    inconclusive and leaves status untouched. Call only on a FREE seed or one
    whose lease the caller holds (a refresh rotates the single-use token).
    """
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "account": None}

    async def _finish(
        alive: bool, *, mark_dead: bool, account: "AccountInfo | None" = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        metadata: dict = {"verify": {"alive": alive, "checkedAt": now}}
        # Stamp the normalized account only on the alive path (mirrors
        # claudecode). Fail-soft analysis may hand us EMPTY; stamp it anyway so
        # the pool consistently carries a metadata.account for every live seed.
        if alive and account is not None:
            metadata["account"] = account.to_dict()
            metadata["accountFetchedAt"] = now
        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id, metadata=metadata,
        )
        if alive:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="alive")
        elif mark_dead:
            await seeds.mark_seed_status(db, prefix=prefix, suffix=suffix, seed_id=seed_id, status="dead")
        return {"alive": alive, "account": account if alive else None}

    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    auth = _read_auth(dec(buf.getvalue()))
    if auth is None:
        return await _finish(False, mark_dead=True)

    tokens = auth.get("tokens")
    # API-key seed: no rotating token → alive by presence. No OAuth identity or
    # usage to fetch (research §1) → EMPTY account.
    if not tokens:
        if auth.get("OPENAI_API_KEY"):
            return await _finish(True, mark_dead=False, account=EMPTY)
        return await _finish(False, mark_dead=True)  # neither tokens nor key

    refresh_token = tokens.get("refresh_token") if isinstance(tokens, dict) else None
    if not refresh_token:
        return await _finish(False, mark_dead=True)

    # Discovery is a REACHABILITY gate only: if OpenAI's OIDC surface is
    # unreachable we fall back to the agent probe. We deliberately do NOT use
    # disco["token_endpoint"] as the refresh URL — for codex that is a different
    # (account-management) surface; codex refreshes against the hardcoded
    # _REFRESH_URL (see module docstring / Task 0 facts).
    disco = await _in_executor(_discover_sync, _ISSUER)
    if not isinstance(disco, dict) or not disco.get("token_endpoint"):
        _LOG.warning(
            "seed %s: OIDC discovery unavailable — falling back to the agent probe",
            seed_id,
        )
        return await _verify_via_probe(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id, ssh=ssh,
            install_dir=install_dir, encrypt=encrypt, decrypt=decrypt,
            finish=_finish,
        )

    now = datetime.now(timezone.utc)
    last = _parse_last_refresh(auth.get("last_refresh"))
    need_refresh = last is None or (now - last) >= _REFRESH_AFTER
    if not need_refresh:
        # Fresh (codex hasn't hit its proactive-refresh window) → trust alive,
        # do not rotate. (Codex tokens carry no cheap userinfo scope like grok;
        # freshness is the liveness signal — documented divergence.) Analyze the
        # (unrotated) tokens for the account stamp — read-only, no refresh.
        account = await analyze_account(tokens)
        return await _finish(True, mark_dead=False, account=account)

    resp = await _in_executor(_refresh_sync, _REFRESH_URL, refresh_token, _CLIENT_ID)
    if resp is _DEAD:
        return await _finish(False, mark_dead=True)
    if not isinstance(resp, dict) or not resp.get("access_token"):
        return await _finish(False, mark_dead=False)  # transport → inconclusive

    tokens["access_token"] = resp["access_token"]
    if resp.get("refresh_token"):
        tokens["refresh_token"] = resp["refresh_token"]
    if resp.get("id_token"):
        tokens["id_token"] = resp["id_token"]
    auth["tokens"] = tokens
    auth["last_refresh"] = now.isoformat().replace("+00:00", "Z")
    try:
        await seeds.overwrite_seed_member(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            member_path=_AUTH_MEMBER, content=json.dumps(auth).encode("utf-8"),
            encrypt=encrypt, decrypt=decrypt,
        )
    except Exception:  # noqa: BLE001 — save-back failed; the refresh still rotated
        _LOG.exception("seed %s: refreshed auth save-back failed", seed_id)
    # Analyze the freshly-rotated tokens for the account stamp (read-only GET;
    # the refresh above is verify's own, no EXTRA refresh here).
    account = await analyze_account(tokens)
    return await _finish(True, mark_dead=False, account=account)


async def _verify_via_probe(
    db, *, prefix, suffix, seed_id, ssh, install_dir, encrypt, decrypt, finish,
) -> dict:
    """Fallback: plant the seed and run one billable ``codex exec`` challenge
    probe; verdict from stdout, rotated auth.json saved back. The previous
    behavior, retained for when OIDC discovery is unavailable.

    The probe scrubs OPENAI_API_KEY from its environment
    (``host_actions._PROBE_SCRUB_ENV_KEYS``), so an ambient provider key on the
    verifying host cannot authenticate the probe via codex's API-key fallback
    and mask a dead ChatGPT-mode seed."""
    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-codex",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    try:
        await host.setup_workdir()
        codex_exec = await host_actions.resolve_codex(
            host, install_dir=install_dir, install_if_missing=False,
        )
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=CODEX_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )
        stdout, exit_code = await host_actions.run_codex_probe(
            host, codex_executable=codex_exec, prompt=PROBE_PROMPT,
        )
        # Verdict: stdout-only. The exit code carries zero verdict bits (answer
        # present proves the full chain regardless) — diagnostics only.
        alive = PROBE_ANSWER_RE.search(stdout) is not None
        if not alive:
            _LOG.info(
                "seed %s: probe dead (exit=%s, stdout[:200]=%r)",
                seed_id, exit_code, stdout[:200],
            )
        # Write back the (possibly rotated) auth.json — valid files only (tokens
        # or OPENAI_API_KEY non-null) — and analyze the read-back tokens for the
        # account stamp when alive (read-only GET; no extra refresh).
        workdir = host.workdir.rstrip("/")
        account: "AccountInfo | None" = None
        try:
            auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
            auth = json.loads(auth_raw.decode("utf-8"))
            if isinstance(auth, dict) and (
                auth.get("tokens") is not None or auth.get("OPENAI_API_KEY") is not None
            ):
                await seeds.overwrite_seed_member(
                    db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                    member_path=_AUTH_MEMBER, content=auth_raw,
                    encrypt=encrypt, decrypt=decrypt,
                )
            if alive and isinstance(auth, dict):
                tokens = auth.get("tokens")
                account = await analyze_account(tokens) if isinstance(tokens, dict) else EMPTY
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            _LOG.warning("seed %s: no valid auth.json after probe; skipping write-back", seed_id)
        # Probe failure is a definitive dead signal (the seed's own creds were
        # exercised end-to-end), so mark_dead=True here (unlike a transport error).
        return await finish(alive, mark_dead=not alive, account=account)
    finally:
        try:
            await host.cleanup_taskdir(aggressive=True)
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: host.disconnect failed")
