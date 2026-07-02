"""AGENTS.md composition for optio-cursor.

Cursor CLI (``cursor-agent``) reads an ``AGENTS.md`` file in its workdir.
The base prompt teaches the agent how to coordinate with the host harness
(which log file to append status/deliverable/done/error lines to, where to
put deliverable files); the consumer's own task description is then
appended verbatim.

Adapted from optio-grok's ``compose_agents_md`` (both agents read
AGENTS.md). Delta vs grok: the browser feature is ``redirect`` (cursor
login prints a URL when ``NO_OPEN_BROWSER=1``; the agent surfaces it via
``BROWSER:`` lines), so the ``BROWSER:`` keyword docs ARE included.
"""

from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt


_CURSOR_INTRO = """# Coordination protocol with the host (optio-cursor)

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
    host_protocol: bool = True,
) -> str:
    """Build the full AGENTS.md body.

    Parameters:
      consumer_instructions: the task author's prompt, appended verbatim.
      host_protocol: when False, omit the keyword-protocol documentation
        block entirely (a Stage-6 concern; the branch is kept). Default
        True — iframe mode's only completion signal is the optio.log
        keyword channel.
    """
    if host_protocol:
        documentation = build_log_channel_prompt(ProtocolFeatures(browser="redirect"))
        base_prompt_pre = _CURSOR_INTRO + documentation
    else:
        base_prompt_pre = ""
    body = consumer_instructions.rstrip()
    pre = f"{base_prompt_pre}\n" if base_prompt_pre else ""
    return f"{pre}{BASE_PROMPT_POST}\n{body}\n"
