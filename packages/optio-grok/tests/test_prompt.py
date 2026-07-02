from optio_grok.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md


def test_agents_md_states_grok_build_identity_in_both_modes():
    """Grok Build guesses its environment from ambient clues and, headless, has
    mis-identified as Cursor. The AGENTS.md must always state its identity — in
    both host_protocol modes (the conversation demo runs host_protocol=False)."""
    for hp in (True, False):
        md = compose_agents_md("x", host_protocol=hp)
        assert "Grok Build" in md
        assert "not Cursor" in md


def test_agents_md_advertises_browser_redirect_keyword():
    """Grok Build runs in ``browser="redirect"`` so its first-launch loopback
    OAuth browser-open is captured and surfaced (not silently suppressed). The
    log-channel docs must therefore advertise the ``BROWSER:`` keyword — the
    parser only recognizes it under ``redirect``. Guards against a silent revert
    to ``suppress`` (which no-op'd the login with no operator feedback)."""
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BROWSER:" in md
