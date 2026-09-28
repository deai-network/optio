"""Codex app-server events -> TodoUpdate."""

from __future__ import annotations

import json

from optio_agents.todos import TodoUpdate, todo_update_from_payload


def _from_item(item: object) -> TodoUpdate | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("type") or ""
    if name != "update_plan":
        return None
    args = item.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict) or "plan" not in args:
        return None
    return todo_update_from_payload({"plan": args.get("plan")})


def extract_codex_todo(event: dict) -> TodoUpdate | None:
    """``update_plan`` on an ``item/completed`` notification, or the item itself."""
    if not isinstance(event, dict):
        return None
    if event.get("method") == "item/completed":
        params = event.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        return _from_item(item)
    return _from_item(event)
