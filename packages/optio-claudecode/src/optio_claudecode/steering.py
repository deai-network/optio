"""Claude Code's busy-send capability and its steering scaffold.

Measured with CLI 2.1.270 (docs/2026-09-13-conversation-steering-design.md):
a message sent while a turn runs is taken at the next tool result, in the
same turn, and echoed as a ``user`` event (joins-next-step). An interrupt ends
the turn with a ``result`` (``error_during_execution``); the CLI then runs
whatever it still holds.

The CLI also reports per-message ``command_lifecycle`` (queued / started /
a final state) for every stdin message that carries a ``uuid``
(cli-queue-lifecycle.md §1; Fix 13a stamps the steering id as that uuid).
``command_lifecycle()`` below is the ``Steering(..., command_lifecycle=...)``
hook: Steering keeps at most ONE message in the CLI's queue and writes the
next the moment the one in flight reports ``started`` or a final state
(Fix 17, docs/2026-09-15-steering-individual-delivery-design.md).
"""
from __future__ import annotations

from typing import Iterable

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


# command_lifecycle states meaning the CLI took a message into a turn
# (cli-queue-lifecycle.md §1): "completed" can arrive without "started".
_DELIVERED_STATES = frozenset({"started", "completed"})


def _echo_texts(event: dict) -> list[str]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []
    return [
        block["text"] for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


def undelivered_queued(events: "Iterable[tuple[int, dict]]") -> list[tuple[str, str]]:
    """Resume (Fix 19, owner ruling 2026-09-15, finding 6 #2): the messages
    the last run left queued and undelivered, as (id, original text), in the
    order they were sent, from a restored replay buffer ([(seq, event)]).

    An x-optio-queued stays undelivered until its id sees command_lifecycle
    "started" or "completed", or a user echo confirming it: its own uuid,
    or, for a fold's combined echo (only the last uuid) and for buffers
    older than Fix 13a (CLI-minted uuids), its text. "queued", "cancelled"
    (the session end's cancel_queued sweep), "discarded" and "refused" leave
    it undelivered. x-optio-requeued renames it in place. x-optio-resumed
    ends a run: whatever it left undelivered is dropped (the UI showed it
    as "Not delivered"), unless the x-optio-requeued events of the resume
    right after it re-queued it. Malformed events are ignored."""
    pending: dict[str, str] = {}
    stale: dict[str, str] = {}
    for _seq, event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "x-optio-queued":
            qid, text = event.get("id"), event.get("text")
            if isinstance(qid, str) and qid and isinstance(text, str) and qid not in pending:
                pending[qid] = text
        elif kind == "x-optio-requeued":
            old, new = event.get("id"), event.get("new_id")
            if not (isinstance(old, str) and isinstance(new, str) and new):
                continue
            if old in pending:
                pending = {(new if k == old else k): v for k, v in pending.items()}
            elif old in stale:
                pending[new] = stale.pop(old)
        elif kind == "x-optio-resumed":
            stale, pending = pending, {}
        elif kind == "command_lifecycle":
            parsed = command_lifecycle(event)
            if parsed is not None and parsed[1] in _DELIVERED_STATES:
                pending.pop(parsed[0], None)
        elif kind == "user":
            texts = _echo_texts(event)
            uuid = event.get("uuid")
            if isinstance(uuid, str) and uuid in pending:
                del pending[uuid]
                texts = texts[:-1]  # the last block is the uuid's own message
            for text in texts:
                wanted = text.rstrip()
                match = next((k for k, v in pending.items() if v.rstrip() == wanted), None)
                if match is not None:
                    del pending[match]
    return list(pending.items())


def make_steering(conversation, **kwargs) -> Steering:
    """Steering over a ClaudeCodeConversation, or anything with its surface:
    send, interrupt, is_pending, on_event, emit_event, runtime_model. Wires
    command_lifecycle (the pure function above), so queued messages reach
    the CLI one at a time, each written the moment the CLI takes the one
    before it (Fix 17). A stand-in that never emits command_lifecycle still
    works: Steering then writes the next message when the conversation is
    idle again."""
    return Steering(
        conversation,
        busy_send=lambda: BUSY_SEND.for_model(getattr(conversation, "runtime_model", None)),
        emit=conversation.emit_event,
        is_turn_end=is_turn_end,
        command_lifecycle=command_lifecycle,
        **kwargs,
    )
