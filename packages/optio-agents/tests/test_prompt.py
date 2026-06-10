"""Tests for the feature-aware keyword-protocol prompt builder."""

from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.prompt import build_log_channel_prompt


def test_core_keywords_present_in_every_mode():
    for browser in ("ignore", "suppress", "redirect"):
        block = build_log_channel_prompt(ProtocolFeatures(browser=browser))
        for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR", "ATTENTION:"):
            assert kw in block, (browser, kw)


def test_browser_keyword_only_in_redirect():
    assert "BROWSER:" in build_log_channel_prompt(ProtocolFeatures(browser="redirect"))
    assert "BROWSER:" not in build_log_channel_prompt(ProtocolFeatures(browser="ignore"))
    assert "BROWSER:" not in build_log_channel_prompt(ProtocolFeatures(browser="suppress"))


def test_suppress_trailing_note_only_in_suppress():
    note = "impossible to launch a browser"
    assert note in build_log_channel_prompt(ProtocolFeatures(browser="suppress"))
    assert note not in build_log_channel_prompt(ProtocolFeatures(browser="ignore"))
    assert note not in build_log_channel_prompt(ProtocolFeatures(browser="redirect"))


def test_client_message_documented_only_when_enabled():
    assert "CLIENT_MESSAGE:" in build_log_channel_prompt(
        ProtocolFeatures(client_messages=True))
    assert "CLIENT_MESSAGE:" not in build_log_channel_prompt(ProtocolFeatures())


def test_caller_message_documented_only_when_enabled():
    assert "CALLER_MESSAGE:" in build_log_channel_prompt(
        ProtocolFeatures(caller_messages=True))
    assert "CALLER_MESSAGE:" not in build_log_channel_prompt(ProtocolFeatures())


def test_domain_message_never_documented():
    assert "DOMAIN_MESSAGE:" not in build_log_channel_prompt(
        ProtocolFeatures(client_messages=True, caller_messages=True))


def test_block_mentions_log_and_deliverables_paths():
    block = build_log_channel_prompt()
    assert "./optio.log" in block
    assert "./deliverables/" in block


def test_block_states_trailing_newline_requirement():
    block = build_log_channel_prompt()
    assert "newline" in block
    assert "tail -c 1" in block


def test_block_has_log_channel_and_deliverables_sections():
    block = build_log_channel_prompt()
    assert "## Log channel" in block
    assert "## Deliverables" in block


def test_block_is_framing_neutral():
    assert "optio-opencode" not in build_log_channel_prompt()
