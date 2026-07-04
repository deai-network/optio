"""In-session credential save-back for kimi seeds.

kimi authenticates against ``auth.kimi.com`` with a **rotating single-use
refresh token**: each token refresh rewrites
``<KIMI_CODE_HOME>/credentials/kimi-code.json`` with a fresh ``refresh_token``
(and ``access_token``), and the old refresh token dies server-side on use. That
is the exact single-use-token failure mode the seed watcher exists for, so this
module is a direct adaptation of ``optio_grok.cred_watcher`` (grok's rotating
``home/.grok/auth.json`` → kimi's ``home/credentials/kimi-code.json``).

The watcher keeps the seed current by writing the changed in-session
``kimi-code.json`` back into the existing seed — via
``seeds.overwrite_seed_member`` (host-free, single-member overwrite: the seed
carries exactly this one credential file) — plus a final backstop at teardown.
It also renews the seed's pool lease each tick and aborts the session on lease
loss (a new holder must never rotate the same token concurrently). The seed is
the single source of truth for credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_host.host import Host

from optio_agents import seeds
from optio_kimicode.seed_manifest import KIMI_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0

# The rotating credential file, relative to the workdir. KIMI_CODE_HOME is
# ``<workdir>/home`` (verified from kimi source), so the file lives at
# ``home/credentials/kimi-code.json``.
_CRED_RELPATH = "home/credentials/kimi-code.json"

# Path of that file INSIDE the seed tar. The kimi seed manifest roots capture at
# ``home_subdir="home"`` and includes the ``credentials`` dir, so the tar member
# is ``credentials/kimi-code.json`` — the member overwrite_seed_member replaces.
_SEED_MEMBER_PATH = "credentials/kimi-code.json"


async def _read_cred_bytes(host: Host) -> bytes | None:
    """Raw bytes of the live ``home/credentials/kimi-code.json``, or None when it
    is missing, unparseable, or carries no non-empty ``refresh_token``.

    A file without a refresh token is a login-less / half-written / logged-out
    identity: a seed built from it is dead on arrival (nothing to refresh with),
    so it must gate out of BOTH capture and save-back. Mirrors claudecode's cred
    gate (which requires ``claudeAiOauth.refreshToken``) — the kimi analog of
    grok's auth.json gate."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return None
    return raw


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live ``kimi-code.json``, or None when it is missing /
    unparseable / carries no non-empty ``refresh_token``."""
    raw = await _read_cred_bytes(host)
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


async def capture_gate_ok(host: Host) -> bool:
    """Gate for seed CAPTURE: a ``kimi-code.json`` with a usable ``refresh_token``
    is present — never seed a login-less / half-written identity.

    kimi keeps no separate config member in the identity seed (the model is a
    launch flag, not persisted auth), so a valid credential is the whole gate.
    Mirrors grok's terminal capture gate + claudecode's refresh-token gate."""
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
    """If the live ``kimi-code.json`` differs from ``baseline`` and is valid,
    overwrite the seed's credential member with it and return the new
    fingerprint. Otherwise return ``baseline`` unchanged. Never raises —
    save-back is best-effort.

    The write goes through ``seeds.overwrite_seed_member`` (host-free: it takes
    the raw file bytes and rewrites GridFS directly), using ``encrypt``/
    ``decrypt`` for the seed blob (Task 3.0's ``session_blob_*`` callables)."""
    raw = await _read_cred_bytes(host)
    if raw is None:
        return baseline
    fp = hashlib.sha256(raw).hexdigest()
    if fp == baseline:
        return baseline
    try:
        await seeds.overwrite_seed_member(
            ctx._db, prefix=ctx._prefix, suffix=KIMI_SEED_SUFFIX,
            seed_id=seed_id, member_path=_SEED_MEMBER_PATH, content=raw,
            encrypt=encrypt, decrypt=decrypt,
        )
        _LOG.info("seed %s: kimi-code.json saved back", seed_id)
        return fp
    except Exception:
        _LOG.exception("seed %s: kimi-code.json save-back failed", seed_id)
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
    """Poll every ``CRED_WATCH_INTERVAL_S``: save back the rotated
    ``kimi-code.json``, and (when ``lease_holder`` is set) renew the seed's
    lease. If the lease is lost, signal the session to stop (set the
    cancellation flag) and exit — continuing would mean a token-rotation
    collision with the new holder.

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
                ctx._db, prefix=ctx._prefix, suffix=KIMI_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
