"""deepseek provider handler — api-key balance → AccountInfo.

DeepSeek is an api-key-only provider with no identity endpoint: the account's
only distinguishing surface is its prepaid balance. So this handler fetches
``GET https://api.deepseek.com/user/balance`` (Bearer <key>) and maps the first
``balance_infos`` row into ``plan`` — ``"DeepSeek · <total> <currency>"`` — so
the pool popover shows the balance as the account label. Identity-less
(name/email/account_id all None), no usage windows.

The urllib GET runs in an executor via the ``_fetch`` seam (mirroring the reuse
handlers' vendor helpers); tests monkeypatch ``_fetch`` — never the network.
Fail-soft throughout: any decline/error → None so the meta-analyzer emits a
placeholder instead of dropping the provider."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from urllib.error import HTTPError, URLError

from optio_agents.account import AccountInfo

_LOG = logging.getLogger(__name__)

_BALANCE_URL = "https://api.deepseek.com/user/balance"
_USER_AGENT = "optio-opencode/account (external)"
_HTTP_TIMEOUT_S = 15


# --- synchronous HTTP (run in an executor; no host) --------------------------


def _get_sync(url: str, key: str) -> "dict | None":
    """Bearer-authed read-only GET → parsed JSON dict, or None on any HTTP/parse
    error (fail-soft)."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (HTTPError, URLError, OSError, ValueError):
        return None


async def _fetch(key: str) -> "dict | None":
    """Async wrapper around the sync Bearer GET (urllib in an executor). This is
    the monkeypatch seam the tests replace to avoid network."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _get_sync, _BALANCE_URL, key
    )


async def handle(entry: dict) -> "AccountInfo | None":
    """Map a deepseek auth entry → AccountInfo, or None (→ placeholder).

    Handles ``api`` only (deepseek has no oauth). Fail-soft: never raises. None
    when the type is not ``api``, the key is missing, the fetch fails, or the
    balance list is empty."""
    if entry.get("type") != "api":
        return None
    key = entry.get("key")
    if not key:
        return None
    try:
        body = await _fetch(key)
    except Exception:  # noqa: BLE001 — analysis must never break the meta-analyzer
        _LOG.exception("deepseek account analysis failed")
        return None
    if not isinstance(body, dict):
        return None
    infos = body.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        return None
    first = infos[0]
    total_balance = first.get("total_balance")
    currency = first.get("currency")
    return AccountInfo(
        plan=f"DeepSeek · {total_balance} {currency}",
        windows=(),
        raw={"balance": body},
    )
