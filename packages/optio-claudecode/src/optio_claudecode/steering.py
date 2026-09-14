"""Claude Code's busy-send capability and its steering scaffold.

Measured with CLI 2.1.270 (docs/2026-09-13-conversation-steering-design.md):
a message sent while a turn runs is taken at the next tool result, in the
same turn, and echoed as a ``user`` event (joins-next-step). An interrupt ends
the turn with a ``result`` (``error_during_execution``); the CLI then runs
whatever it still holds.
"""
from __future__ import annotations

from optio_agents.steering import BusySendDeclaration, Steering

BUSY_SEND = BusySendDeclaration(agent="joins-next-step")


def is_turn_end(event: dict) -> bool:
    """A turn ends with its ``result`` event (success or error)."""
    return event.get("type") == "result"


def make_steering(conversation, **kwargs) -> Steering:
    """Steering over a ClaudeCodeConversation, or anything with its surface:
    send, interrupt, is_pending, on_event, emit_event, runtime_model."""
    return Steering(
        conversation,
        busy_send=lambda: BUSY_SEND.for_model(getattr(conversation, "runtime_model", None)),
        emit=conversation.emit_event,
        is_turn_end=is_turn_end,
        **kwargs,
    )
