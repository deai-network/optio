"""Unit tests for the agy print-only-login auth-URL pane scraper.

agy's login prints the Google OAuth URL into the TUI (no browser open — the
redirect shims never fire), and the URL is HARD-wrapped to the box width so
ttyd only linkifies the first line. The scraper reads the tmux pane, reassembles
the wrapped URL, and emits a ``BROWSER:`` marker so optio surfaces one clean,
copyable link.
"""

import pytest

from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.parser import BrowserEvent, parse_log_line
from optio_host.host import LocalHost

from optio_antigravity.auth_scrape import (
    _emit_browser_marker,
    extract_auth_url,
    run_auth_url_scraper,
)


# A realistic capture of agy's login box (hard-wrapped URL, box rules, rocket art).
_REAL_PANE = """\
     ▄▀▀▄
    ▀▀▀▀▀▀
   ▀▀▀▀▀▀▀▀

 Open the URL below in your browser:
 ────────────────────────────────────────────────────────────────────────────
 https://accounts.google.com/o/oauth2/auth?access_type=offline&client_id=1071006060591-abc.apps.google
 usercontent.com&code_challenge=rByogkLT&code_challenge_method=S256&prompt=consent&redirect_uri=
 https%3A%2F%2Fantigravity.google%2Foauth-callback&response_type=code&scope=openid&state=1q2QQ0
 ────────────────────────────────────────────────────────────────────────────
 After authenticating, copy the code displayed in the browser and paste it below:
 authorization code...
"""

_EXPECTED = (
    "https://accounts.google.com/o/oauth2/auth?access_type=offline"
    "&client_id=1071006060591-abc.apps.googleusercontent.com"
    "&code_challenge=rByogkLT&code_challenge_method=S256&prompt=consent"
    "&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
    "&response_type=code&scope=openid&state=1q2QQ0"
)


def test_extracts_and_reassembles_hard_wrapped_oauth_url():
    assert extract_auth_url(_REAL_PANE) == _EXPECTED


def test_returns_none_when_no_url_present():
    assert extract_auth_url("just a normal terminal\n$ ls -la\nfoo bar\n") is None


def test_ignores_non_oauth_urls():
    # Only an OAuth authorize URL should be surfaced — not arbitrary links the
    # agent may print during normal work.
    assert extract_auth_url("visit https://example.com/docs for help\n") is None
    assert extract_auth_url("see https://github.com/foo/bar/issues/76\n") is None


def test_single_line_url_not_split_is_returned_whole():
    pane = (
        "Open the URL below in your browser:\n"
        "https://accounts.google.com/o/oauth2/auth?client_id=abc&response_type=code&state=z\n"
        "After authenticating, paste the code:\n"
    )
    assert extract_auth_url(pane) == (
        "https://accounts.google.com/o/oauth2/auth?client_id=abc&response_type=code&state=z"
    )


@pytest.mark.asyncio
async def test_emit_marker_is_parsed_as_browser_open(tmp_path):
    # The emitted optio.log line must round-trip through the log-protocol parser
    # as a BrowserEvent (so the driver calls ctx.request_browser_open).
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    url = "https://accounts.google.com/o/oauth2/auth?client_id=abc&response_type=code&state=z"
    await _emit_browser_marker(host, url)
    log = open(f"{host.workdir}/optio.log").read()
    lines = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(lines) == 1
    ev = parse_log_line(lines[0], features=ProtocolFeatures(browser="redirect"))
    assert isinstance(ev, BrowserEvent)
    assert ev.url == url


@pytest.mark.asyncio
async def test_scraper_emits_each_url_once(tmp_path, monkeypatch):
    # Given a pane that keeps showing the same auth URL, the scraper emits it
    # exactly once (dedup), regardless of poll count.
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    pane = (
        "Open the URL below in your browser:\n"
        "https://accounts.google.com/o/oauth2/auth?client_id=abc&response_type=code&state=z\n"
        "authorization code...\n"
    )
    import optio_antigravity.auth_scrape as m

    async def fake_capture(*a, **k):
        return pane

    monkeypatch.setattr(m, "_capture_pane", fake_capture)

    import asyncio
    task = asyncio.ensure_future(
        run_auth_url_scraper(
            host, tmux_path="tmux", socket_path="s", session_name="optio",
            interval=0.01,
        )
    )
    await asyncio.sleep(0.1)  # several poll cycles
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    log = open(f"{host.workdir}/optio.log").read()
    emitted = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(emitted) == 1
