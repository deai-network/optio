"""Conversation todo list -> process progress.

The list is engine-neutral. Each wrapper extracts its own events into a
TodoUpdate; TodoProgress turns that into the percent and message
ctx.report_progress already shows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)

TODO_FILENAME = ".optio-todo.json"
TODO_TOOL_NAMES = frozenset({"todowrite", "writetodo"})

_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


@dataclass(frozen=True)
class TodoItem:
    id: str
    text: str
    status: str
    active: str | None = None


@dataclass(frozen=True)
class TodoUpdate:
    items: list[TodoItem]
    merge: bool


@dataclass(frozen=True)
class ProgressReport:
    percent: int
    message: str


def _status(value: str) -> str:
    return value if value in _STATUSES else "pending"


def assign_ids(items: list[TodoItem]) -> list[TodoItem]:
    """Fill blank ids from the text. A later duplicate gets ``#<index>``."""
    seen: set[str] = set()
    out: list[TodoItem] = []
    for index, item in enumerate(items):
        id_ = item.id or item.text
        if id_ in seen:
            id_ = f"{id_}#{index}"
        seen.add(id_)
        out.append(TodoItem(
            id=id_, text=item.text, status=_status(item.status), active=item.active,
        ))
    return out


class TodoProgress:
    def __init__(self) -> None:
        self.items: list[TodoItem] = []

    def apply(self, update: TodoUpdate) -> ProgressReport | None:
        if update.merge:
            self._merge(update.items)
        else:
            self.items = assign_ids(update.items)
        return self.report()

    def _merge(self, incoming: list[TodoItem]) -> None:
        index = {item.id: i for i, item in enumerate(self.items)}
        for n, item in enumerate(incoming):
            id_ = item.id
            if not id_:
                base = item.text or f"item#{n}"
                id_ = base
                k = n
                while id_ in index:
                    k += 1
                    id_ = f"{base}#{k}"
            stored = TodoItem(
                id=id_, text=item.text, status=_status(item.status), active=item.active,
            )
            if id_ in index:
                self.items[index[id_]] = stored
            else:
                index[id_] = len(self.items)
                self.items.append(stored)

    def report(self) -> ProgressReport | None:
        countable = [item for item in self.items if item.status != "cancelled"]
        total = len(countable)
        if total == 0:
            return None
        done = sum(1 for item in countable if item.status == "completed")
        percent = (100 * done + total // 2) // total
        running = [
            item.active or item.text
            for item in self.items
            if item.status == "in_progress"
        ]
        message = "; ".join(running) if running else f"{done} of {total} done"
        return ProgressReport(percent=percent, message=message)

    def to_json(self) -> str:
        rows = []
        for item in self.items:
            row: dict = {"id": item.id, "text": item.text, "status": item.status}
            if item.active is not None:
                row["active"] = item.active
            rows.append(row)
        return json.dumps({"items": rows}, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> TodoProgress | None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return None
        items: list[TodoItem] = []
        for entry in data["items"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                return None
            active = entry.get("active")
            if active is not None and not isinstance(active, str):
                return None
            id_ = entry.get("id", "")
            if not isinstance(id_, str):
                return None
            status = entry.get("status", "pending")
            if not isinstance(status, str):
                status = "pending"
            items.append(TodoItem(id=id_, text=entry["text"], status=status, active=active))
        progress = cls()
        progress.items = assign_ids(items)
        return progress


def normalize_tool_name(name: str) -> str:
    return name.casefold().replace("_", "")


def todo_update_from_payload(payload: dict) -> TodoUpdate | None:
    """A ``todos`` array, or a ``plan`` array of ``{step, status}``."""
    if not isinstance(payload, dict):
        return None
    merge = payload.get("merge") is True
    raw = payload.get("todos")
    from_plan = False
    if raw is None and "plan" in payload:
        raw = payload.get("plan")
        from_plan = True
    if not isinstance(raw, list):
        return None
    items: list[TodoItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        if from_plan:
            text = entry.get("step", entry.get("text", entry.get("content")))
        else:
            text = entry.get("content", entry.get("text"))
        if not isinstance(text, str):
            return None
        status = entry.get("status", "pending")
        if not isinstance(status, str):
            status = "pending"
        id_ = entry.get("id", "")
        if not isinstance(id_, str):
            id_ = ""
        active = entry.get("activeForm", entry.get("active"))
        if not isinstance(active, str):
            active = None
        items.append(TodoItem(id=id_, text=text, status=status, active=active))
    return TodoUpdate(items=items, merge=merge)


def _acp_update(event: dict) -> dict | None:
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    return update if isinstance(update, dict) else None


def extract_acp_todo(event: dict) -> TodoUpdate | None:
    """ACP ``plan`` session update, or a completed todo_write tool call."""
    if not isinstance(event, dict):
        return None
    update = _acp_update(event)
    if update is None:
        return None
    kind = update.get("sessionUpdate")
    if kind == "plan":
        entries = update.get("entries")
        if not isinstance(entries, list):
            return None
        todos = []
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            todos.append({
                "id": entry.get("id", ""),
                "content": entry.get("content"),
                "status": entry.get("status", "pending"),
            })
        return todo_update_from_payload({"todos": todos, "merge": False})
    if kind not in ("tool_call", "tool_call_update"):
        return None
    name = update.get("title") or update.get("kind") or ""
    if not isinstance(name, str) or normalize_tool_name(name) not in TODO_TOOL_NAMES:
        return None
    raw = update.get("rawInput")
    if not isinstance(raw, dict):
        return None
    return todo_update_from_payload(raw)


class TodoTracker:
    """One conversation's list, subscribed to its event stream."""

    def __init__(self, extract, ctx) -> None:
        self.extract = extract
        self.ctx = ctx
        self.progress = TodoProgress()
        self._unsub = None

    def observe(self, event: dict) -> None:
        try:
            update = self.extract(event)
            if update is None:
                return
            report = self.progress.apply(update)
        except Exception:
            _LOG.exception("todo event ignored")
            return
        if report is not None:
            self.ctx.report_progress(report.percent, report.message)

    def arm(self, conversation) -> None:
        if self._unsub is None:
            self._unsub = conversation.on_event(self.observe)

    def disarm(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def restore(self, host) -> bool:
        """Load ``.optio-todo.json``. True when a valid file was read."""
        path = f"{host.workdir.rstrip('/')}/{TODO_FILENAME}"
        try:
            raw = await host.fetch_bytes_from_host(path)
        except FileNotFoundError:
            return False
        except Exception:
            _LOG.exception("todo list read failed")
            return False
        try:
            text = raw.decode("utf-8")
        except Exception:
            return False
        loaded = TodoProgress.from_json(text)
        if loaded is None:
            return False
        self.progress = loaded
        report = self.progress.report()
        if report is not None:
            self.ctx.report_progress(report.percent, report.message)
        return True

    async def save(self, host) -> None:
        try:
            await host.write_text(TODO_FILENAME, self.progress.to_json())
        except Exception:
            _LOG.exception("todo list persist failed")
