"""get_protocol binds documentation + parser + browser mode together."""

import pytest

from optio_agents import get_protocol
from optio_agents.protocol.parser import BrowserEvent, UnknownLine


def test_browser_field_matches_request():
    for browser in ("ignore", "suppress", "redirect"):
        assert get_protocol(browser=browser).browser == browser


def test_default_is_ignore():
    assert get_protocol().browser == "ignore"


def test_documentation_reflects_mode():
    assert "BROWSER:" in get_protocol(browser="redirect").documentation
    assert "BROWSER:" not in get_protocol(browser="ignore").documentation
    assert "impossible to launch a browser" in get_protocol(browser="suppress").documentation


def test_parser_reflects_mode():
    redirect = get_protocol(browser="redirect")
    suppress = get_protocol(browser="suppress")
    assert isinstance(redirect.parse_log_line("BROWSER: https://x"), BrowserEvent)
    assert isinstance(suppress.parse_log_line("BROWSER: https://x"), UnknownLine)
