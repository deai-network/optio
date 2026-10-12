"""Claudecode-specific parts of the CLAUDE.md resume section and delivery note."""

import dataclasses

import pytest

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.prompt import PROFILE, compose_agents_md
from optio_claudecode.session import _maybe_refresh_on_resume

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


class _FakeHost:
    workdir = "/tmp/fake-wd"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    async def write_text(self, path: str, text: str) -> None:
        self.writes.append((path, text))


class _FakeHookCtx:
    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        return "stale"


@pytest.mark.parametrize("mode_kw", [
    dict(mode="iframe"),
    dict(mode="conversation", host_protocol=False,
         permission_mode="bypassPermissions"),
], ids=["iframe", "conversation-no-host-protocol"])
async def test_refreshed_claude_md_waits_for_the_resume_notice(mode_kw):
    """Every resume that continues a transcript is announced, host_protocol
    or not, so the CLAUDE.md the on-resume refresh writes tells claude to
    wait for the notice instead of reading resume.log before each message."""
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="original", fs_isolation=False, **mode_kw,
        on_resume_refresh=lambda c: dataclasses.replace(
            c, consumer_instructions="UPDATED"),
    )
    host = _FakeHost()
    protocol = get_protocol(browser="redirect")
    assert await _maybe_refresh_on_resume(host, _FakeHookCtx(), cfg, protocol) == ["CLAUDE.md"]
    (path, text), = host.writes
    assert path == "CLAUDE.md" and "UPDATED" in text
    assert _POLL_RULE not in text
    assert f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`" in text


def test_threaded_docs_and_no_prompt_text_of_its_own():
    import inspect
    import optio_claudecode.prompt as m
    docs = get_protocol(browser="redirect", caller_messages=True).documentation
    assert "CALLER_MESSAGE:" in compose_agents_md("x", documentation=docs)
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
