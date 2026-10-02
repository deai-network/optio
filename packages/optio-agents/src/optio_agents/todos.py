"""Conversation todo list -> process progress.

The list is engine-neutral. Each wrapper extracts its own events into a
TodoUpdate; TodoProgress turns that into the percent and message
ctx.report_progress already shows.
"""

from __future__ import annotations

import asyncio
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
    description: str | None = None


@dataclass(frozen=True)
class TodoFieldPatch:
    """One row, changing only the fields that are set.

    ``None`` leaves that field as it was. A status the checklist does not
    know is ignored, so a partial update cannot reset a row to pending.
    """

    id: str
    text: str | None = None
    status: str | None = None
    active: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class TodoUpdate:
    items: list[TodoItem]
    merge: bool
    # "tool" is a todo_write/write_todo payload. "plan" is an ACP plan
    # snapshot. The tool is the authority for a row it named: a later plan
    # must not mark that row completed while the tool still says in_progress.
    # "task" is a Claude TaskCreate / TaskUpdate: items are new rows, patches
    # change named fields, remove_ids drops rows. "task_list" is a TaskList
    # snapshot and replaces membership.
    source: str = "tool"
    patches: tuple[TodoFieldPatch, ...] = ()
    remove_ids: tuple[str, ...] = ()
    # Several tool results arrived in one event. Applied in order; the
    # fields above are ignored when this is set.
    steps: tuple[TodoUpdate, ...] = ()


@dataclass(frozen=True)
class ProgressReport:
    percent: int
    message: str | None


def _status(value: str) -> str:
    # Agents spell the same state both ways. One L must not fall through
    # to pending, or a cancelled row is counted and not struck through.
    if value == "canceled":
        return "cancelled"
    return value if value in _STATUSES else "pending"


def _known_status(value: str) -> str | None:
    """A status the checklist stores, or None when the value is not one."""
    if value == "canceled":
        return "cancelled"
    if value in _STATUSES:
        return value
    return None


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
            description=item.description,
        ))
    return out


