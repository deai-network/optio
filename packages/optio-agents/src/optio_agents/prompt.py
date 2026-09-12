"""Instructions-file composer shared by every optio agent wrapper.

``compose_instructions_file`` renders the file each wrapper writes into its
workdir (``CLAUDE.md`` for optio-claudecode, ``AGENTS.md`` for the others):
the coordination intro plus the keyword-protocol docs, the resume section, the
``System:`` explainer, the task framing, and the consumer's own instructions.
Everything that differs between agents arrives through an
``AgentPromptProfile``; a wrapper's ``prompt.py`` holds its profile and a thin
``compose_agents_md`` and no prompt text of its own (a guard test in
optio-agents-all enforces that).

The keyword-protocol docs are **passed in** by the session
(``get_protocol(...).documentation``) so they reflect the task's protocol
features; the ``profile.browser`` fallback exists for unit tests and
standalone callers only.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from optio_agents.context import SYSTEM_MESSAGE_PREFIX
from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.prompt import RESUME_NOTICE, build_log_channel_prompt


@dataclass(frozen=True)
class AgentPromptProfile:
    """What differs between agents in the instructions file."""

    instructions_file: str = "AGENTS.md"   # named in the REFRESHED: example
    state_dir: str | None = None           # e.g. "home/.claude/"; None = no bullet
    state_dir_contents: str = ""           # e.g. "credentials, settings, and ..."
    preamble: str = ""                     # rendered first (grok's identity line)
    browser: str = "redirect"              # fallback docs only, see module doc
    delivery_section: str = ""             # rendered just before the task framing


_INTRO = """# Coordination protocol with the host harness

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


DEFAULT_CONVERSATION_INSTRUCTIONS = "Let's have a conversation with the user."


# Added whenever the keyword-protocol docs (which explain the System:
# convention) are omitted (host_protocol=False).
_SYSTEM_PREFIX_EXPLAINER = """
(Messages prefixed `System:` on your input channel originate from the
harness coordinating this session, not from the human user.)
"""


_RESUME_SECTION_TEMPLATE = """## Resumes

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
{state_dir_bullet}
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
`2026-05-28T13:15:42Z REFRESHED:{instructions_file}`) — your in-memory copy of
those files is stale and must be re-read before continuing.

{detection_clause}"""


def _resume_steps(instructions_file: str) -> str:
    """What the agent does once it knows it was resumed (either detection style)."""
    return f"""- Verify any tools, processes, or files you previously gathered
  outside the workdir are still where you left them.
- Re-establish anything that's gone (re-launch a server, re-fetch a
  file, etc.) before continuing.
- **If the latest line carries a `REFRESHED:` suffix, re-read each
  listed file** (e.g. `cat ./{instructions_file}`) — the harness updated it
  since your last context snapshot and the version you remember is
  out of date.
- Then resume the work you were doing.
"""


def _detect_by_polling(instructions_file: str) -> str:
    """Pull: poll resume.log on every user message, for sessions the harness
    may resume without telling the agent."""
    return """**At the start of every new incoming user message, read
`./resume.log` first.** Compare the latest line to the value you
remembered last time you checked. If a new line has appeared, treat
the situation as a resume:

""" + _resume_steps(instructions_file) + """
If a resume slips past unnoticed, a failing tool call is the
next-best signal — re-check `./resume.log` then.

You may also be notified of a resume by a `System:` message on your input
channel; when you see one, follow the `resume.log` procedure above.
"""


def _detect_by_notice(instructions_file: str) -> str:
    """Push: every resume is announced by the resume notice, so resume.log is
    read only when it arrives (for its REFRESHED: suffix)."""
    return f"""The harness tells you about every resume with a
`{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}` message on your input channel, so you do
not need to check `./resume.log` before each message. When that message
arrives, read the latest line of `./resume.log` and treat the situation
as a resume:

""" + _resume_steps(instructions_file)


def _state_dir_bullet(profile: AgentPromptProfile) -> str:
    if not profile.state_dir:
        return ""
    text = (
        f"**Your `{profile.state_dir}` directory — {profile.state_dir_contents} — "
        "IS preserved across resumes**, so your history travels with you even "
        "when the underlying process and host change."
    )
    return "\n" + textwrap.fill(
        text, width=72, initial_indent="- ", subsequent_indent="  ",
        break_long_words=False, break_on_hyphens=False,
    ) + "\n"


def render_resume_section(
    profile: AgentPromptProfile,
    workdir_exclude: list[str],
    check_resume_log_every_message: bool = True,
) -> str:
    """Render the resume section for ``profile`` and the effective exclude list.

    ``check_resume_log_every_message`` picks how the agent detects a resume:
    by polling ``resume.log`` on every user message (default), or by waiting
    for the ``System:`` resume notice (only when every resume sends one)."""
    if not workdir_exclude:
        excludes_clause = (
            "**No paths are excluded** — every file in the workdir is preserved."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) — but remember it may be missing when you next look."
        )
    else:
        excludes_str = ", ".join(f"`{p}`" for p in workdir_exclude)
        excludes_clause = (
            f"**Paths matching the snapshot exclude list are NOT preserved**, "
            f"even inside the workdir. The current exclude list is: {excludes_str}."
        )
        outside_clause = (
            "If you need to stash large data, place it outside the workdir "
            "(e.g. `/tmp/`) or inside an excluded subdirectory — but remember "
            "any such location may be missing when you next look."
        )
    detect = _detect_by_polling if check_resume_log_every_message else _detect_by_notice
    return _RESUME_SECTION_TEMPLATE.format(
        excludes_clause=excludes_clause,
        outside_clause=outside_clause,
        state_dir_bullet=_state_dir_bullet(profile),
        instructions_file=profile.instructions_file,
        detection_clause=detect(profile.instructions_file),
    )


