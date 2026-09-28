"""Claude Code stream-json -> TodoUpdate."""

from __future__ import annotations

from optio_agents.todos import (
    TODO_TOOL_NAMES,
    TodoUpdate,
    normalize_tool_name,
    todo_update_from_payload,
)


def extract_claude_todo(event: dict) -> TodoUpdate | None:
    """A finished assistant ``TodoWrite``. Streaming deltas are not a list."""
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return None
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name") or ""
        if not isinstance(name, str) or normalize_tool_name(name) not in TODO_TOOL_NAMES:
            continue
        payload = block.get("input")
        if not isinstance(payload, dict):
            return None
        return todo_update_from_payload(payload)
    return None
