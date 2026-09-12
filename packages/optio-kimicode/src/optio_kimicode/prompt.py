"""AGENTS.md composition for optio-kimicode: the profile, plus a thin wrapper
over the shared composer (``optio_agents.prompt.compose_instructions_file``),
which owns all of the text."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file
from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES

__all__ = ["PROFILE", "compose_agents_md"]

PROFILE = AgentPromptProfile(
    state_dir="home/.kimi-code/",
    state_dir_contents="the kimi session store (conversation history, session state)",
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
    """Render <workdir>/AGENTS.md for an optio-kimicode task.

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
