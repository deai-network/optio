"""Best-effort Kimi Code account summary + usage/limit status for seeded logins.

The per-engine ``analyze_account`` seam every wrapper implements: a live OAuth
bearer in, a vendor-agnostic ``optio_agents.account.AccountInfo`` out. Fail-soft:
any error (missing field, HTTP/parse error, expired token) yields ``EMPTY`` --
account analysis is informational (it feeds the ``on_seed_saved`` summary + a
stamped ``metadata.account``), never load-bearing, so a failure here must not
disturb seed capture, verify, or launch.

kimicode divergence from the JWT-decode engines (codex/cursor): identity AND
usage both ride a single read-only GET -- ``https://api.kimi.com/coding/v1/usages``
(``Authorization: Bearer <access_token>``). There is NO dedicated identity
endpoint (``/coding/v1/{auth,me,user,users/me}`` all 404); ``user.userId`` (the
account id) and ``user.membership.level`` (the plan tier) are bundled INTO the
usages body. name/email are NOT exposed anywhere, so ``AccountInfo.summary``
stays ``None`` even though plan + account_id are populated.

Real-payload quirks (from docs/2026-07-09-kimicode-account-research.md, pinned
against a live 200 capture -- see fixtures/kimi_coding_usages.json):

  * counts are JSON STRINGS (``"100"``), not ints -> ``int()``-coerce;
  * ``used`` is present ONLY on the top-level ``usage`` -- for each ``limits[]``
    row derive ``used = limit - remaining``;
  * the reset field is ``resetTime`` (camelCase, microsecond precision);
  * ``window.timeUnit`` is an enum string (``TIME_UNIT_MINUTE``) -> synthesize a
    compact window label (``300 MINUTE`` -> ``"5h"``);
  * usage is account-wide, so every ``UsageWindow.model`` is ``None``.

No token refresh: the analyzer uses the stored token as-is; an expired/invalid
token simply yields ``EMPTY`` (never a refresh -- kimi's rotating refresh token
is single-use, and refreshing without seed save-back would strand the seed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from optio_agents.account import EMPTY, AccountInfo, UsageWindow

_LOG = logging.getLogger(__name__)

# The one read-only usage/limits (+ partial identity) endpoint. Read-only,
# non-billable -- the same GET the kimi TUI polls.
_USAGE_URL = "https://api.kimi.com/coding/v1/usages"
# A plain UA authorizes the GET fine -- no X-Msh-* device headers needed.
_USER_AGENT = "optio-kimicode/account"
_HTTP_TIMEOUT_S = 15

# Live-workdir credentials (isolated HOME=<workdir>/home, creds dir
# ``credentials``) -- the file kimi itself writes, still on disk at capture time
# (pre-cleanup). Mirrors verify's ``_CRED_MEMBER`` rooted at the workdir.
_CRED_RELPATH = "home/credentials/kimi-code.json"

# One TIME_UNIT_* enum -> seconds, for synthesizing a window label.
_UNIT_SECONDS = {
    "SECOND": 1,
    "MINUTE": 60,
    "HOUR": 3600,
    "DAY": 86400,
    "WEEK": 604800,
}


# --- synchronous HTTP (run in an executor; no host, no kimi) ----------------


def _usage_sync(token: str) -> "dict | None":
    """GET the usages window source. Fail-soft -> None on any error."""
    req = urllib.request.Request(
        _USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
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


async def _fetch(token: str) -> "dict | None":
    """Async wrapper around the sync usages fetcher (urllib in an executor).
    This is the monkeypatch seam the tests replace to avoid network."""
    return await asyncio.get_event_loop().run_in_executor(None, _usage_sync, token)


# --- vendor payload -> normalized AccountInfo -------------------------------


def _int(x) -> "int | None":
    """Tolerate kimi's stringified counts (``"100"``) and bare ints; None when
    unparseable."""
    if isinstance(x, bool):
        return None
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def _prettify_plan(level) -> "str | None":
    """``user.membership.level`` -> display name. ``"LEVEL_BASIC"`` -> ``"Basic"``
    (strip the ``LEVEL_`` enum prefix, title-case). None when absent/blank."""
    if not isinstance(level, str) or not level.strip():
        return None
    tail = level.strip().removeprefix("LEVEL_")
    if not tail:
        return None
    return tail.replace("_", " ").title()


def _parse_reset(ra) -> "datetime | None":
    """kimi's ``resetTime`` (ISO-8601, ``Z``, microsecond precision) -> tz-aware
    datetime; None if absent/unparseable. Tolerates >6 fractional digits by
    trimming to microseconds."""
    if not isinstance(ra, str) or not ra.strip():
        return None
    s = ra.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # nanoseconds -> microseconds
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _synth_label(duration, unit) -> "str | None":
    """``duration`` + ``timeUnit`` enum -> compact window label. ``300`` +
    ``TIME_UNIT_MINUTE`` (= 18000 s) -> ``"5h"``. None when unresolvable."""
    d = _int(duration)
    u = str(unit or "").removeprefix("TIME_UNIT_")
    secs = _UNIT_SECONDS.get(u)
    if d is None or d <= 0 or secs is None:
        return None
    total = d * secs
    for divisor, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if total % divisor == 0:
            return f"{total // divisor}{suffix}"
    return f"{total}s"


def _window(label: str, row) -> "UsageWindow | None":
    """One usage/limit row (``{limit, [used], remaining, resetTime}`` with string
    counts) -> UsageWindow. ``used`` is derived from ``limit - remaining`` when
    absent (the ``limits[]`` rows omit it). None when there is no usable
    limit/used pair. Every window is account-wide -> ``model=None``."""
    if not isinstance(row, dict):
        return None
    limit = _int(row.get("limit"))
    if not limit or limit <= 0:
        return None
    used = _int(row.get("used"))
    if used is None:
        rem = _int(row.get("remaining"))
        used = (limit - rem) if rem is not None else None
    if used is None:
        return None
    return UsageWindow(
        label=label,
        pct=100.0 * used / limit,
        resets_at=_parse_reset(row.get("resetTime")),
        model=None,
    )


def _windows_from_usage(body: dict) -> list[UsageWindow]:
    """Build windows from the top-level ``usage`` (account quota) + each
    ``limits[].detail`` (rolling sub-window). Null/unusable rows are skipped."""
    out: list[UsageWindow] = []
    w = _window("Account quota", body.get("usage"))
    if w is not None:
        out.append(w)
    for i, entry in enumerate(body.get("limits") or []):
        if not isinstance(entry, dict):
            continue
        win = entry.get("window") if isinstance(entry.get("window"), dict) else {}
        label = _synth_label(win.get("duration"), win.get("timeUnit")) or f"Limit #{i + 1}"
        w = _window(label, entry.get("detail"))
        if w is not None:
            out.append(w)
    return out


def _info_from(body: dict) -> AccountInfo:
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    membership = user.get("membership") if isinstance(user.get("membership"), dict) else {}
    return AccountInfo(
        name=None,   # kimi exposes no name field anywhere
        email=None,  # kimi exposes no email field anywhere -> summary stays None
        plan=_prettify_plan(membership.get("level")),
        account_id=user.get("userId") or None,
        windows=tuple(_windows_from_usage(body)),
        raw={"usage": body},
    )


async def analyze_account(token: str) -> AccountInfo:
    """Best-effort kimicode ``AccountInfo`` from a live session access token.
    One read-only ``/coding/v1/usages`` GET; identity (``account_id``/``plan``)
    and usage windows both come from its body. Never raises -> ``EMPTY`` on any
    failure (blank/expired token, HTTP/parse error, unexpected shape). No token
    refresh."""
    try:
        if not isinstance(token, str) or not token:
            return EMPTY
        body = await _fetch(token)
        if not isinstance(body, dict):
            return EMPTY
        return _info_from(body)
    except Exception:  # noqa: BLE001 -- fail-soft, never disturbs the caller
        return EMPTY


async def resolve_capture_account(host) -> AccountInfo:
    """Live-host capture variant: read the isolated HOME's ``kimi-code.json``
    ``access_token``, then ``analyze_account``. Fail-soft -> ``EMPTY`` on any
    failure (no creds file, no token, analysis error).

    No token refresh: the operator just authed, so the token is fresh; an
    expired/invalid token simply yields ``EMPTY`` (analysis fail-soft)."""
    path = f"{host.workdir.rstrip('/')}/{_CRED_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
        token = data.get("access_token")
    except Exception:  # noqa: BLE001 -- missing/unreadable/malformed creds -> EMPTY
        return EMPTY
    if not isinstance(token, str) or not token:
        return EMPTY
    return await analyze_account(token)
