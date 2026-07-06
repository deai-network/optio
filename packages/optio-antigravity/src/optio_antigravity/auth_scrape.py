"""Surface agy's print-only login URL as a clean, copyable ``BROWSER:`` link.

agy authenticates with a hosted-redirect OAuth flow that PRINTS the Google
authorize URL into the TUI ("Open the URL below in your browser:") and never
shells out to a browser — so the redirect browser-shims never fire (an empty
``optio.log`` on the login step is the tell). Worse, agy HARD-wraps the long URL
to the box width, so ttyd linkifies only the first line and the operator can't
copy it in one piece.

This module reads the tmux pane (the ttyd surface's backing terminal),
reassembles the wrapped OAuth URL, and appends a ``BROWSER: "<url>"`` marker to
``optio.log`` — the same channel the redirect shim uses, so the log-protocol
driver parses it and calls ``ctx.request_browser_open`` (a single clean link in
the dashboard). Best-effort and idempotent: each distinct URL is emitted once.

iframe/ttyd path only — conversation mode has no tmux pane.
"""

from __future__ import annotations

import asyncio
import re
import shlex

from optio_host.host import Host


# A reassembled candidate must look like an OAuth *authorize* URL, so we never
# surface arbitrary links the agent prints during normal work.
_OAUTH_MARKERS = re.compile(r"(oauth|client_id=|redirect_uri=|response_type=code)", re.I)
_URL_START = re.compile(r"https?://")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")


def extract_auth_url(pane: str) -> "str | None":
    """Reassemble a hard-wrapped OAuth authorize URL from a captured tmux pane.

    agy prints the URL inside a box, hard-wrapped at the box content width, so
    the fragments arrive as separate physical lines (``capture-pane -J`` joins
    only soft wraps, not these). We start at a line that begins with ``http`` and
    greedily append following fragment lines — non-empty, whitespace-free, and
    containing alphanumerics (so box rules like ``────`` and prose like "After
    authenticating…" both terminate the run). Returns the URL only if the
    assembled string looks like an OAuth authorize URL, else ``None``.
    """
    parts: "list[str]" = []
    collecting = False
    for raw in pane.splitlines():
        s = raw.strip().strip("│").strip()
        if not collecting:
            if _URL_START.match(s) and " " not in s:
                parts = [s]
                collecting = True
            continue
        # A continuation fragment: no whitespace + has alphanumerics (a border of
        # box-drawing chars has none → stops; prose has spaces → stops).
        if s and " " not in s and _HAS_ALNUM.search(s):
            parts.append(s)
        else:
            break
    if not parts:
        return None
    url = "".join(parts)
    if not _URL_START.match(url) or not _OAUTH_MARKERS.search(url):
        return None
    return url


async def _capture_pane(
    host: Host, tmux_path: str, socket_path: str, session_name: str, lines: int,
) -> str:
    """Joined dump of the tmux pane's recent scrollback (best-effort)."""
    cmd = (
        f"{shlex.quote(tmux_path)} -S {shlex.quote(socket_path)} "
        f"capture-pane -t {shlex.quote(session_name)} -pJ -S -{int(lines)}"
    )
    r = await host.run_command(cmd)
    return r.stdout if r.exit_code == 0 else ""


async def _emit_browser_marker(host: Host, url: str) -> None:
    """Append a ``BROWSER: "<url>"`` line to optio.log (same format as the shim,
    so the log-protocol parser surfaces it via ctx.request_browser_open)."""
    log = f"{host.workdir}/optio.log"
    await host.run_command(
        f"printf 'BROWSER: \"%s\"\\n' {shlex.quote(url)} >> {shlex.quote(log)}"
    )


async def run_auth_url_scraper(
    host: Host,
    *,
    tmux_path: str,
    socket_path: str,
    session_name: str,
    interval: float = 2.0,
    capture_lines: int = 400,
) -> None:
    """Poll the tmux pane; surface each distinct printed OAuth URL once.

    Runs for the life of the iframe session (the caller cancels it on teardown).
    Never raises out of the loop — a transient capture failure is skipped, and
    ``CancelledError`` (a BaseException) propagates so teardown is clean.
    """
    emitted: "set[str]" = set()
    while True:
        try:
            pane = await _capture_pane(
                host, tmux_path, socket_path, session_name, capture_lines,
            )
            url = extract_auth_url(pane)
            if url and url not in emitted:
                emitted.add(url)
                await _emit_browser_marker(host, url)
        except Exception:
            pass  # best-effort; a poll failure must not kill the session
        await asyncio.sleep(interval)
