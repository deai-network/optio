"""Rewrite Claude Code's OAuth login redirect for headless/remote operators.

Claude Code's ``/login`` starts a loopback callback server and opens an
authorize URL with ``redirect_uri=http://localhost:<port>/callback``. That only
completes when the operator's browser shares a loopback with the agent (local
dev). For a remotely-driven agent (the operator's browser is elsewhere) the
loopback redirect dead-ends and login cannot finish.

Claude Code's bundle exchanges a *manually pasted* code against a DIFFERENT
``redirect_uri`` — its hosted ``MANUAL_REDIRECT_URL``
(``https://platform.claude.com/oauth/code/callback``). OAuth requires the
exchange's ``redirect_uri`` to equal the authorize's, so pasting a loopback
code fails (HTTP 400). Rewriting the authorize URL's ``redirect_uri`` to that
same hosted value makes claude.com show a reachable code page AND makes the
manual exchange line up — login completes and the seed is captured normally.

The rewrite is deliberately surgical: it only touches a Claude
``…/oauth/authorize`` URL whose ``redirect_uri`` is a ``localhost``/``127.0.0.1``
loopback ``/callback``. Every other ``BROWSER:`` URL the agent emits (docs, MCP
auth, anything else) is returned untouched.
"""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Claude Code's hosted manual-flow callback (the value its bundle uses as
# ``MANUAL_REDIRECT_URL`` for the paste-a-code exchange). Overridable in case
# Anthropic moves it.
MANUAL_REDIRECT_URL = os.environ.get(
    "OPTIO_CLAUDECODE_MANUAL_REDIRECT_URL",
    "https://platform.claude.com/oauth/code/callback",
)

# Only a loopback ``/callback`` qualifies — the exact shape Claude Code's
# ``/login`` emits for the auto (loopback) flow.
_LOOPBACK_CALLBACK = re.compile(r"^http://(?:localhost|127\.0\.0\.1):\d+/callback$")


def _is_loopback_callback(value: str) -> bool:
    return bool(_LOOPBACK_CALLBACK.match(value))


def rewrite_oauth_redirect(url: str) -> str:
    """Swap a Claude OAuth authorize URL's loopback ``redirect_uri`` for the
    hosted manual callback. Return ``url`` unchanged for anything else."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    # Must be an authorize endpoint. ``/cai/oauth/authorize`` and plain
    # ``/oauth/authorize`` both end with this.
    if not parts.path.endswith("/oauth/authorize"):
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k == "redirect_uri" and _is_loopback_callback(v) for k, v in pairs):
        return url
    new_pairs = [
        (k, MANUAL_REDIRECT_URL if (k == "redirect_uri" and _is_loopback_callback(v)) else v)
        for k, v in pairs
    ]
    new_query = urlencode(new_pairs)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )
