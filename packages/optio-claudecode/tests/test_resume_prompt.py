"""Tests for the claudecode resume prompt section."""

from optio_claudecode.prompt import _render_resume_section, compose_agents_md


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
