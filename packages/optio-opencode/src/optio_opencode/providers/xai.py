"""xai provider handler — oauth reuses grok's map helper; api reads /v1/api-key.

opencode's xai-``oauth`` entry stores the same OAuth access token grok drives,
so we delegate to ``account_from_xai`` (userinfo + subscriptions). Imported at
module scope so tests can monkeypatch it here (patch-where-used).

An xai-``api`` entry is a raw api-key (``{"type": "api", "key": "xai-..."}``).
The only identity surface it can reach is ``GET https://api.x.ai/v1/api-key``
(Bearer <key>), returning the key's metadata — owning ``user_id``/``team_id``,
the key's label ``name``, ``acls``, and block flags — but no person name, no
email, no plan, no usage. We map ``user_id`` (fallback ``team_id``) into
``account_id`` and stash the whole body under ``raw``. The key's ``name`` field
is a key *label*, not a person, so it is deliberately NOT used as identity."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo
from optio_grok.account import account_from_xai

_LOG = logging.getLogger(__name__)

# Raw api-key identity surface (Bearer <key>, GET). Returns the key metadata;
# no name/email/plan/usage is reachable with an api-key credential.
_API_KEY_URL = "https://api.x.ai/v1/api-key"
_USER_AGENT = "optio-opencode/account (external)"
_HTTP_TIMEOUT_S = 15


def _get_api_key_sync(key: str) -> "dict | None":
    """Bearer-authed read-only GET of ``/v1/api-key`` → parsed JSON dict, or None
    on any HTTP/parse error (fail-soft)."""
    req = urllib.request.Request(
        _API_KEY_URL,
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


async def _fetch_api_key(key: str) -> "dict | None":
    """Async wrapper around the sync ``/v1/api-key`` GET (urllib in an executor).
    This is the monkeypatch seam the tests replace to avoid network."""
    return await asyncio.get_event_loop().run_in_executor(None, _get_api_key_sync, key)


async def _handle_api(entry: dict) -> "AccountInfo | None":
    """Map an xai ``api`` (raw api-key) entry → AccountInfo, or None. Fail-soft."""
    key = entry.get("key")
    if not key:
        return None
    try:
        body = await _fetch_api_key(key)
    except Exception:  # noqa: BLE001 — analysis must never break the meta-analyzer
        _LOG.exception("xai api-key account analysis failed")
        return None
    if not body:
        return None
    account_id = body.get("user_id") or body.get("team_id")
    if not account_id:
        return None
    # name/email/plan intentionally None (the api-key exposes no such identity);
    # windows empty (no usage surface). raw carries the full key metadata.
    return AccountInfo(account_id=account_id, raw={"api_key": body})


async def handle(entry: dict) -> "AccountInfo | None":
    """Map an xai auth entry → AccountInfo, or None (→ placeholder).

    Dispatches on ``type``: ``oauth`` delegates to grok's map helper, ``api``
    reads the raw key's metadata. Any other type declines. Fail-soft: never
    raises."""
    etype = entry.get("type")
    if etype == "api":
        return await _handle_api(entry)
    if etype != "oauth":
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
