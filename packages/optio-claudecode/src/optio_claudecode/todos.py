"""Claude Code stream-json -> TodoUpdate.

TodoWrite is one assistant tool call whose input is the whole list.
TaskCreate, TaskUpdate, and TaskList split the same fact across two
events: the assistant tool_use carries the fields, and the following
user tool_result carries the id and whether Claude accepted the call.
One extractor instance holds the unpaired tool_use until that result
arrives, including across a resume that folds the transcript and then
keeps listening.
"""

from __future__ import annotations

import json
import re

from optio_agents.todos import (
    TODO_TOOL_NAMES,
    TodoFieldPatch,
    TodoItem,
    TodoProgress,
    TodoUpdate,
    normalize_tool_name,
    todo_update_from_payload,
)

_TASK_TOOLS = frozenset({"taskcreate", "taskupdate", "tasklist", "taskget"})
_CREATE_RE = re.compile(r"^Task #(\d+) created successfully: ([\s\S]*)\Z")
_UPDATE_RE = re.compile(r"^Updated task #\d+\b")
_LIST_LINE_RE = re.compile(r"^#(\d+) \[([A-Za-z_]+)\] (.*)\Z")
_BLOCKED_RE = re.compile(r" \[blocked by #[0-9]+(?:, #[0-9]+)*\]\Z")


def extract_claude_todo(event: dict) -> TodoUpdate | None:
    """A finished assistant ``TodoWrite``. Streaming deltas are not a list.

    A fresh extractor, so a TaskCreate in this event is not paired with
    a later result. Live sessions use one :class:`ClaudeTodoExtractor`.
    """
    return ClaudeTodoExtractor()(event)


class ClaudeTodoExtractor:
    """Folds Claude checklist events into one TodoUpdate per event."""

    def __init__(self) -> None:
        # tool_use id -> (normalized tool name, input). Popped when the
        # matching tool_result arrives.
        self._stash: dict[str, tuple[str, dict]] = {}

    def __call__(self, event: dict) -> TodoUpdate | None:
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "assistant":
            return self._on_assistant(event)
        if kind == "user":
            return self._on_user(event)
        return None

    def _on_assistant(self, event: dict) -> TodoUpdate | None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        found: TodoUpdate | None = None
        malformed = False
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            raw_name = block.get("name") or ""
            if not isinstance(raw_name, str):
                continue
            name = normalize_tool_name(raw_name)
            if name in _TASK_TOOLS:
                payload = block.get("input")
                tool_id = block.get("id")
                if isinstance(payload, dict) and isinstance(tool_id, str):
                    self._stash[tool_id] = (name, payload)
                continue
            if name not in TODO_TOOL_NAMES or found is not None or malformed:
                continue
            payload = block.get("input")
            if not isinstance(payload, dict):
                malformed = True
                continue
            found = todo_update_from_payload(payload)
        if malformed:
            return None
        return found

    def _on_user(self, event: dict) -> TodoUpdate | None:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        steps: list[TodoUpdate] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            step = self._one_result(block)
            if step is not None:
                steps.append(step)
        if not steps:
            return None
        if len(steps) == 1:
            return steps[0]
        return TodoUpdate(items=[], merge=True, source="task", steps=tuple(steps))

    def _one_result(self, block: dict) -> TodoUpdate | None:
        tool_id = block.get("tool_use_id")
        stashed = self._stash.pop(tool_id, None) if isinstance(tool_id, str) else None
        if block.get("is_error") is True:
            return None
        name = stashed[0] if stashed else ""
        payload = stashed[1] if stashed else {}
        text = _result_text(block.get("content")).strip()
        if name == "taskget":
            return None
        if name == "tasklist":
            return _task_list_update(text)
        if name == "taskupdate":
            if not _UPDATE_RE.match(text):
                return None
            return _task_update(payload)
        if name == "taskcreate" or name == "":
            return _task_create(text, payload)
        return None


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _task_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return str(int(text))
        return text
    return None


def _task_create(text: str, payload: dict) -> TodoUpdate | None:
    match = _CREATE_RE.match(text)
    if match is None:
        return None
    subject = match.group(2).strip()
    if not subject:
        return None
    active = payload.get("activeForm")
    if not isinstance(active, str):
        active = None
    return TodoUpdate(
        items=[TodoItem(
            id=match.group(1), text=subject, status="pending", active=active,
            description=_description(payload),
        )],
        merge=True,
        source="task",
    )


def _task_update(payload: dict) -> TodoUpdate | None:
    task_id = _task_id(payload.get("taskId"))
    if task_id is None:
        return None
    status = payload.get("status")
    if status == "deleted":
        return TodoUpdate(
            items=[], merge=True, source="task", remove_ids=(task_id,),
        )
    text = payload.get("subject") if isinstance(payload.get("subject"), str) else None
    active = payload.get("activeForm") if isinstance(payload.get("activeForm"), str) else None
    status_text = status if isinstance(status, str) else None
    description = _description(payload)
    if text is None and active is None and status_text is None and description is None:
        return None
    return TodoUpdate(
        items=[],
        merge=True,
        source="task",
        patches=(TodoFieldPatch(
            id=task_id, text=text, status=status_text, active=active,
            description=description,
        ),),
    )


def _description(payload: dict) -> str | None:
    value = payload.get("description")
    if not isinstance(value, str) or value.strip() == "":
        return None
    return value


def _task_list_update(text: str) -> TodoUpdate | None:
    if text == "No tasks found":
        items: list[TodoItem] = []
    else:
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = _LIST_LINE_RE.match(line)
            if match is None:
                return None
            body = _BLOCKED_RE.sub("", match.group(3))
            items.append(TodoItem(
                id=match.group(1),
                text=body,
                status=match.group(2),
                active=None,
            ))
        if not items:
            return None
    return TodoUpdate(items=items, merge=False, source="task_list")


def _applied_task(update: TodoUpdate) -> bool:
    if update.steps:
        return any(_applied_task(step) for step in update.steps)
    return update.source in ("task", "task_list")


def todo_progress_from_transcript(
    text: str, extractor: ClaudeTodoExtractor,
) -> TodoProgress | None:
    """Fold a Claude jsonl transcript into a checklist.

    None when no TaskCreate, TaskUpdate, or TaskList result was accepted.
    An accepted ``No tasks found`` is an empty list, not None: the task
    tools replaced whatever TodoWrite had written. A tool_use that the
    transcript has not answered stays on ``extractor`` for the live result.
    """
    if not isinstance(text, str):
        return None
    progress = TodoProgress()
    applied = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        update = extractor(event)
        if update is None:
            continue
        progress.apply(update)
        if _applied_task(update):
            applied = True
    if not applied:
        return None
    return progress
