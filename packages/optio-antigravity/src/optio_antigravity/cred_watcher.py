"""In-session credential save-back for antigravity seeds.

``agy`` authenticates against Google via OAuth with a **rotating refresh
token**: each refresh rotates the stored ``refresh_token`` in agy's token store.
That is the single-use-token failure mode optio-opencode's watcher was built for
and optio-grok reuses, so this module is a direct adaptation of
``optio_grok.cred_watcher`` (grok → antigravity renames; the credential store is
``<workdir>/home/.gemini/antigravity-cli/antigravity-oauth-token``, nested JSON
with a ``token.refresh_token`` — see ``seed_manifest``).

The watcher keeps the seed current by writing the changed in-session token store
back into the existing seed, plus a final backstop at teardown. It also renews
the seed's pool lease each tick and aborts the session on lease loss (a new
holder must never rotate the same token concurrently). The seed is the single
source of truth for credentials.

The watched relpath is derived from ``seed_manifest`` (``home_subdir`` +
``_TOKEN_STORE_RELPATH``) — the single source of truth for the token path — so
the watcher can never drift from what the seed manifest captures.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Callable

from optio_host.host import Host

from optio_agents import seeds
from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_CRED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
    _TOKEN_STORE_RELPATH,
    token_capture_is_valid,
)

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
# The rotating OAuth token store, relative to the task workdir. Derived from the
# seed manifest (SSOT for the token path) so the live file the watcher fingerprints
# is exactly the member the cred manifest captures — resolves to
# ``<workdir>/home/.gemini/antigravity-cli/antigravity-oauth-token``.
_CRED_RELPATH = f"{ANTIGRAVITY_CRED_MANIFEST.home_subdir}/{_TOKEN_STORE_RELPATH}"


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live token store, or None when it is missing, unparseable,
    or carries no usable OAuth ``refresh_token`` (nothing worth saving back).

    Guards against corrupting a seed with a half-written / logged-out store —
    the antigravity analog of grok's ``cred_fingerprint``. The validity gate is
    SSOT in ``seed_manifest.token_capture_is_valid``.
    """
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    if not token_capture_is_valid(raw):
        return None
    return hashlib.sha256(raw).hexdigest()


async def capture_gate_ok(host: Host) -> bool:
    """Gate for seed CAPTURE: a valid token store (non-empty ``refresh_token``)
    is present. Save-back uses ``cred_fingerprint`` directly; this is the
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
    """If the live token store differs from ``baseline`` and is valid, save it
    back into the seed and return the new fingerprint. Otherwise return
    ``baseline`` unchanged. Never raises — save-back is best-effort."""
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    try:
        await seeds.refresh_seed(
            ctx, host, seed_id=seed_id, manifest=ANTIGRAVITY_CRED_MANIFEST,
            suffix=ANTIGRAVITY_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: token store saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: token-store save-back failed", seed_id)
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
    """Poll every ``CRED_WATCH_INTERVAL_S``: save back the rotated token store,
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
                ctx._db, prefix=ctx._prefix, suffix=ANTIGRAVITY_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
