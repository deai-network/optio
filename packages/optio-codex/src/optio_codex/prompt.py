"""AGENTS.md composition for optio-codex: the profile, plus a thin wrapper over
the shared composer (``optio_agents.prompt.compose_instructions_file``), which
owns all of the text. The exclude list is resolved with the same function the
snapshot archive uses, so the section's preservation claims never drift."""

from optio_agents.prompt import AgentPromptProfile, compose_instructions_file

from optio_codex.snapshots import effective_workdir_exclude

__all__ = ["PROFILE", "compose_agents_md"]

PROFILE = AgentPromptProfile(
    state_dir="home/.codex/",
    state_dir_contents=(
        "the codex session store (rollout files under `home/.codex/sessions`, "
        "auth, config)"
    ),
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
    """Render <workdir>/AGENTS.md for an optio-codex task.

    ``workdir_exclude=None`` means the codex defaults
    (``snapshots.effective_workdir_exclude``), not the bare framework ones."""
    return compose_instructions_file(
        consumer_instructions,
        profile=PROFILE,
        workdir_exclude=effective_workdir_exclude(workdir_exclude),
        documentation=documentation,
        supports_resume=supports_resume,
        host_protocol=host_protocol,
        omit_task_framing=omit_task_framing,
        fs_isolation_dirs=fs_isolation_dirs,
        file_download=file_download,
        check_resume_log_every_message=check_resume_log_every_message,
    )
