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


def test_resume_section_present_and_synced_to_default_excludes():
    md = compose_agents_md("X")
    assert "## Resumes" in md
    assert "resume.log" in md
    # Truth-sync: the rendered exclude list is the EFFECTIVE codex default
    # (snapshots.effective_workdir_exclude), not the framework default…
    assert "`home/.codex/packages`" in md
    assert "`*.sqlite*`" in md
    # …and the session store is called out as preserved.
    assert "home/.codex/sessions" in md


def test_resume_section_renders_custom_and_empty_excludes():
    md = compose_agents_md("X", workdir_exclude=["bigdata"])
    assert "`bigdata`" in md
    assert "`home/.codex/packages`" not in md

    md_empty = compose_agents_md("X", workdir_exclude=[])
    assert "No paths are excluded" in md_empty


def test_supports_resume_false_omits_resume_section():
    md = compose_agents_md("X", supports_resume=False)
    assert "## Resumes" not in md
    assert "resume.log" not in md


def test_host_protocol_false_keeps_resume_section_and_explainer():
    md = compose_agents_md("X", host_protocol=False)
    assert "## Resumes" in md
    assert "System:" in md
    # Protocol docs stay omitted. (Cannot assert on the bare "STATUS:"
    # literal — the imported BASE_PROMPT_POST framing mentions it in
    # passing; same caveat as test_host_protocol_false_adds_system_explainer.)
    assert get_protocol(browser="suppress").documentation not in md
    assert "## Log channel" not in md
