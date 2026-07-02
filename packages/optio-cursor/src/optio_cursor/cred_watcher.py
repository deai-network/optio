"""In-session credential save-back for cursor seeds.

Cursor authenticates via ``accessToken``/``refreshToken`` stored in
``<workdir>/home/.config/cursor/auth.json``. We treat the refresh token as
potentially rotating — the safe assumption, and the exact single-use-token
failure mode optio-opencode's watcher was built for — so this module is a
direct adaptation of ``optio_grok.cred_watcher`` (grok → cursor renames;
the credential path is ``<workdir>/home/.config/cursor/auth.json``).

The watcher keeps the seed current by writing the changed in-session
auth.json back into the existing seed, plus a final backstop at teardown. It
also renews the seed's pool lease each tick and aborts the session on lease
loss (a new holder must never rotate the same token concurrently). The seed
is the single source of truth for credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_host.host import Host

from optio_agents import seeds
from optio_cursor.seed_manifest import CURSOR_CRED_MANIFEST, CURSOR_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.config/cursor/auth.json"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live ``home/.config/cursor/auth.json``, or None when it
    is missing, unparseable, or an empty object (nothing worth saving back).

    Guards against corrupting a seed with a half-written / logged-out file —
    the cursor analog of opencode's provider-entry gate (``cursor-agent
    logout`` deletes the file; a crash mid-write leaves it unparseable).
    """
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
    """Gate for seed CAPTURE: a valid ``auth.json`` is present.

    Cursor, like grok and unlike opencode, has no separate model requirement
    (model selection is a per-invocation flag), so a valid credential is the
    whole gate. Save-back uses ``cred_fingerprint`` directly; this is the
    terminal capture gate (mirrors grok's ``capture_gate_ok``)."""
    return await cred_fingerprint(host) is not None


async def save_back_if_changed(
    ctx,
    host: Host,
    *,
    seed_id: str,
    baseline: str | None,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> str | None:
    """If the live auth.json differs from ``baseline`` and is valid, save it
    back into the seed and return the new fingerprint. Otherwise return
    ``baseline`` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=CURSOR_CRED_MANIFEST,
            suffix=CURSOR_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
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
    """Poll every ``CRED_WATCH_INTERVAL_S``: save back the rotated auth.json,
    and (when ``lease_holder`` is set) renew the seed's lease. If the lease is
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
                ctx._db, prefix=ctx._prefix, suffix=CURSOR_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
