"""xai provider handler — reuses grok's OAuth map helper.

opencode's xai-oauth entry stores the same OAuth access token grok drives, so
we delegate to ``account_from_xai`` (userinfo + subscriptions). Imported at
module scope so tests can monkeypatch it here (patch-where-used)."""

from __future__ import annotations

import logging

from optio_agents.account import EMPTY, AccountInfo
from optio_grok.account import account_from_xai

_LOG = logging.getLogger(__name__)


async def handle(entry: dict) -> "AccountInfo | None":
    """Map an xai auth entry → AccountInfo, or None (→ placeholder).

    This phase handles ``oauth`` only (the ``api`` key branch lands with the
    Phase-3 handlers). Fail-soft: never raises."""
    if entry.get("type") != "oauth":
        return None
    access = entry.get("access")
    if not access:
        return None
    try:
        info = await account_from_xai(access)
    except Exception:  # noqa: BLE001 — analysis must never break the meta-analyzer
        _LOG.exception("xai account analysis failed")
        return None
    return info if info != EMPTY else None