def _sandbox_note(fs_isolation_dirs: list[tuple[str, str]]) -> str:
    # The session is sandboxed (claustrum/Landlock); tell the agent its bounds
    # so EACCES on a stray path isn't a mystery. Entries are (path, mode) with
    # claustrum modes; ro/rox grants are flagged read-only so the agent doesn't
    # plan to write there.
    allowed = ", ".join(
        f"`{d}` (read-only)" if m in ("ro", "rox") else f"`{d}`"
        for d, m in fs_isolation_dirs
    )
    return (
        "\n\n**Filesystem access:** This session is filesystem-isolated — "
        "you can access files within these directories (and their "
        f"subdirectories): {allowed}. Files outside them are not accessible "
        "(reads and writes will fail with a permission error)."
    )


def compose_instructions_file(
    consumer_instructions: str,
    *,
    profile: AgentPromptProfile,
    workdir_exclude: list[str],
    documentation: str | None = None,
    supports_resume: bool = True,
    host_protocol: bool = True,
    omit_task_framing: bool = False,
    fs_isolation_dirs: list[tuple[str, str]] | None = None,
    file_download: bool = False,
    check_resume_log_every_message: bool = True,
) -> str:
    """Render a wrapper's instructions file.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      profile: the agent's ``AgentPromptProfile``.
      workdir_exclude: the EFFECTIVE snapshot exclude list (already resolved
        by the wrapper), so the section's preservation claims match the
        snapshot. ``[]`` renders the "no paths are excluded" wording.
      documentation: the keyword-protocol block from the session's protocol
        (``get_protocol(...).documentation``). ``None`` falls back to
        ``ProtocolFeatures(browser=profile.browser)``.
      supports_resume: when False, no resume section (and no state-dir bullet).
      host_protocol: when False, the intro and the protocol docs are omitted
        and the ``System:`` explainer is added instead.
      omit_task_framing: drop the ``## Task`` framing (defaulted conversation
        instructions). The delivery section is still rendered.
      fs_isolation_dirs: (path, mode) pairs the agent may touch under fs
        isolation; renders the sandbox note after the instructions.
      file_download: append the downloadables note (comparative wording when
        ``host_protocol``).
      check_resume_log_every_message: False tells the agent to rely on the
        ``System:`` resume notice instead of polling ``resume.log``. Pass it
        only when every resume sends that notice.
    """
    body = consumer_instructions.rstrip()
    if file_download:
        body += downloadables_block(comparative=host_protocol)
    if fs_isolation_dirs:
        body += _sandbox_note(fs_isolation_dirs)
    if host_protocol:
        if documentation is None:
            documentation = build_log_channel_prompt(
                ProtocolFeatures(browser=profile.browser)
            )
        pre = _INTRO + documentation + "\n"
    else:
        pre = ""
    resume_block = (
        render_resume_section(profile, workdir_exclude, check_resume_log_every_message)
        if supports_resume else ""
    )
    if not host_protocol:
        resume_block += _SYSTEM_PREFIX_EXPLAINER
    if resume_block:
        resume_block += "\n"
    delivery = (
        profile.delivery_section.rstrip() + "\n\n" if profile.delivery_section else ""
    )
    framing = "" if omit_task_framing else BASE_PROMPT_POST + "\n"
    return f"{profile.preamble}{pre}{resume_block}{delivery}{framing}{body}\n"


def downloadables_block(comparative: bool) -> str:
    """Instruction paragraph teaching the agent to offer a file to the human as
    a one-click download via a sentinel markdown link. Two wordings:
    comparative (when the deliverable keyword protocol is active) vs standalone.
    """
    sentinel = "`[name](optio-file:relpath)`"
    if comparative:
        return (
            "\n\n## Downloadable files\n"
            "Deliverables (the DELIVERABLE keyword) are shipped to the host harness "
            "for automatic processing. **Downloadable files are different**: they go "
            "directly to the human user. Produce one only **deliberately**, when the "
            "user interactively asks you for a file. To offer a file for download, "
            "write it into the working directory and present it as a markdown link "
            f"with the optio-file scheme: {sentinel} — where `relpath` is the file's "
            "path relative to the working directory."
        )
    return (
        "\n\n## Downloadable files\n"
        "When the user asks you for a file, write it into the working directory and "
        f"present it to them as a one-click download: a markdown link {sentinel}, "
        "where `relpath` is the file's path relative to the working directory."
    )
