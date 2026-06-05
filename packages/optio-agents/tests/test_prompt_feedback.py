from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt


def test_resume_notice_is_nonempty_str():
    assert isinstance(RESUME_NOTICE, str) and RESUME_NOTICE.strip()


def test_feedback_block_present_in_every_mode():
    for mode in ("ignore", "redirect", "suppress"):
        doc = build_log_channel_prompt(mode)
        assert "System:" in doc
        assert "input channel" in doc.lower()
        assert "Messages from the harness" in doc
