"""Shared optio coordination prompt for log/deliverables-protocol agents.

Owned by ``optio-host`` so that ``optio-opencode`` and
``optio-claudecode`` (and any future agent package) compose the same
AGENTS.md base text from the same single source of truth. Consumer
packages stay responsible for their own resume-specific content (if any)
and pass it in via the ``resume_section`` parameter.
"""


BASE_PROMPT_PRE = """# Coordination protocol with the host (optio)

You are running inside a coordination harness. Follow these conventions
throughout the session.

## Log channel

Append one line per entry to `./optio.log` in this directory. Each line
must start with one of:

- `STATUS:` — progress update for the human. Optional leading percent,
  e.g. `STATUS: 50% counting my fingers`.
- `DELIVERABLE:` — absolute or workdir-relative path to a file you've
  just produced, e.g. `DELIVERABLE: ./deliverables/summary.md`.
- `DONE` — you have finished the task. May be followed by an optional
  summary on the same line: `DONE: wrote the report`.
- `ERROR` — you cannot continue. May be followed by an optional
  message: `ERROR: provider auth failed`.

**Every entry must end with a newline character (`\\n`).** The host
reads `optio.log` with a line-oriented tailer that only emits a line
once it sees `\\n`; an entry written without a trailing newline (e.g.
via `printf 'DONE'`) will be buffered indefinitely and never reach the
host. Use `echo`, `>>` redirection of a heredoc, or any other mechanism
that guarantees a trailing newline. If unsure, double-check with
`tail -c 1 ./optio.log` — the result must be a newline.

After writing `DONE` or `ERROR`, the session will terminate. Do not
write further lines.

## Deliverables

Place files you want to hand back to the host under `./deliverables/`.
For each file, write a `DELIVERABLE:` log line *after* the file exists
and its contents are final. The host fetches files by reading these
log lines.
"""


BASE_PROMPT_POST = """## Task

Here comes the description of your actual task to complete. Throughout
the task, you are encouraged to narrate progress — both on the normal
UI and in parallel using the `STATUS:` messages explained above — and
you are free to ask questions and dialogue with the human. They are
also working on the same task and will cooperate with you on achieving
the same goals. So:
"""


def compose_agents_md(
    consumer_instructions: str,
    *,
    resume_section: str | None = None,
) -> str:
    """Build the AGENTS.md body for an optio-coordinated agent task.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      resume_section: optional pre-rendered resume-detection section to
        insert between ``BASE_PROMPT_PRE`` and ``BASE_PROMPT_POST``.
        ``None`` (default) omits the section, which is what packages
        that don't support resume should pass.
    """
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{BASE_PROMPT_PRE}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
