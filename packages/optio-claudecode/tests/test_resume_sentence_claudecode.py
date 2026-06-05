from optio_claudecode.prompt import _render_resume_section


def test_resume_section_mentions_system_notification():
    section = _render_resume_section(None)
    assert "`System:` message" in section
    assert "resume.log" in section
