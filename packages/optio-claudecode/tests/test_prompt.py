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


def test_compose_has_no_resume_section_in_v1():
    body = compose_agents_md("Whatever.")
    assert "resume.log" not in body
    assert "## Resumes" not in body
