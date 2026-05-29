"""Tests for the SSOT LLM-facing keyword-protocol prompt block."""

from optio_agents.protocol.prompt import LOG_CHANNEL_PROMPT


def test_block_documents_all_four_keywords():
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in LOG_CHANNEL_PROMPT


def test_block_mentions_log_and_deliverables_paths():
    assert "./optio.log" in LOG_CHANNEL_PROMPT
    assert "./deliverables/" in LOG_CHANNEL_PROMPT


def test_block_states_trailing_newline_requirement():
    # The newline rule is load-bearing — the tailer buffers lines without it.
    assert "newline" in LOG_CHANNEL_PROMPT
    assert "tail -c 1" in LOG_CHANNEL_PROMPT


def test_block_has_log_channel_and_deliverables_sections():
    assert "## Log channel" in LOG_CHANNEL_PROMPT
    assert "## Deliverables" in LOG_CHANNEL_PROMPT


def test_block_is_framing_neutral():
    # The SSOT block must NOT carry opencode-specific framing — that's the
    # consumer's job to wrap around it.
    assert "optio-opencode" not in LOG_CHANNEL_PROMPT
