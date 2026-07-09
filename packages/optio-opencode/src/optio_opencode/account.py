"""opencode account meta-analyzer.

opencode is a model-selection TUI over third-party providers: a seed's
``auth.json`` holds credentials for whichever provider(s) the operator logged
into. So an opencode seed's "account" is really **one account per configured
provider**. Unlike the single-vendor wrappers, opencode needs a meta-analyzer:
dispatch each provider entry to its handler (which reuses the vendor map
helpers), aggregate into a ``list[AccountInfo]``.

Every configured provider yields exactly one ``AccountInfo`` — an analyzed one
when a handler resolves it, else a placeholder (``"Unknown account · <provider>"``)
so nothing silently disappears from the pool popover. Fail-soft throughout: a
provider handler never breaks the others; empty/unreadable auth → ``[]``.
"""

from __future__ import annotations

import asyncio
import json
import logging

from optio_agents.account import EMPTY, AccountInfo
from optio_opencode.providers import anthropic, openai, xai

_LOG = logging.getLogger(__name__)

# models.dev provider id → per-provider handler. Providers absent here (the
# ~146 remaining) yield a placeholder AccountInfo.
_REGISTRY = {
    "anthropic": anthropic.handle,
    "openai": openai.handle,
    "xai": xai.handle,
}

_AUTH_RELPATH = "home/.local/share/opencode/auth.json"


def _placeholder(provider_id: str, entry: dict) -> AccountInfo:
    """A stand-in for a provider we do not (yet) analyze. ``plan`` is set to
    ``"Unknown account · <provider>"`` so the frame's plain ``summary`` property
    renders ``"Plan: Unknown account · <provider>"`` — no special-casing in the
    frame required."""
    return AccountInfo(
        account_id=entry.get("accountId"),
        plan=f"Unknown account · {provider_id}",
        raw={"provider": provider_id, "unanalyzed": True},
    )


async def analyze_accounts(auth: dict) -> "list[AccountInfo]":
    """Map an opencode ``auth.json`` dict → one ``AccountInfo`` per provider.

    For each ``provider_id -> entry``: skip non-dict entries; dispatch to the
    registered handler (fail-soft); append the analyzed account, or a
    placeholder when there is no handler / it declines / it fails / it returns
    the ``EMPTY`` sentinel. Empty or non-dict auth → ``[]``."""
    if not isinstance(auth, dict):
        return []
    items = [(pid, e) for pid, e in auth.items() if isinstance(e, dict)]

    async def _one(provider_id: str, entry: dict) -> AccountInfo:
        handler = _REGISTRY.get(provider_id)
        info: AccountInfo | None = None
        if handler is not None:
            try:
                info = await handler(entry)
            except Exception:  # noqa: BLE001 — one bad provider never drops the rest
                _LOG.exception("opencode provider handler failed: %s", provider_id)
                info = None
        if info is not None and info != EMPTY:
            return info
        return _placeholder(provider_id, entry)

    # Providers analyzed CONCURRENTLY — each hits a different vendor with its own
    # (up to 15s) HTTP timeout, so a multi-provider seed must not pay the SUM of
    # those; the verify/verify-free RPC deadline can't absorb it (see the
    # asyncio.wait_for budget in verify.py).
    return list(await asyncio.gather(*(_one(pid, e) for pid, e in items)))


async def resolve_capture_accounts(host) -> "list[AccountInfo]":
    """Live-host capture variant: read the isolated HOME's ``auth.json`` and
    analyze it. Fail-soft → ``[]`` on any read/parse failure."""
    path = f"{host.workdir.rstrip('/')}/{_AUTH_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        auth = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 — missing/unreadable/malformed auth → []
        return []
    return await analyze_accounts(auth)
