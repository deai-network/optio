"""In-session credential save-back for opencode seeds.

OAuth providers with rotating refresh tokens (xAI, OpenAI/Codex) make
refresh tokens single-use: opencode's plugin loader() refreshes a token on
use, the provider rotates the refresh token, and opencode persists the
rotated pair to auth.json (best-effort). This watcher keeps the seed
current by writing the changed in-session auth.json back into the existing
seed, plus a final backstop at teardown. Provider-agnostic: opencode does
the refreshing; the watcher only persists the file.

The seed is the single source of truth for credentials; see
docs/2026-06-11-opencode-seed-save-back-design.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_agents import seeds
from optio_host.host import Host

from optio_opencode.seed_manifest import OPENCODE_CRED_MANIFEST, OPENCODE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.local/share/opencode/auth.json"
_MODEL_RELPATH = "home/.config/opencode/opencode.json"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live auth.json, or None when it is missing,
    unparseable, or carries no provider entry (i.e. nothing worth saving
    back). The multi-provider analog of claudecode's refresh-token gate —
    guards against corrupting a seed with a half-written/logged-out file."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    return hashlib.sha256(raw).hexdigest()


async def capture_gate_ok(host: Host) -> bool:
    """Stricter gate for seed CAPTURE: valid auth.json (cred_fingerprint)
    AND a non-empty `model` in the live opencode.json. A model-less seed is
    unusable — a consuming task gets no default and verify has nothing to
    probe. Save-back deliberately does NOT use this gate: save-back only
    replaces auth.json (the seed's opencode.json is untouched), and blocking
    it over an unrelated field would drop a rotated refresh token."""
    if await cred_fingerprint(host) is None:
        return False
    path = f"{host.workdir.rstrip('/')}/{_MODEL_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        cfg = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(cfg, dict) and bool(cfg.get("model"))


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live auth.json differs from `baseline` and is valid, save it
    back into the seed and return the new fingerprint. Otherwise return
    `baseline` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=OPENCODE_CRED_MANIFEST,
            suffix=OPENCODE_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: auth.json saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: auth.json save-back failed", seed_id)
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
    """Poll every CRED_WATCH_INTERVAL_S: save back rotated auth.json, and
    (when `lease_holder` is set) renew the seed's lease. If the lease is
    lost, signal the session to stop (set the cancellation flag) and exit —
    continuing would mean a token-rotation collision with the new holder.
    Runs until cancelled. Best-effort save-back; lease-loss is decisive."""
    current = baseline
    while True:
        await asyncio.sleep(CRED_WATCH_INTERVAL_S)
        current = await save_back_if_changed(
            ctx, host, seed_id=seed_id, baseline=current,
            encrypt=encrypt, decrypt=decrypt,
        )
        if lease_holder is not None:
            ok = await seeds.renew_lease(
                ctx._db, prefix=ctx._prefix, suffix=OPENCODE_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
