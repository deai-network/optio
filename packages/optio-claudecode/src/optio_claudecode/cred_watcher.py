"""In-session credential save-back for claudecode seeds.

Claude Code OAuth refresh tokens rotate (single-use): each refresh issues a new
token and invalidates the old. This watcher keeps the seed current by writing
refreshed credentials back into the existing seed whenever the in-session
`.claude/.credentials.json` changes, plus a final backstop at teardown.

The seed is the single source of truth for credentials; see
docs/superpowers/specs/2026-06-05-claudecode-seed-saveback-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_agents import seeds
from optio_host.host import Host

from optio_claudecode.seed_manifest import CLAUDE_CRED_MANIFEST, CLAUDE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.claude/.credentials.json"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live credentials file, or None when it is missing,
    unparseable, or carries no non-empty refresh token (i.e. nothing worth
    saving back). Guards against corrupting a seed with logged-out/half-written
    credentials."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        token = data["claudeAiOauth"]["refreshToken"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        return None
    if not token:
        return None
    return hashlib.sha256(raw).hexdigest()


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live credentials differ from `baseline` and are valid, save them
    back into the seed and return the new fingerprint. Otherwise return
    `baseline` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=CLAUDE_CRED_MANIFEST,
            suffix=CLAUDE_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: credentials saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: credential save-back failed", seed_id)
        return baseline


async def run_credential_watcher(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
    lease_holder: str | None = None,
) -> None:
    """Poll every CRED_WATCH_INTERVAL_S: save back rotated creds, and (when
    `lease_holder` is set) renew the seed's lease. If the lease is lost, signal
    the session to stop (set the cancellation flag) and exit. Runs until
    cancelled. Best-effort save-back; lease-loss is decisive."""
    current = baseline
    while True:
        await asyncio.sleep(CRED_WATCH_INTERVAL_S)
        current = await save_back_if_changed(
            ctx, host, seed_id=seed_id, baseline=current,
            encrypt=encrypt, decrypt=decrypt,
        )
        if lease_holder is not None:
            ok = await seeds.renew_lease(
                ctx._db, prefix=ctx._prefix, suffix=CLAUDE_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
