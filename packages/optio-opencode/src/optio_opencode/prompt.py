"""System-prompt composition for optio-opencode.

The base prompt teaches opencode (via AGENTS.md) how to coordinate with the
host harness: which log file to append status/deliverable/done/error lines
to, where to put deliverable files, and how the human expects to be
addressed. The consumer's own task description is then appended verbatim.
"""


from optio_agents.protocol import build_log_channel_prompt


_OPENCODE_INTRO = """# Coordination protocol with the host (optio-opencode)

You are running inside a coordination harness. Follow these conventions
throughout the session.

"""


BASE_PROMPT_POST = """## Task

Here comes the description of your actual task to complete. Throughout
the task, you are encouraged to narrate progress — both on the normal
UI and in parallel using the `STATUS:` messages explained above — and
you are free to ask questions and dialogue with the human. They are
also working on the same task and will cooperate with you on achieving
the same goals. So:
"""


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
    documentation: str | None = None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      workdir_exclude: the snapshot exclude list for this task. Mandatory:
        callers must pass it explicitly to prevent silent desync between
        archive.py defaults and the prompt's claims about what's preserved.
        Pass None to render with the framework defaults.
      documentation: the keyword-protocol block; the session passes
        ``get_protocol(browser="suppress").documentation``. Defaults (for
        unit tests / standalone callers) to opencode's ``suppress`` docs.
      supports_resume: when False, the resume-detection section is omitted
        from the prompt. Default True.
    """
    if documentation is None:
        documentation = build_log_channel_prompt("suppress")
    base_prompt_pre = _OPENCODE_INTRO + documentation
    body = consumer_instructions.rstrip()
    if supports_resume:
        resume_block = _render_resume_section(workdir_exclude) + "\n"
    else:
        resume_block = ""
    return f"{base_prompt_pre}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
