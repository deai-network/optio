from optio_kimicode.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    """host_protocol=True documents the log-channel keywords and appends the
    consumer's task verbatim."""
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md


def test_agents_md_omits_keyword_docs_and_adds_system_explainer_when_off():
    """host_protocol=False omits the keyword-protocol documentation entirely and
    instead carries the ``System:`` explainer so the agent still understands
    harness-originated messages (conversation demo runs host_protocol=False)."""
    md = compose_agents_md("x", host_protocol=False)
    # keyword documentation block gone (the ## Log channel section and its
    # DELIVERABLE: bullet). The trailing task prose still backreferences
    # STATUS:, so that literal alone is not a reliable marker.
    assert "DELIVERABLE:" not in md
    assert "Log channel" not in md
    # System explainer present
    assert "System:" in md
    assert "harness" in md


def test_agents_md_resume_section_names_kimi_session_store():
    """With supports_resume=True (default) the resume-awareness section teaches
    the agent to watch ``resume.log`` and that the per-task kimi session store
    (``home/.kimi-code``) survives across resumes."""
    md = compose_agents_md("task", host_protocol=True)
    assert "resume.log" in md
    assert "home/.kimi-code" in md


def test_agents_md_no_resume_section_when_unsupported():
    md = compose_agents_md("task", host_protocol=True, supports_resume=False)
    assert "resume.log" not in md
    assert "Resumes" not in md


def test_agents_md_file_download_appends_downloadables_block():
    """file_download=True appends the optio-file: downloadables instruction."""
    plain = compose_agents_md("task", host_protocol=True, file_download=False)
    with_dl = compose_agents_md("task", host_protocol=True, file_download=True)
    assert "optio-file:" in with_dl
    assert "optio-file:" not in plain
