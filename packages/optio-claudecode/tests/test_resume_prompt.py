"""Claudecode-specific parts of the CLAUDE.md resume section and delivery note."""

from types import SimpleNamespace

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol

from optio_claudecode.prompt import PROFILE, compose_agents_md

_POLL_RULE = "At the start of every new incoming user message"


def test_profile():
    assert PROFILE.instructions_file == "CLAUDE.md"
    assert PROFILE.state_dir == "home/.claude/"


def test_resume_section_names_claude_md_and_home_claude():
    out = compose_agents_md("hi")
    assert "## Resumes" in out
    assert "REFRESHED:CLAUDE.md" in out and "`cat ./CLAUDE.md`" in out
    assert "**Your `home/.claude/` directory" in out


def test_resume_section_omitted_when_disabled():
    out = compose_agents_md("hi", supports_resume=False)
    assert "## Resumes" not in out and "home/.claude/" not in out


def test_delivery_section_present_in_every_mode():
    for kw in (dict(), dict(host_protocol=False), dict(omit_task_framing=True)):
        out = compose_agents_md("DO THE TASK", **kw)
        assert "## Getting text to the user" in out
        assert "AskUserQuestion" in out
        assert out.index("## Getting text to the user") < out.index("DO THE TASK")


def test_polling_flag_passes_through():
    assert _POLL_RULE in compose_agents_md("hi")
    out = compose_agents_md("hi", check_resume_log_every_message=False)
    assert _POLL_RULE not in out
    assert f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`" in out


def test_session_polls_resume_log_only_without_host_protocol():
    from optio_claudecode.session import _checks_resume_log_every_message
    assert _checks_resume_log_every_message(SimpleNamespace(host_protocol=False))
    assert not _checks_resume_log_every_message(SimpleNamespace(host_protocol=True))


def test_threaded_docs_and_no_prompt_text_of_its_own():
    import inspect
    import optio_claudecode.prompt as m
    docs = get_protocol(browser="redirect", caller_messages=True).documentation
    assert "CALLER_MESSAGE:" in compose_agents_md("x", documentation=docs)
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
