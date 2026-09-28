"""Conversation todo list -> process progress.

The list is engine-neutral. Each wrapper extracts its own events into a
TodoUpdate; TodoProgress turns that into the percent and message
ctx.report_progress already shows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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
