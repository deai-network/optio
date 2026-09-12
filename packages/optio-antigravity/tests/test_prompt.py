from optio_antigravity.prompt import compose_agents_md


def test_agents_md_has_log_protocol_and_resume_pull():
    """AGENTS.md carries the optio.log keyword channel, the DONE completion
    signal, the pull half of resume awareness (``resume.log``), and the
    consumer's own task text verbatim."""
    md = compose_agents_md("Do the thing", host_protocol=True)
    assert "optio.log" in md
    assert "DONE" in md
    assert "resume.log" in md            # pull half of resume awareness
    assert "Do the thing" in md          # verbatim consumer instructions


def test_agents_md_keyword_docs_only_under_host_protocol():
    """host_protocol=True documents the optio.log keyword channel; with
    host_protocol=False the channel docs are omitted but the session's
    ``System:`` convention is still explained (so a resumed conversation-mode
    agent understands harness messages)."""
    md_on = compose_agents_md("x", host_protocol=True)
    assert "optio.log" in md_on
    assert "STATUS:" in md_on and "DELIVERABLE:" in md_on

    md_off = compose_agents_md("x", host_protocol=False)
    # The log-channel documentation block (and its optio.log reference) is gone.
    assert "optio.log" not in md_off
    assert "Append one line per entry" not in md_off
    # ...but the System: convention is still taught via the resume explainer.
    assert "System:" in md_off
    assert "x" in md_off


def test_agents_md_omits_resume_section_when_not_supported():
    """supports_resume=False drops the resume-awareness section entirely."""
    md = compose_agents_md("x", host_protocol=True, supports_resume=False)
    assert "resume.log" not in md


def test_sandbox_note_and_no_prompt_text_of_its_own():
    import inspect
    import optio_antigravity.prompt as m
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Your `home/.gemini/antigravity/` directory" in out
    assert "**Filesystem access:**" in out
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
