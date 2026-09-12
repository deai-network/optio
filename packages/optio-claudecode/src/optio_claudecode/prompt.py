"""CLAUDE.md composition for optio-claudecode: the profile, plus a thin wrapper
over the shared composer (``optio_agents.prompt.compose_instructions_file``),
which owns all of the text.

Claudecode-specific: the file is ``CLAUDE.md``; ``home/.claude/`` (credentials,
settings, conversation transcript) is preserved across resumes; and the
"Getting text to the user" section, because with extended thinking on, text
Claude Code writes in a response that also calls a tool may never reach the
reader (owner request, 2026-09-12)."""

from optio_agents.prompt import (
    DEFAULT_CONVERSATION_INSTRUCTIONS,
    AgentPromptProfile,
    compose_instructions_file,
)
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["DEFAULT_CONVERSATION_INSTRUCTIONS", "PROFILE", "compose_agents_md"]

_DELIVERY_SECTION = """## Getting text to the user

Text you write in a response that also calls a tool may never reach the user: with extended thinking on it can end up in your hidden reasoning, and at most a short summary is shown. So:

- Anything the user must read in full (drafts, spec sections, reports, long answers) goes in a response with no tool call after it, or in a file you write and name the path of.
- When you ask the user to review something with AskUserQuestion, put the full text in the question itself or in a file, never in text before the call.
- Never assume the user saw text you wrote before a tool call.
"""

PROFILE = AgentPromptProfile(
    instructions_file="CLAUDE.md",
    state_dir="home/.claude/",
    state_dir_contents="credentials, settings, and the conversation transcript",
    delivery_section=_DELIVERY_SECTION,
)


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None = None,
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render <workdir>/CLAUDE.md for an optio-claudecode task.

    ``workdir_exclude=None`` means the framework defaults, the same list the
    snapshot archive uses (``optio_host.archive``)."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=(
            list(DEFAULT_WORKDIR_EXCLUDES) if workdir_exclude is None else workdir_exclude
        ),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
