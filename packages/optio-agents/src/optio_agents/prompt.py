"""Shared AGENTS.md composer for optio coordination-protocol agents.

Owned by ``optio-agents`` so that ``optio-opencode`` and
``optio-claudecode`` (and any future agent package) compose the same
AGENTS.md framing. The keyword-protocol documentation is **passed in** by
the caller (built via ``optio_agents.protocol.build_log_channel_prompt`` /
``get_protocol().documentation``) so it reflects the caller's protocol
mode rather than a fixed all-keywords block.

Consumer packages stay responsible for their own resume-specific content
(if any) and pass it in via the ``resume_section`` parameter.
"""


_INTRO = """# Coordination protocol with the host (optio)

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


def compose_agents_md(
    consumer_instructions: str,
    *,
    documentation: str,
    resume_section: str | None = None,
) -> str:
    """Build the AGENTS.md body for an optio-coordinated agent task.

    Args:
      consumer_instructions: the task author's prompt, appended verbatim
        (trailing whitespace stripped).
      documentation: the keyword-protocol documentation block for the
        caller's protocol mode (e.g. ``get_protocol(browser=…).documentation``).
      resume_section: optional pre-rendered resume-detection section to
        insert between the protocol docs and ``BASE_PROMPT_POST``.
        ``None`` (default) omits the section.
    """
    pre = _INTRO + documentation
    body = consumer_instructions.rstrip()
    resume_block = (resume_section + "\n") if resume_section else ""
    return f"{pre}\n{resume_block}{BASE_PROMPT_POST}\n{body}\n"
