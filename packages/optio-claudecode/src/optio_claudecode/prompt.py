"""AGENTS.md composer for optio-claudecode.

Renders the claudecode resume section and forwards to the shared
``optio_agents.prompt.compose_agents_md``. The resume text is byte-identical
to optio-opencode's, with one added bullet: ``home/.claude/`` (credentials,
settings, conversation transcript) is preserved across resumes — claudecode
needs this because all sensitive agent-continuity state lives there.
"""

from optio_agents.prompt import (
    BASE_PROMPT_POST,
    compose_agents_md as _compose_agents_md_host,
)
from optio_agents.protocol import build_log_channel_prompt


__all__ = ["BASE_PROMPT_POST", "compose_agents_md"]


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

- **Your `home/.claude/` directory — credentials, settings, and the
  conversation transcript — IS preserved across resumes**, so your
  identity and history travel with you even when the underlying process
  and host change.

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
    documentation: str | None = None,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
) -> str:
    """Render <workdir>/AGENTS.md for an optio-claudecode task.

    Renders the claudecode resume section when ``supports_resume`` is
    True and forwards everything else to the shared host composer.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="redirect").documentation``. Defaults (for
    unit tests / standalone callers) to claudecode's ``redirect`` docs.
    """
    if documentation is None:
        documentation = build_log_channel_prompt("redirect")
    resume_section = _render_resume_section(workdir_exclude) if supports_resume else None
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=documentation,
        resume_section=resume_section,
    )
