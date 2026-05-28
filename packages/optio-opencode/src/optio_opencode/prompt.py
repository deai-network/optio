"""System-prompt composition for optio-opencode.

The base optio.log/deliverables text now lives in ``optio_host.agents``.
This module keeps only the opencode-specific resume section and a thin
wrapper that renders it and forwards to the shared composer.
"""

from optio_host.agents import (
    BASE_PROMPT_PRE,
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)


# Re-export so existing `from optio_opencode.prompt import BASE_PROMPT_PRE`
# call sites keep working.
__all__ = ["BASE_PROMPT_PRE", "BASE_PROMPT_POST", "compose_agents_md"]


RESUME_SECTION_TEMPLATE = """## Resumes

This harness may pause your session, save your context to a database,
terminate the underlying process, and later rehydrate it. From your
point of view the conversation is fully continuous — you keep your
prior context and will not "notice" the resume.

**A resume can happen at any point, not only at the start.** The host
environment may have changed across a resume — different host,
different running processes, files outside this workdir gone — even
though your context remembers everything as alive and well.

**The workdir (this directory) is preserved across resumes, with two
caveats:**

- {excludes_clause}
- **Anything outside the workdir is not preserved.**

{outside_clause}

### Detecting a resume: `resume.log`

Each session start (fresh or resumed) appends one line to
`./resume.log`. Line format:

```
<ISO 8601 UTC timestamp>[ REFRESHED:<comma-separated filenames>]
```

The very first line is the original launch timestamp; each subsequent
line is a resume. The optional `REFRESHED:` suffix signals that the
harness rewrote the listed files on that resume (e.g.
`2026-05-28T13:15:42Z REFRESHED:AGENTS.md`) — your in-memory copy of
those files is stale and must be re-read before continuing.

**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- **If the latest line carries a `REFRESHED:` suffix, re-read each
  listed file** (e.g. `cat ./AGENTS.md`) — the harness updated it
  since your last context snapshot and the version you remember is
  out of date.
- Then resume the work you were doing.

If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render the RESUME_SECTION_TEMPLATE with the effective exclude list."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    effective = workdir_exclude if workdir_exclude is not None else DEFAULT_WORKDIR_EXCLUDES
    if not effective:
        excludes_clause = (
            "**No paths are excluded** — every file in the workdir is preserved."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) — but remember it may be missing when you next look."
        )
    else:
        excludes_str = ", ".join(f"`{p}`" for p in effective)
        excludes_clause = (
            f"**Paths matching the snapshot exclude list are NOT preserved**, "
            f"even inside the workdir. The current exclude list is: {excludes_str}."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) or inside an excluded subdirectory — but remember "
            "any such location may be missing when you next look."
        )
    return RESUME_SECTION_TEMPLATE.format(
        excludes_clause=excludes_clause,
        outside_clause=outside_clause,
    )


def compose_agents_md(
    consumer_instructions: str,
    *,
    workdir_exclude: list[str] | None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body for an opencode task.

    Backwards-compatible wrapper around
    ``optio_host.agents.compose_agents_md``. Renders the
    opencode-specific resume section (when ``supports_resume`` is True)
    and forwards everything else.
    """
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions, resume_section=resume_section,
    )
