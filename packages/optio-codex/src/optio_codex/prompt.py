"""AGENTS.md composition for optio-codex.

Codex reads an ``AGENTS.md`` file in its workdir. The base prompt teaches
the agent how to coordinate with the host harness; the consumer's task
description is appended verbatim.
"""

from optio_agents.protocol import ProtocolFeatures, build_log_channel_prompt


_CODEX_INTRO = """# Coordination protocol with the host (optio-codex)

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
    """Build the full AGENTS.md body."""
    if host_protocol:
        documentation = build_log_channel_prompt(ProtocolFeatures(browser="suppress"))
        base_prompt_pre = _CODEX_INTRO + documentation
    else:
        base_prompt_pre = ""
    body = consumer_instructions.rstrip()
    pre = f"{base_prompt_pre}\n" if base_prompt_pre else ""
    return f"{pre}{BASE_PROMPT_POST}\n{body}\n"