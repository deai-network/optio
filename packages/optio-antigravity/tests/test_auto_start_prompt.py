"""The auto-start kickoff is a harness message, not a human one."""

from optio_agents.context import SYSTEM_MESSAGE_PREFIX
from optio_antigravity.host_actions import AUTO_START_PROMPT


def test_auto_start_prompt_is_a_system_message():
    # The kickoff comes from the harness: it carries the System: prefix every
    # harness message has, so the agent (told that the human may not be
    # present) is not given a turn that reads as the human typing, and the
    # conversation widget renders it as a System row, not a user bubble.
    assert AUTO_START_PROMPT == (
        f"{SYSTEM_MESSAGE_PREFIX}Read AGENTS.md and execute the task it describes"
    )
