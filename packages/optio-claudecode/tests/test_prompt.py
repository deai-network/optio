"""Tests for the claudecode AGENTS.md composer."""

from optio_claudecode.prompt import compose_agents_md


def test_compose_includes_consumer_instructions():
    body = compose_agents_md("Please write a haiku about MongoDB.")
    assert "Please write a haiku about MongoDB." in body


def test_compose_includes_coordination_preamble():
    body = compose_agents_md("Whatever.")
    assert "STATUS:" in body
    assert "DELIVERABLE:" in body
    assert "DONE" in body
    assert "ERROR" in body
    assert "./deliverables/" in body


def test_compose_includes_resume_section_by_default():
    """Resume support landed: the default composer now renders the
    resume section. (Was test_compose_has_no_resume_section_in_v1 before
    resume support; updated per the v1 follow-up.)"""
    body = compose_agents_md("Whatever.")
    assert "resume.log" in body
    assert "## Resumes" in body


def test_compose_omits_resume_section_when_disabled():
    body = compose_agents_md("Whatever.", supports_resume=False)
    assert "resume.log" not in body
    assert "## Resumes" not in body
