"""OpenCode ``message.part.updated`` tool parts -> TodoUpdate."""

from __future__ import annotations

from optio_agents.todos import (
    TODO_TOOL_NAMES,
    TodoUpdate,
    normalize_tool_name,
    todo_update_from_payload,
)


def extract_opencode_todo(event: dict) -> TodoUpdate | None:
    if not isinstance(event, dict) or event.get("type") != "message.part.updated":
        return None
    props = event.get("properties")
    part = props.get("part") if isinstance(props, dict) else None
    if not isinstance(part, dict) or part.get("type") != "tool":
        return None
    name = part.get("tool") or ""
    if not isinstance(name, str) or normalize_tool_name(name) not in TODO_TOOL_NAMES:
        return None
    state = part.get("state")
    raw = state.get("input") if isinstance(state, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    return todo_update_from_payload(raw)