class TodoProgress:
    def __init__(self) -> None:
        self.items: list[TodoItem] = []
        # Last status a todo tool gave each row, keyed by text. A plan
        # snapshot is not allowed to finish a row the tool left in progress.
        self._tool_status: dict[str, str] = {}

    def apply(self, update: TodoUpdate) -> ProgressReport | None:
        if update.steps:
            report = None
            for step in update.steps:
                report = self.apply(step)
            return report
        if update.source == "task_list":
            self._replace_task_list(update.items)
            self._remember_tool(self.items, replace=True)
            return self.report()
        if update.source == "task":
            if update.items:
                self._merge(update.items)
            if update.patches:
                self._apply_patches(update.patches)
            if update.remove_ids:
                self._remove(update.remove_ids)
            touched = {item.id for item in update.items}
            touched.update(patch.id for patch in update.patches)
            touched.difference_update(update.remove_ids)
            self._remember_tool(
                [item for item in self.items if item.id in touched],
                replace=False,
            )
            return self.report()
        items = update.items
        if update.source == "plan":
            items = [self._respect_tool(item) for item in items]
        if update.merge:
            self._merge(items)
        else:
            self.items = assign_ids(items)
        if update.source == "tool":
            self._remember_tool(items, replace=not update.merge)
        return self.report()

    def _respect_tool(self, item: TodoItem) -> TodoItem:
        status = _status(item.status)
        if status == "cancelled":
            return item
        if status == "completed" and self._tool_status.get(item.text) == "in_progress":
            return TodoItem(
                id=item.id, text=item.text, status="in_progress", active=item.active,
                description=item.description,
            )
        return item

    def _remember_tool(self, items: list[TodoItem], *, replace: bool) -> None:
        # Remember the status we stored, which is the normalized one.
        # A full replace drops authority for rows the tool no longer names.
        stored = {item.text: item.status for item in self.items}
        remembered = {
            item.text: stored[item.text] for item in items if item.text in stored
        }
        if replace:
            self._tool_status = remembered
        else:
            self._tool_status.update(remembered)

    def reconcile_tool(self, update: TodoUpdate) -> bool:
        """Copy a replayed todo tool's statuses onto matching rows.

        Does not add, drop, or reorder rows: a shortened replay must not
        replace the saved list. Returns True when a status changed.
        """
        incoming = {item.text: _status(item.status) for item in update.items}
        self._tool_status.update(incoming)
        changed = False
        updated: list[TodoItem] = []
        for item in self.items:
            status = incoming.get(item.text)
            if status is not None and status != item.status:
                updated.append(TodoItem(
                    id=item.id, text=item.text, status=status, active=item.active,
                    description=item.description,
                ))
                changed = True
            else:
                updated.append(item)
        if changed:
            self.items = updated
        return changed

    def _replace_task_list(self, items: list[TodoItem]) -> None:
        """TaskList is the membership. A line can grow a suffix the row
        does not store: `` (owner)`` or `` [blocked by #…]``. A subject we
        already have is kept, and so are its active form and description,
        which the line does not carry."""
        previous = {item.id: item for item in self.items}
        out: list[TodoItem] = []
        for item in items:
            text = item.text
            active = None
            description = None
            old = previous.get(item.id)
            if old is not None:
                active = old.active
                description = old.description
                if (
                    text == old.text
                    or text.startswith(old.text + " (")
                    or text.startswith(old.text + " [")
                ):
                    text = old.text
            out.append(TodoItem(
                id=item.id, text=text, status=_status(item.status), active=active,
                description=description,
            ))
        self.items = out

    def _apply_patches(self, patches: tuple[TodoFieldPatch, ...]) -> None:
        index = {item.id: i for i, item in enumerate(self.items)}
        for patch in patches:
            status = _known_status(patch.status) if patch.status is not None else None
            slot = index.get(patch.id)
            if slot is None:
                text = patch.text if patch.text is not None else f"#{patch.id}"
                self.items.append(TodoItem(
                    id=patch.id,
                    text=text,
                    status=status or "pending",
                    active=patch.active,
                    description=patch.description,
                ))
                index[patch.id] = len(self.items) - 1
                continue
            previous = self.items[slot]
            text = previous.text if patch.text is None else patch.text
            active = previous.active if patch.active is None else patch.active
            description = (
                previous.description if patch.description is None else patch.description
            )
            self.items[slot] = TodoItem(
                id=previous.id,
                text=text,
                status=previous.status if status is None else status,
                active=active,
                description=description,
            )
            if text != previous.text and not any(
                item.text == previous.text for item in self.items
            ):
                self._tool_status.pop(previous.text, None)

    def _remove(self, ids: tuple[str, ...]) -> None:
        drop = set(ids)
        removed = [item.text for item in self.items if item.id in drop]
        self.items = [item for item in self.items if item.id not in drop]
        kept = {item.text for item in self.items}
        for text in removed:
            if text not in kept:
                self._tool_status.pop(text, None)

    def _merge(self, incoming: list[TodoItem]) -> None:
        index = {item.id: i for i, item in enumerate(self.items)}
        # First row of a given text. A plan snapshot has no ids, so the row
        # id is the text; a later todo merge addresses that same row as "2".
        by_text: dict[str, int] = {}
        for i, item in enumerate(self.items):
            by_text.setdefault(item.text, i)
        for n, item in enumerate(incoming):
            id_ = item.id
            if not id_:
                base = item.text or f"item#{n}"
                id_ = base
                k = n
                while id_ in index:
                    k += 1
                    id_ = f"{base}#{k}"
            status = _status(item.status)
            if id_ in index:
                slot = index[id_]
            elif item.text in by_text:
                slot = by_text[item.text]
                index[id_] = slot
            else:
                slot = None
            if slot is None:
                stored = TodoItem(
                    id=id_, text=item.text, status=status, active=item.active,
                    description=item.description,
                )
                index[id_] = len(self.items)
                by_text.setdefault(item.text, len(self.items))
                self.items.append(stored)
                continue
            previous = self.items[slot]
            active = item.active if item.active is not None else previous.active
            description = (
                item.description if item.description is not None else previous.description
            )
            self.items[slot] = TodoItem(
                id=previous.id, text=item.text or previous.text,
                status=status, active=active, description=description,
            )
            by_text.setdefault(item.text, slot)

    def report(self) -> ProgressReport | None:
        countable = [item for item in self.items if item.status != "cancelled"]
        total = len(countable)
        if total == 0:
            return None
        done = sum(1 for item in countable if item.status == "completed")
        percent = (100 * done + total // 2) // total
        # Position is among the rows that count. Cancelled rows are skipped,
        # so "Task 3/4" is the third countable row of four.
        running = [
            f'Task {n}/{total}, "{item.text}"'
            for n, item in enumerate(countable, start=1)
            if item.status == "in_progress"
        ]
        # Nothing in progress: move the percent and send no sentence.
        message = (
            "Now working on " + " and ".join(running) if running else None
        )
        return ProgressReport(percent=percent, message=message)

    def widget_items(self) -> list[dict]:
        """The checklist the conversation view renders. No tool-authority field."""
        rows = []
        for item in self.items:
            row: dict = {"id": item.id, "text": item.text, "status": item.status}
            if item.active is not None:
                row["active"] = item.active
            if item.description:
                row["description"] = item.description
            rows.append(row)
        return rows

    def to_json(self) -> str:
        rows = []
        for item in self.items:
            row: dict = {"id": item.id, "text": item.text, "status": item.status}
            if item.active is not None:
                row["active"] = item.active
            if item.description:
                row["description"] = item.description
            # The todo tool's last status. A later plan must not finish a row
            # this still says is in progress, including after a resume whose
            # history replay never arrives. Absent on rows no tool has named.
            tool = self._tool_status.get(item.text)
            if tool is not None:
                row["tool"] = tool
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
        tools: dict[str, str] = {}
        for entry in data["items"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                return None
            active = entry.get("active")
            if active is not None and not isinstance(active, str):
                return None
            description = entry.get("description")
            if description is not None and not isinstance(description, str):
                return None
            if isinstance(description, str) and description.strip() == "":
                description = None
            id_ = entry.get("id", "")
            if not isinstance(id_, str):
                return None
            status = entry.get("status", "pending")
            if not isinstance(status, str):
                status = "pending"
            tool = entry.get("tool")
            if isinstance(tool, str) and (tool in _STATUSES or tool == "canceled"):
                tool = _status(tool)
                # A file saved while a plan had already marked the row done
                # still carries the tool's in-progress authority.
                if tool == "in_progress" and _status(status) == "completed":
                    status = "in_progress"
                tools[entry["text"]] = tool
            items.append(TodoItem(
                id=id_, text=entry["text"], status=status, active=active,
                description=description,
            ))
        progress = cls()
        progress.items = assign_ids(items)
        progress._tool_status = tools
        return progress


def normalize_tool_name(name: str) -> str:
    return name.casefold().replace("_", "")


def todo_update_from_payload(payload: dict, *, source: str = "tool") -> TodoUpdate | None:
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
        # ACP plan has no cancelled status. Grok sends completed plus this flag.
        meta = entry.get("_meta")
        if isinstance(meta, dict) and meta.get("cancelled") is True:
            status = "cancelled"
        id_ = entry.get("id", "")
        if not isinstance(id_, str):
            id_ = ""
        active = entry.get("activeForm", entry.get("active"))
        if not isinstance(active, str):
            active = None
        items.append(TodoItem(id=id_, text=text, status=status, active=active))
    return TodoUpdate(items=items, merge=merge, source=source)


def _acp_update(event: dict) -> dict | None:
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    return update if isinstance(update, dict) else None


def _acp_tool_name(update: dict) -> str:
    """The todo tool's name. Grok's wire title is "Updating plan"; the
    tool name is on ``_meta`` and on ``rawInput.variant``."""
    meta = update.get("_meta")
    if isinstance(meta, dict):
        tool = meta.get("x.ai/tool")
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            if normalize_tool_name(tool["name"]) in TODO_TOOL_NAMES:
                return tool["name"]
    raw = update.get("rawInput")
    if isinstance(raw, dict) and isinstance(raw.get("variant"), str):
        if normalize_tool_name(raw["variant"]) in TODO_TOOL_NAMES:
            return raw["variant"]
    title = update.get("title") or update.get("kind") or ""
    return title if isinstance(title, str) else ""


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
                "_meta": entry.get("_meta"),
            })
        return todo_update_from_payload(
            {"todos": todos, "merge": False}, source="plan",
        )
    if kind not in ("tool_call", "tool_call_update"):
        return None
    if normalize_tool_name(_acp_tool_name(update)) not in TODO_TOOL_NAMES:
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
        # Stay false until restore() decides the in-memory list, or observe()
        # applies a live update. A resume that dies during widget setup must
        # not overwrite the file the workdir restore just put back.
        self._save_ok = False
        # While set, a replay may correct statuses from a todo tool call but
        # must not add, drop, or reorder rows, and a replayed plan is ignored.
        self._replay_reconcile = False
        # While set, replay may update the list but must not append a
        # progress line for each historical change.
        self._replay_silent = False
        # Last sentence appended to the process log. Grok delivers one todo
        # change as a tool call, again as its update, then as a plan.
        self._logged_message: str | None = None
        self._logged_percent: int | None = None
        # create_task's result is weakly referenced (3.12+). Dropping it lets
        # the publish vanish before the widget write runs.
        self._publishes: set[asyncio.Task] = set()
        # One widget write at a time, reading the list when the write starts,
        # so an earlier todo event cannot land after a later one.
        self._publish_lock = asyncio.Lock()

    def begin_replay(self, *, reconcile: bool = False) -> None:
        """History replay is about to run.

        Updates still change the list and the checklist. They do not append
        a progress line: the resumed agent is not working through those old
        steps. ``end_replay`` announces the list once, as it stands then.

        When ``reconcile`` is set, a saved list is already loaded. A todo
        tool call may correct a matching row's status, and a replayed plan
        is ignored, so a shortened replay cannot replace the list.
        """
        self._replay_silent = True
        self._replay_reconcile = reconcile

    def end_replay(self) -> None:
        """Replay has finished. One progress line for the list now."""
        if not self._replay_silent and not self._replay_reconcile:
            return
        self._replay_silent = False
        self._replay_reconcile = False
        # The sentence from before the window must not swallow this one.
        self._logged_message = None
        self._logged_percent = None
        self._emit(self.progress.report())

    def observe(self, event: dict) -> None:
        try:
            update = self.extract(event)
            if update is None:
                return
            if self._replay_reconcile:
                if update.source != "tool":
                    return
                if not self.progress.reconcile_tool(update):
                    self._save_ok = True
                    return
                report = self.progress.report()
            else:
                report = self.progress.apply(update)
        except Exception:
            _LOG.exception("todo event ignored")
            return
        self._save_ok = True
        self._emit(report)
        self._schedule_widget_todos()

    def _emit(self, report: ProgressReport | None) -> None:
        """Log a status sentence once. A repeat of the same sentence does
        not append another line; a changed percent still moves the bar.
        An empty list, after one that had a sentence, sets the bar to
        indeterminate: the work is still active and there is no percentage."""
        if self._replay_silent:
            return
        if report is None:
            if self.progress.items:
                return
            if self._logged_message is None and self._logged_percent is None:
                return
            self.ctx.report_progress(None)
            self._logged_message = None
            self._logged_percent = None
            return
        if report.message == self._logged_message:
            if report.percent != self._logged_percent:
                self.ctx.report_progress(report.percent, None)
                self._logged_percent = report.percent
            return
        self.ctx.report_progress(report.percent, report.message)
        self._logged_message = report.message
        self._logged_percent = report.percent

    def _schedule_widget_todos(self) -> None:
        """Hand the list to the conversation widget. Sync on purpose: observe
        stays sync so existing callers do not have to await it. No running
        loop (the unit tests) or no set_widget_todos (headless) skips."""
        if getattr(self.ctx, "set_widget_todos", None) is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._publish_widget_todos())
        self._publishes.add(task)
        task.add_done_callback(self._publishes.discard)

    def arm(self, conversation) -> None:
        if self._unsub is None:
            self._unsub = conversation.on_event(self.observe)

    def disarm(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def use_rebuilt(self, progress: TodoProgress) -> None:
        """Use a checklist folded from history instead of the saved file.

        One progress line, then the widget publish. An empty fold clears
        the bar the same way an emptied live list does.
        """
        self.progress = progress
        self._save_ok = True
        if not progress.items:
            # _emit only clears the bar after a sentence this tracker logged.
            self._logged_message = ""
        self._emit(self.progress.report())
        await self._publish_widget_todos()

    async def restore(self, host) -> bool:
        """Load ``.optio-todo.json``. True when a valid file was read."""
        self._save_ok = True
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
        self._emit(self.progress.report())
        await self._publish_widget_todos()
        return True

    async def _publish_widget_todos(self) -> None:
        publish = getattr(self.ctx, "set_widget_todos", None)
        if publish is None:
            return
        async with self._publish_lock:
            items = self.progress.widget_items()
            try:
                await publish(items)
            except Exception:
                _LOG.exception("todo list widget publish failed")

    async def save(self, host) -> None:
        if not self._save_ok:
            return
        try:
            await host.write_text(TODO_FILENAME, self.progress.to_json())
        except Exception:
            _LOG.exception("todo list persist failed")
