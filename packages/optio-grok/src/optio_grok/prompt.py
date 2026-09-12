"""AGENTS.md composition for optio-grok: the profile, plus a thin wrapper over
the shared composer (``optio_agents.prompt.compose_instructions_file``), which
owns all of the shared text. The only grok-specific text is the identity
preamble below."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["PROFILE", "compose_agents_md"]

# Always-present identity line. Grok Build infers its environment from ambient
# clues and, running headlessly here, has guessed it is inside Cursor (both are
# xAI-owned) — so state its identity explicitly, regardless of host_protocol.
_GROK_IDENTITY = """You are running inside **Grok Build** (xAI's agentic coding CLI), \
driven headlessly by an automation harness — not Cursor or any other IDE. If asked \
about your environment or identity, you are Grok Build.

"""

PROFILE = AgentPromptProfile(
    state_dir="home/.grok/",
    state_dir_contents="the grok session store (conversation history, plans, session state)",
    preamble=_GROK_IDENTITY,
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
    """Render <workdir>/AGENTS.md for an optio-grok task.

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
