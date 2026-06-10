"""get_protocol binds documentation + parser + features together."""

from optio_agents import get_protocol
from optio_agents.protocol.parser import (
    BrowserEvent,
    CallerMessageEvent,
    ClientMessageEvent,
    UnknownLine,
)


def test_browser_property_matches_request():
    for browser in ("ignore", "suppress", "redirect"):
        assert get_protocol(browser=browser).browser == browser


def test_default_is_all_off():
    p = get_protocol()
    assert p.features.browser == "ignore"
    assert p.features.client_messages is False
    assert p.features.caller_messages is False


def test_documentation_reflects_features():
    assert "BROWSER:" in get_protocol(browser="redirect").documentation
    assert "BROWSER:" not in get_protocol(browser="ignore").documentation
    assert "impossible to launch a browser" in get_protocol(browser="suppress").documentation
    assert "CLIENT_MESSAGE:" in get_protocol(client_messages=True).documentation
    assert "CLIENT_MESSAGE:" not in get_protocol().documentation
    assert "CALLER_MESSAGE:" in get_protocol(caller_messages=True).documentation
    assert "CALLER_MESSAGE:" not in get_protocol().documentation


def test_parser_reflects_features():
    redirect = get_protocol(browser="redirect")
    suppress = get_protocol(browser="suppress")
    assert isinstance(redirect.parse_log_line("BROWSER: https://x"), BrowserEvent)
    assert isinstance(suppress.parse_log_line("BROWSER: https://x"), UnknownLine)

    msgs = get_protocol(client_messages=True, caller_messages=True)
    plain = get_protocol()
    assert isinstance(
        msgs.parse_log_line('CLIENT_MESSAGE: k {"n": 1}'), ClientMessageEvent)
    assert isinstance(
        msgs.parse_log_line('CALLER_MESSAGE: k {"n": 1}'), CallerMessageEvent)
    assert isinstance(
        plain.parse_log_line('CLIENT_MESSAGE: k {"n": 1}'), UnknownLine)
    assert isinstance(
        plain.parse_log_line('CALLER_MESSAGE: k {"n": 1}'), UnknownLine)
