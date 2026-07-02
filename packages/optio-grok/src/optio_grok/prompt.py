"""AGENTS.md composition for optio-grok.

Grok Build reads an ``AGENTS.md`` file in its workdir. The base prompt
teaches grok how to coordinate with the host harness (which log file to
append status/deliverable/done/error lines to, where to put deliverable
files); the consumer's own task description is then appended verbatim.

Adapted from optio-opencode's ``compose_agents_md`` (both agents read
AGENTS.md). Stage 2 adds the resume-awareness section, gated on
``supports_resume``.
"""

from optio_agents.prompt import downloadables_block
from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt


# Always-present identity line. Grok Build infers its environment from ambient
# clues and, running headlessly here, has guessed it is inside Cursor (both are
# xAI-owned) — so state its identity explicitly, regardless of host_protocol.
_GROK_IDENTITY = """You are running inside **Grok Build** (xAI's agentic coding CLI), \
driven headlessly by an automation harness — not Cursor or any other IDE. If asked \
about your environment or identity, you are Grok Build.

"""


_GROK_INTRO = """# Coordination protocol with the host harness

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


# Appended to the resume section when the keyword-protocol docs (which
# normally explain the System: convention) are omitted (host_protocol=False).
_SYSTEM_PREFIX_EXPLAINER = """
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
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

- **Your `home/.grok/` directory — the grok session store (conversation
  history, plans, session state) — IS preserved across resumes**, so
  your history travels with you even when the underlying process and
  host change.

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

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""


def _render_resume_section(workdir_exclude: list[str] | None) -> str:
    """Render the RESUME_SECTION_TEMPLATE with the effective exclude list."""
    from optio_host.archive import DEFAULT_WORKDIR_EXCLUDES
    effective = (
        workdir_exclude if workdir_exclude is not None else DEFAULT_WORKDIR_EXCLUDES
    )
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
    host_protocol: bool = True,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
    file_download: bool = False,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      host_protocol: when False, omit the keyword-protocol documentation
        block entirely (a Stage-6 concern; the branch is kept). Default
        True — iframe mode's only completion signal is the optio.log
        keyword channel.
      workdir_exclude: the snapshot exclude list for this task, used to keep
        the resume section's claims in sync with what is actually preserved.
        None → render with the framework defaults.
      supports_resume: when True (default), the resume-awareness section is
        included so the agent knows to watch ``resume.log`` and that
        ``home/.grok`` survives across resumes.
      file_download: when True, append the downloadables instruction block so
        grok offers files to the human via the ``optio-file:`` sentinel link
        (conversation_ui file-download feature).
    """
    if file_download:
        consumer_instructions = (
            consumer_instructions.rstrip() + downloadables_block(comparative=host_protocol)
        )
    if host_protocol:
        documentation = build_log_channel_prompt(ProtocolFeatures(browser="redirect"))
        base_prompt_pre = _GROK_INTRO + documentation
    else:
        base_prompt_pre = ""
    if supports_resume:
        resume_block = _render_resume_section(workdir_exclude)
        if not host_protocol:
            # The protocol docs normally explain the `System:` convention;
            # without them the resume section carries its own explainer.
            resume_block = resume_block + _SYSTEM_PREFIX_EXPLAINER
        resume_block = resume_block + "\n"
    else:
        resume_block = ""
    body = consumer_instructions.rstrip()
    pre = f"{base_prompt_pre}\n" if base_prompt_pre else ""
    # _GROK_IDENTITY is always first so grok never mis-identifies its environment.
    return f"{_GROK_IDENTITY}{pre}{resume_block}{BASE_PROMPT_POST}\n{body}\n"
