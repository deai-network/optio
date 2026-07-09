"""openai provider handler — reuses codex's OAuth map helper.

opencode's openai-oauth entry stores ``{access, accountId}`` with **no**
``id_token``, so identity comes from codex's ``account_from_openai`` (which
fetches ``GET /backend-api/me`` rather than decoding an id_token). Imported at
module scope so tests can monkeypatch it here (patch-where-used)."""

from __future__ import annotations

import logging

from optio_agents.account import EMPTY, AccountInfo
from optio_codex.account import account_from_openai

_LOG = logging.getLogger(__name__)


async def handle(entry: dict) -> "AccountInfo | None":
    """Map an openai auth entry → AccountInfo, or None (→ placeholder).

    This phase handles ``oauth`` only (raw ``api`` keys are admin-gated for
    account/usage). Fail-soft: never raises."""
    if entry.get("type") != "oauth":
        return None
    access = entry.get("access")
    if not access:
        return None
    try:
        info = await account_from_openai(access, entry.get("accountId"))
    except Exception:  # noqa: BLE001 — analysis must never break the meta-analyzer
        _LOG.exception("openai account analysis failed")
        return None
    return info if info != EMPTY else None
