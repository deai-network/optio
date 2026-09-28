"""Antigravity transcript lines -> TodoUpdate."""

from __future__ import annotations

import json

from optio_agents.todos import (
    TODO_TOOL_NAMES,
    TodoUpdate,
    normalize_tool_name,
    todo_update_from_payload,
)


def extract_antigravity_todo(event: dict) -> TodoUpdate | None:
    if not isinstance(event, dict) or event.get("type") != "PLANNER_RESPONSE":
        return None
    calls = event.get("tool_calls")
    if not isinstance(calls, list):
        return None
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name") or ""
        if not isinstance(name, str) or normalize_tool_name(name) not in TODO_TOOL_NAMES:
            continue
        args = call.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return None
        if not isinstance(args, dict):
            return None
        return todo_update_from_payload(args)
    return None
