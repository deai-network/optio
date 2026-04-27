"""System-prompt composition for optio-opencode.

The base prompt teaches opencode (via AGENTS.md) how to coordinate with the
host harness: which log file to append status/deliverable/done/error lines
to, where to put deliverable files, and how the human expects to be
addressed. The consumer's own task description is then appended verbatim.
"""


BASE_PROMPT_PRE = """# Coordination protocol with the host (optio-opencode)

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
    workdir_exclude: list[str] | None,
    supports_resume: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      workdir_exclude: the snapshot exclude list for this task. Mandatory:
        callers must pass it explicitly to prevent silent desync between
        archive.py defaults and the prompt's claims about what's preserved.
        Pass None to render with the framework defaults.
      supports_resume: when False, the resume-detection section is omitted
        from the prompt. Default True.
    """
    body = consumer_instructions.rstrip()
    # Resume section landing here in Task 3.
    return f"{BASE_PROMPT_PRE}\n{BASE_PROMPT_POST}\n{body}\n"
