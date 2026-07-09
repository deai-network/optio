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


class UnsliceableSeed(Exception):
    """The seed's auth.json holds several providers but cannot be safely
    reduced to the one backing the configured default model."""


def _provider_of(model: str | None) -> str | None:
    """The provider id of a `provider/model` string, or None if it carries no
    `provider/` prefix."""
    if not model or "/" not in model:
        return None
    return model.split("/", 1)[0]


async def _read_json(host: Host, relpath: str) -> dict | None:
    path = f"{host.workdir.rstrip('/')}/{relpath}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def slim_auth_to_selected_provider(host: Host) -> bool:
    """Enforce one-provider-per-seed: prune the live auth.json to the single
    provider backing the configured default model (`small_model || model`),
    dropping the rest. Returns True if it rewrote auth.json, False on no-op
    (auth absent/invalid, or already one provider).

    Raises UnsliceableSeed when the seed cannot be reduced to one provider:
    the selected model has no `provider/` prefix, `model` and `small_model`
    resolve to different providers, or the selected provider is absent from
    auth.json. The caller decides what an un-sliceable seed means (capture
    refuses it; save-back leaves the seed untouched)."""
    auth = await _read_json(host, _CRED_RELPATH)
    if not auth:                      # missing/invalid/empty -> nothing to slim
        return False
    if len(auth) <= 1:                # already single-provider
        return False

    cfg = await _read_json(host, _MODEL_RELPATH) or {}
    selected = _provider_of(cfg.get("model"))
    if selected is None:
        raise UnsliceableSeed("no provider-qualified model in opencode.json")
    small = _provider_of(cfg.get("small_model"))
    if small is not None and small != selected:
        raise UnsliceableSeed(
            f"model provider {selected!r} != small_model provider {small!r}")
    if selected not in auth:
        raise UnsliceableSeed(
            f"selected provider {selected!r} not in auth.json {sorted(auth)}")

    dropped = sorted(k for k in auth if k != selected)
    await host.write_text(_CRED_RELPATH, json.dumps({selected: auth[selected]}))
    _LOG.info("slimmed seed auth to provider %r; dropped %s", selected, dropped)
    return True


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
    try:
        await slim_auth_to_selected_provider(host)
    except UnsliceableSeed as e:
        _LOG.warning("seed %s: save-back skipped, un-sliceable auth (%s)", seed_id, e)
        return baseline
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
