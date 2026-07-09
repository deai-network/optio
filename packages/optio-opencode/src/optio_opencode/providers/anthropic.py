"""anthropic provider handler — reuses claudecode's OAuth map helper.

opencode's anthropic-oauth entry stores the same shape of OAuth access token
claudecode drives, so we delegate to ``account_from_oauth_token`` rather than
reimplement the profile+usage fetch. Imported at module scope so tests can
monkeypatch it here (patch-where-used)."""

from __future__ import annotations

import logging

from optio_agents.account import EMPTY, AccountInfo
from optio_claudecode.account import account_from_oauth_token

_LOG = logging.getLogger(__name__)


async def handle(entry: dict) -> "AccountInfo | None":
    """Map an anthropic auth entry → AccountInfo, or None (→ placeholder).

    This phase handles ``oauth`` only; ``api`` (raw keys expose ~no account)
    and any other type decline. Fail-soft: never raises."""
    if entry.get("type") != "oauth":
        return None
    access = entry.get("access")
    if not access:
        return None
    try:
        info = await account_from_oauth_token(access)
    except Exception:  # noqa: BLE001 — analysis must never break the meta-analyzer
        _LOG.exception("anthropic account analysis failed")
        return None
    return info if info != EMPTY else None
