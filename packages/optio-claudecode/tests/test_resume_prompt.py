"""Tests for the claudecode resume prompt section."""

from types import SimpleNamespace

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX

from optio_claudecode.prompt import _render_resume_section, compose_agents_md

_POLL_RULE = "At the start of every new incoming user message"


def test_render_mentions_resume_log():
    out = _render_resume_section(None)
    assert "## Resumes" in out
    assert "resume.log" in out


def test_render_mentions_home_claude_preserved():
    """Claudecode-specific bullet: home/.claude/ survives resumes."""
    out = _render_resume_section(None)
    assert "home/.claude/" in out


def test_render_default_excludes_listed():
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _render_resume_section(None)
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" in out


def test_render_custom_excludes_listed_and_defaults_absent():
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    out = _render_resume_section(["custom_a", "custom_b"])
    assert "`custom_a`" in out
    assert "`custom_b`" in out
    for pattern in DEFAULT_WORKDIR_EXCLUDES:
        assert f"`{pattern}`" not in out


def test_render_empty_excludes_says_no_paths_excluded():
    out = _render_resume_section([])
    assert "No paths are excluded" in out


def test_compose_includes_resume_section_by_default():
    out = compose_agents_md("hi", workdir_exclude=None, supports_resume=True)
    assert "## Resumes" in out
    assert "home/.claude/" in out


def test_compose_omits_resume_section_when_disabled():
    out = compose_agents_md("hi", workdir_exclude=None, supports_resume=False)
    assert "## Resumes" not in out
    assert "resume.log" not in out


def test_compose_appends_consumer_instructions_verbatim():
    out = compose_agents_md("compute 2+2", workdir_exclude=None, supports_resume=True)
    assert out.endswith("compute 2+2\n")


def test_render_polls_resume_log_every_message_by_default():
    assert _POLL_RULE in _render_resume_section(None)


def test_render_without_polling_relies_on_the_resume_notice():
    out = _render_resume_section(None, check_resume_log_every_message=False)
    assert _POLL_RULE not in out
    assert "slips past unnoticed" not in out
    assert f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`" in out
    # resume.log is still read when the notice arrives, for REFRESHED:.
    assert "read the latest line of `./resume.log`" in out
    assert "re-read each\n  listed file" in out
    assert "Then resume the work you were doing." in out


def test_compose_passes_polling_flag_through():
    assert _POLL_RULE in compose_agents_md("hi")
    out = compose_agents_md("hi", check_resume_log_every_message=False)
    assert _POLL_RULE not in out
    assert f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`" in out


def test_session_polls_resume_log_only_without_host_protocol():
    from optio_claudecode.session import _checks_resume_log_every_message
    assert _checks_resume_log_every_message(SimpleNamespace(host_protocol=False))
    assert not _checks_resume_log_every_message(SimpleNamespace(host_protocol=True))
