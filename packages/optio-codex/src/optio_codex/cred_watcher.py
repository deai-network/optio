"""In-session credential save-back for codex seeds.

Codex's ChatGPT-mode ``auth.json`` holds a **single-use rotating refresh
token** (``tokens.refresh_token``): the manager proactively refreshes after
8 days (``TOKEN_REFRESH_INTERVAL``, manager.rs) and on any 401, rewriting
auth.json in place — and a used refresh token invalidates every other copy
(openai/codex#15410, by design). That is the exact failure mode
optio-opencode's watcher was built for and optio-grok ported; this module
is the codex adaptation (credential path ``<workdir>/home/.codex/auth.json``).
OpenAI's own CI/CD guidance is the same restore -> run -> persist pattern.

The watcher keeps the seed current by writing the changed in-session
auth.json back into the existing seed, plus a final backstop at teardown.
It also renews the seed's pool lease each tick and aborts the session on
lease loss (a new holder must never rotate the same token concurrently).
The seed is the single source of truth for credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable

from optio_host.host import Host

from optio_agents import seeds
from optio_codex.seed_manifest import CODEX_CRED_MANIFEST, CODEX_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

CRED_WATCH_INTERVAL_S = 10.0
_CRED_RELPATH = "home/.codex/auth.json"


def _auth_valid(data: object) -> bool:
    """True iff ``data`` is one of codex's two live auth shapes: ChatGPT
    mode (``tokens`` non-null) or API-key mode (``OPENAI_API_KEY``
    non-null). A logged-out ``{}``/null-tokens file is invalid — saving it
    back would clobber a good seed."""
    if not isinstance(data, dict) or not data:
        return False
    return data.get("tokens") is not None or data.get("OPENAI_API_KEY") is not None


async def cred_fingerprint(host: Host) -> str | None:
    """SHA-256 of the live ``home/.codex/auth.json``, or None when it is
    missing, unparseable, or logged-out (nothing worth saving back).

    Guards against corrupting a seed with a half-written / logged-out file —
    the codex analog of opencode's provider-entry gate, tightened to codex's
    two documented auth shapes (tokens / OPENAI_API_KEY).
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
    if not _auth_valid(data):
        return None
    return hashlib.sha256(raw).hexdigest()


async def capture_gate_ok(host: Host) -> bool:
    """Gate for seed CAPTURE: a valid ``auth.json`` is present.

    Codex, like grok, has no separate model requirement (the model lives in
    ``config.toml`` and is optional), so a valid credential is the whole
    gate. Save-back uses ``cred_fingerprint`` directly; this is the terminal
    capture gate."""
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
            ctx, host, seed_id=seed_id, manifest=CODEX_CRED_MANIFEST,
            suffix=CODEX_SEED_SUFFIX, encrypt=encrypt, decrypt=decrypt,
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
    and (when ``lease_holder`` is set) renew the seed's lease. If the lease
    is lost, signal the session to stop (set the cancellation flag) and exit
    — continuing would mean a token-rotation collision with the new holder.

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
                ctx._db, prefix=ctx._prefix, suffix=CODEX_SEED_SUFFIX,
                seed_id=seed_id, holder=lease_holder,
            )
            if not ok:
                _LOG.warning("seed %s: lease lost; aborting session", seed_id)
                ctx.cancellation_flag.set()
                return
