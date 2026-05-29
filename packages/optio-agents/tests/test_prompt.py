"""Tests for the mode-aware keyword-protocol prompt builder."""

from optio_agents.protocol.prompt import build_log_channel_prompt


def test_core_keywords_present_in_every_mode():
    for browser in ("ignore", "suppress", "redirect"):
        block = build_log_channel_prompt(browser)
        for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR",
                   "ATTENTION:", "DOMAIN_MESSAGE:"):
            assert kw in block, (browser, kw)


def test_browser_keyword_only_in_redirect():
    assert "BROWSER:" in build_log_channel_prompt("redirect")
    assert "BROWSER:" not in build_log_channel_prompt("ignore")
    assert "BROWSER:" not in build_log_channel_prompt("suppress")


def test_suppress_trailing_note_only_in_suppress():
    note = "impossible to launch a browser"
    assert note in build_log_channel_prompt("suppress")
    assert note not in build_log_channel_prompt("ignore")
    assert note not in build_log_channel_prompt("redirect")


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
