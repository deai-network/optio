"""Tests for the shared instructions-file composer."""

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX, get_protocol
from optio_agents.prompt import (
    BASE_PROMPT_POST,
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    AgentPromptProfile,
    compose_instructions_file,
)

PLAIN = AgentPromptProfile()
CLAUDE = AgentPromptProfile(
    instructions_file="CLAUDE.md",
    state_dir="home/.claude/",
    state_dir_contents="credentials, settings, and the conversation transcript",
    delivery_section="## Getting text to the user\n\nBody.",
)
POLL_RULE = "At the start of every new incoming user message"
NOTICE = f"`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}`"


def _c(instructions="do X", profile=PLAIN, workdir_exclude=("a", "b"), **kw):
    return compose_instructions_file(
        instructions, profile=profile, workdir_exclude=list(workdir_exclude), **kw,
    )


def test_order_preamble_intro_docs_resume_delivery_framing_body():
    out = _c(profile=AgentPromptProfile(
        preamble="PREAMBLE\n\n", delivery_section="## Delivery\n\nD.",
    ))
    pos = [out.index(s) for s in (
        "PREAMBLE", "# Coordination protocol with the host harness", "## Log channel",
        "## Resumes", "## Delivery", "## Task", "do X",
    )]
    assert pos == sorted(pos)
    assert out.startswith("PREAMBLE")
    assert out.endswith("do X\n")


def test_default_docs_follow_profile_browser():
    assert "BROWSER:" in _c()                                   # redirect
    sup = _c(profile=AgentPromptProfile(browser="suppress"))
    assert "BROWSER:" not in sup and "impossible to launch a browser" in sup


def test_passed_documentation_is_used_verbatim():
    docs = get_protocol(browser="redirect", client_messages=True).documentation
    out = _c(documentation=docs)
    assert docs in out and "CLIENT_MESSAGE:" in out


def test_host_protocol_off_drops_intro_and_docs_adds_explainer():
    out = _c(host_protocol=False)
    assert "# Coordination protocol" not in out and "## Log channel" not in out
    assert "originate from the harness" in " ".join(out.split())
    assert "## Resumes" in out


def test_explainer_present_without_resume_section_when_host_protocol_off():
    out = _c(host_protocol=False, supports_resume=False)
    assert "## Resumes" not in out
    assert "originate from the harness" in " ".join(out.split())


def test_explainer_absent_when_host_protocol_on():
    assert "originate from the harness" not in " ".join(_c().split())


def test_supports_resume_false_omits_resume_and_state_dir():
    out = _c(profile=CLAUDE, supports_resume=False)
    assert "## Resumes" not in out and "resume.log" not in out
    assert "home/.claude/" not in out


def test_state_dir_bullet_only_when_set():
    assert "IS preserved across resumes" not in _c()
    out = _c(profile=CLAUDE)
    assert "**Your `home/.claude/` directory — credentials, settings, and the" in out
    assert "IS preserved across resumes**, so your" in out
    assert "history travels with you" in out


def test_instructions_file_named_in_refreshed_example_and_cat_hint():
    out = _c(profile=CLAUDE)
    assert "REFRESHED:CLAUDE.md" in out and "`cat ./CLAUDE.md`" in out
    out = _c()
    assert "REFRESHED:AGENTS.md" in out and "`cat ./AGENTS.md`" in out


def test_excludes_listed_custom_and_empty():
    out = _c(workdir_exclude=("custom_a",))
    assert "`custom_a`" in out and "exclude list is: `custom_a`." in out
    out = _c(workdir_exclude=())
    assert "No paths are excluded" in out
    assert "inside an excluded subdirectory" not in out


def test_resume_detection_polls_by_default():
    out = _c()
    assert POLL_RULE in out and "slips past unnoticed" in out
    assert NOTICE not in out


def test_resume_detection_by_notice():
    out = _c(check_resume_log_every_message=False)
    assert POLL_RULE not in out and "slips past unnoticed" not in out
    assert NOTICE in out
    assert "read the latest line of `./resume.log`" in out
    assert "Then resume the work you were doing." in out


def test_task_framing_and_omission():
    assert BASE_PROMPT_POST in _c()
    out = _c(DEFAULT_CONVERSATION_INSTRUCTIONS, omit_task_framing=True)
    assert "## Task" not in out
    assert out.endswith(DEFAULT_CONVERSATION_INSTRUCTIONS + "\n")


def test_delivery_section_survives_omit_task_framing():
    out = _c(profile=CLAUDE, omit_task_framing=True)
    assert "## Getting text to the user" in out and "## Task" not in out


def test_downloads_note_then_sandbox_note_after_body():
    out = _c(file_download=True, fs_isolation_dirs=[("/wd", "rwx"), ("/ro", "ro")])
    body, dl, fs = out.index("do X"), out.index("## Downloadable files"), out.index("**Filesystem access:**")
    assert body < dl < fs
    assert "`/ro` (read-only)" in out and "`/wd` (read-only)" not in out
    assert "## Downloadable files" not in _c()
    assert "Filesystem access" not in _c()


def test_downloads_wording_follows_host_protocol():
    assert "Deliverables (the DELIVERABLE keyword)" in _c(file_download=True)
    assert "Deliverables (the DELIVERABLE keyword)" not in _c(file_download=True, host_protocol=False)
