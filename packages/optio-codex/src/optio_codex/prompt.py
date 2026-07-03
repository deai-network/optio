"""AGENTS.md composition for optio-codex.

Codex reads an ``AGENTS.md`` file in its workdir. The shared framing and
the keyword-protocol documentation are owned by ``optio-agents`` (the
prompt SSOT); this module threads codex's protocol mode through and owns
the codex-specific resume-awareness section (Stage 2), rendered from the
EFFECTIVE snapshot exclude list so its preservation claims never drift
from what the snapshot actually keeps.
"""

from optio_agents.prompt import compose_agents_md as _compose_agents_md_host
from optio_agents.prompt import downloadables_block
from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt

from optio_codex.snapshots import effective_workdir_exclude


# Self-contained System: explainer for sessions without the keyword-protocol
# docs (which normally explain the convention). Per-wrapper copy is the
# established pattern (claudecode/opencode/grok each carry their own).
_SYSTEM_PREFIX_EXPLAINER = """\
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

- **Your `home/.codex/` directory — the codex session store (rollout
  files under `home/.codex/sessions`, auth, config) — IS preserved
  across resumes** (minus the excluded paths above), so your history
  travels with you even when the underlying process and host change.

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
    """Render RESUME_SECTION_TEMPLATE with the EFFECTIVE exclude list.

    ``effective_workdir_exclude`` is the same resolver the snapshot archive
    uses (``None`` → the codex defaults), so what this section claims is
    preserved is exactly what the snapshot preserves.
    """
    effective = effective_workdir_exclude(workdir_exclude)
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
    host_protocol: bool = True,
    workdir_exclude: list[str] | None = None,
    supports_resume: bool = True,
    file_download: bool = False,
) -> str:
    """Build the full AGENTS.md body.

    ``documentation`` is the keyword-protocol block; the session passes
    ``get_protocol(browser="redirect").documentation``. Defaults (for unit
    tests / standalone callers) to codex's ``redirect`` docs. It must
    always come from the session's ``Protocol`` where one exists — never
    rebuild features at a second site.

    ``host_protocol=False`` omits the keyword-protocol documentation and
    instead includes a self-contained ``System:`` message explainer
    (guide Part 2D); iframe mode always runs with ``host_protocol=True``
    (validated in ``CodexTaskConfig``), the False branch serves
    conversation mode in a later stage.

    ``supports_resume=True`` (default) appends the resume-awareness section
    so the agent watches ``resume.log`` and knows ``home/.codex`` (minus
    ``workdir_exclude``) survives across resumes. ``workdir_exclude`` is
    this task's snapshot exclude list (None → the codex defaults), used to
    keep the section's claims in sync with what is actually preserved.

    ``file_download=True`` appends the downloadables instruction block so
    codex offers files to the human via the ``optio-file:`` sentinel link
    (conversation_ui file-download feature); the wording is comparative when
    the keyword protocol is active (``host_protocol``).
    """
    if file_download:
        consumer_instructions = (
            consumer_instructions.rstrip()
            + downloadables_block(comparative=host_protocol)
        )
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser="redirect")
            )
    else:
        documentation = None
    resume_section: str | None = (
        _render_resume_section(workdir_exclude) if supports_resume else None
    )
    if not host_protocol:
        # The protocol docs normally explain the `System:` convention;
        # without them the composed prompt carries its own explainer.
        resume_section = (
            resume_section + _SYSTEM_PREFIX_EXPLAINER
            if resume_section
            else _SYSTEM_PREFIX_EXPLAINER
        )
    return _compose_agents_md_host(
        consumer_instructions,
        documentation=documentation,
        resume_section=resume_section,
    )
