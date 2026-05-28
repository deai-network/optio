"""Tests for the shared optio.log/AGENTS.md prompt composer."""

from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md,
)


def test_base_prompt_pre_contains_log_keywords():
    assert "STATUS:" in BASE_PROMPT_PRE
    assert "DELIVERABLE:" in BASE_PROMPT_PRE
    assert "DONE" in BASE_PROMPT_PRE
    assert "ERROR" in BASE_PROMPT_PRE


def test_base_prompt_pre_documents_deliverables_dir():
    assert "./deliverables/" in BASE_PROMPT_PRE


def test_compose_with_no_resume_section():
    body = compose_agents_md("My consumer instructions")
    assert "My consumer instructions" in body
    assert "STATUS:" in body
    # No resume content when resume_section=None
    assert "resume.log" not in body
    assert "Resumes" not in body


def test_compose_with_resume_section():
    body = compose_agents_md(
        "Task body",
        resume_section="## Custom resume block\n\nDoes things.",
    )
    assert "Task body" in body
    assert "## Custom resume block" in body
    assert "Does things." in body
    # Resume section appears between PRE and POST
    assert body.index("## Custom resume block") < body.index("## Task")


def test_consumer_instructions_appended_verbatim():
    body = compose_agents_md("  Trailing whitespace   \n\n")
    # Trailing whitespace is stripped; body otherwise verbatim
    assert "Trailing whitespace" in body
    assert "Trailing whitespace   \n\n\n" not in body
