from optio_agents import get_protocol
from optio_agents.prompt import BASE_PROMPT_POST

from optio_codex.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md


def test_documentation_threads_from_session_protocol():
    """SSOT: the session's protocol documentation is the one that lands in
    AGENTS.md — and the standalone default must render identically, so the
    two construction sites cannot drift."""
    protocol = get_protocol(browser="suppress")
    threaded = compose_agents_md("X", documentation=protocol.documentation)
    defaulted = compose_agents_md("X")
    assert threaded == defaulted
    assert protocol.documentation in threaded


def test_shared_framing_is_imported_not_copied():
    md = compose_agents_md("X")
    assert BASE_PROMPT_POST in md  # the optio-agents SSOT copy, verbatim


def test_host_protocol_false_adds_system_explainer():
    md = compose_agents_md("X", host_protocol=False)
    # The keyword-protocol documentation block is omitted. (Cannot assert on
    # the bare literal "STATUS:" — the SSOT BASE_PROMPT_POST framing, imported
    # verbatim, itself mentions `STATUS:` in passing.)
    assert get_protocol(browser="suppress").documentation not in md
    assert "## Log channel" not in md
    assert "System:" in md  # the explainer replaces the protocol docs
    assert "X" in md
