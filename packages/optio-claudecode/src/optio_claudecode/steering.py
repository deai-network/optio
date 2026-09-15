"""Claude Code's busy-send capability and its steering scaffold.

Measured with CLI 2.1.270 (docs/2026-09-13-conversation-steering-design.md):
a message sent while a turn runs is taken at the next tool result, in the
same turn, and echoed as a ``user`` event (joins-next-step). An interrupt ends
the turn with a ``result`` (``error_during_execution``); the CLI then runs
whatever it still holds.

Fix 13a: the CLI also reports per-message ``command_lifecycle`` (queued /
started / a terminal state) for every stdin message that carries a ``uuid``
(cli-queue-lifecycle.md §1). ``command_lifecycle()`` below is the
``Steering(..., command_lifecycle=...)`` hook this enables: it lets
``interrupt_and_send``'s ``up_to`` ("Send now" through message k) tell which
later queued messages the CLI has not yet started, and wait for k's own
``started`` before re-sending the ones it cancelled.
"""
from __future__ import annotations

from optio_agents.steering import BusySendDeclaration, Steering

BUSY_SEND = BusySendDeclaration(agent="joins-next-step")


def is_turn_end(event: dict) -> bool:
    """A turn ends with its ``result`` event (success or error)."""
    return event.get("type") == "result"


def command_lifecycle(event: dict) -> "tuple[str, str] | None":
    """``(command_uuid, state)`` for a ``command_lifecycle`` event, else
    None. Unknown/malformed shapes (missing or non-string fields) are
    treated as "not a lifecycle event" rather than raising, matching
    cli-queue-lifecycle.md's own robustness note (§4): ignore unknown
    command_uuids and any shape drift the schema does not promise."""
    if event.get("type") != "command_lifecycle":
        return None
    command_uuid, state = event.get("command_uuid"), event.get("state")
    if isinstance(command_uuid, str) and isinstance(state, str):
        return command_uuid, state
    return None


def make_steering(conversation, **kwargs) -> Steering:
    """Steering over a ClaudeCodeConversation, or anything with its surface:
    send, interrupt, is_pending, on_event, emit_event, runtime_model. Wires
    command_lifecycle unconditionally (the pure function above) and
    cancel_async_message only if the conversation actually offers it, so a
    minimal stand-in (tests, another wrapper's fake) without that method
    still works — interrupt_and_send's up_to then just degrades to
    delivering everything queued, per Steering's own contract."""
    return Steering(
        conversation,
        busy_send=lambda: BUSY_SEND.for_model(getattr(conversation, "runtime_model", None)),
        emit=conversation.emit_event,
        is_turn_end=is_turn_end,
        command_lifecycle=command_lifecycle,
        cancel_async_message=getattr(conversation, "cancel_async_message", None),
        **kwargs,
    )
