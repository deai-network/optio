"""Tests for prompt composition."""

import pytest

from optio_opencode.prompt import compose_agents_md


def _compose(consumer="say hi", workdir_exclude=None, supports_resume=True):
    """Helper: call compose_agents_md with the new mandatory args."""
    return compose_agents_md(
        consumer,
        workdir_exclude=workdir_exclude,
        supports_resume=supports_resume,
    )


def test_base_prompt_contains_all_keywords():
    out = _compose()
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in out


def test_base_prompt_mentions_log_and_deliverables_paths():
    out = _compose()
    assert "./optio.log" in out
    assert "./deliverables/" in out


def test_base_prompt_contains_task_framing():
    out = _compose()
    assert "## Task" in out
    assert "ask questions and dialogue with the human" in out


def test_compose_agents_md_appends_consumer_instructions_verbatim():
    out = _compose("please compute 2 + 2")
    assert out.endswith("please compute 2 + 2\n")


def test_compose_agents_md_empty_consumer_still_ends_cleanly():
    out = _compose("")
    assert out.endswith("\n")


def test_compose_agents_md_workdir_exclude_required():
    """workdir_exclude is mandatory — calling without it raises TypeError."""
    with pytest.raises(TypeError):
        compose_agents_md("hi")  # type: ignore[call-arg]


def test_compose_agents_md_includes_resume_section_by_default():
    """Default supports_resume=True → resume section is present."""
    out = _compose()
    assert "## Resumes" in out
    assert "resume.log" in out


def test_compose_agents_md_omits_resume_section_when_supports_resume_false():
    """supports_resume=False → resume section is absent."""
    out = _compose(supports_resume=False)
    assert "## Resumes" not in out
    assert "resume.log" not in out


def test_compose_agents_md_renders_default_excludes_when_none():
    """workdir_exclude=None → prompt mentions DEFAULT_WORKDIR_EXCLUDES patterns."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _compose(workdir_exclude=None, supports_resume=True)
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" in out


def test_compose_agents_md_resume_section_between_deliverables_and_task():
    """Resume section sits between Deliverables and Task in the rendered prompt."""
    out = _compose()
    deliverables_pos = out.index("## Deliverables")
    resumes_pos = out.index("## Resumes")
    task_pos = out.index("## Task")
    assert deliverables_pos < resumes_pos < task_pos


def test_compose_agents_md_renders_custom_excludes():
    """workdir_exclude=[...] → prompt lists those patterns and NOT defaults."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _compose(workdir_exclude=["custom_a", "custom_b"])
    assert "`custom_a`" in out
    assert "`custom_b`" in out
    # None of the default patterns should appear.
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" not in out


def test_compose_agents_md_empty_excludes_renders_no_paths_excluded_copy():
    """workdir_exclude=[] → 'No paths are excluded' wording."""
    out = _compose(workdir_exclude=[])
    assert "No paths are excluded" in out
    # The 'inside an excluded subdirectory' clause should be absent (it's
    # only relevant when there are exclusions to live inside).
    assert "inside an excluded subdirectory" not in out


def test_opencode_docs_omit_browser_keyword():
    """opencode suppresses browser-opens, so it must NOT advertise BROWSER:."""
    out = _compose()
    assert "BROWSER:" not in out


def test_opencode_docs_include_suppress_note():
    out = _compose()
    assert "impossible to launch a browser" in out


# --- conversation-mode composition -------------------------------------

from optio_opencode.prompt import DEFAULT_CONVERSATION_INSTRUCTIONS


def test_host_protocol_off_omits_keyword_docs():
    out = compose_agents_md(
        "talk to me", workdir_exclude=None, host_protocol=False,
    )
    assert "optio.log" not in out
    assert "DELIVERABLE" not in out
    assert "talk to me" in out


def test_host_protocol_off_resume_gains_system_explainer():
    out = compose_agents_md(
        "x", workdir_exclude=None, host_protocol=False, supports_resume=True,
    )
    assert "System:" in out          # the explainer
    assert "## Resumes" in out       # resume section retained


def test_omit_task_framing_drops_task_header():
    out = compose_agents_md(
        DEFAULT_CONVERSATION_INSTRUCTIONS,
        workdir_exclude=None, host_protocol=False, supports_resume=False,
        omit_task_framing=True,
    )
    assert "## Task" not in out
    assert out.rstrip().endswith(DEFAULT_CONVERSATION_INSTRUCTIONS)


def test_default_composition_unchanged():
    out = compose_agents_md("body", workdir_exclude=None)
    assert "## Task" in out
    assert "optio.log" in out
