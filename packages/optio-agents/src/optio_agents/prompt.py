"""Shared AGENTS.md composer for optio coordination-protocol agents.

Owned by ``optio-agents`` so that ``optio-opencode`` and
``optio-claudecode`` (and any future agent package) compose the same
AGENTS.md base text from the single source of truth — the LLM-facing
keyword protocol documented in :data:`optio_agents.protocol.prompt.LOG_CHANNEL_PROMPT`.

Consumer packages stay responsible for their own resume-specific content
(if any) and pass it in via the ``resume_section`` parameter.
"""

from optio_agents.protocol.prompt import LOG_CHANNEL_PROMPT


_INTRO = """# Coordination protocol with the host (optio)

You are running inside a coordination harness. Follow these conventions
throughout the session.

"""


BASE_PROMPT_PRE = _INTRO + LOG_CHANNEL_PROMPT


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
