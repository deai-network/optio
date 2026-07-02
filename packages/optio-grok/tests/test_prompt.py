from optio_grok.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md


def test_agents_md_advertises_browser_redirect_keyword():
    """Grok Build runs in ``browser="redirect"`` so its first-launch loopback
    OAuth browser-open is captured and surfaced (not silently suppressed). The
    log-channel docs must therefore advertise the ``BROWSER:`` keyword — the
    parser only recognizes it under ``redirect``. Guards against a silent revert
    to ``suppress`` (which no-op'd the login with no operator feedback)."""
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BROWSER:" in md
