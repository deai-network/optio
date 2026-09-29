"""Progress rule for a conversation's todo list."""

import asyncio
import json

from optio_agents.todos import TodoItem, TodoProgress, TodoUpdate


def _item(text, status, id="", active=None):
    return TodoItem(id=id, text=text, status=status, active=active)


def test_all_pending_is_zero():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "pending", "1"),
        _item("b", "pending", "2"),
        _item("c", "pending", "3"),
    ], merge=False))
    assert report is not None
    assert report.percent == 0
    assert report.message == "0 of 3 done"


def test_mixed_uses_active_text():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("Done one", "completed", "1"),
        _item("Write", "in_progress", "2", active="Writing"),
        _item("Later", "pending", "3"),
    ], merge=False))
    assert report.percent == 33
    assert report.message == 'Now working on Task 2/3, "Write"'


def test_several_in_progress_join():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("A", "in_progress", "1"),
        _item("B", "in_progress", "2"),
    ], merge=False))
    assert report.message == 'Now working on Task 1/2, "A" and Task 2/2, "B"'


def test_merge_matches_an_existing_row_by_text_when_the_id_differs():
    """Grok's plan rows are keyed by their text. A later todo merge addresses
    them as "2" and "3". That patch updates those rows; it does not append
    a second copy and leave the old one in progress."""
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("Review", "completed", "Review"),
        _item("Check", "in_progress", "Check"),
        _item("Confirm", "pending", "Confirm"),
    ], merge=False))
    report = progress.apply(TodoUpdate(items=[
        _item("Check", "completed", "2"),
        _item("Confirm", "in_progress", "3"),
    ], merge=True))
    assert [(item.text, item.status) for item in progress.items] == [
        ("Review", "completed"),
        ("Check", "completed"),
        ("Confirm", "in_progress"),
    ]
    assert report is not None
    assert report.percent == 67
    assert report.message == 'Now working on Task 3/3, "Confirm"'


def test_all_complete():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "completed", "2"),
    ], merge=False))
    assert report.percent == 100
    assert report.message == "2 of 2 done"


def test_canceled_spelling_is_cancelled():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "canceled", "2"),
    ], merge=False))
    assert progress.items[1].status == "cancelled"
    assert report.percent == 100
    assert report.message == "1 of 1 done"


def test_cancelled_excluded():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "cancelled", "2"),
    ], merge=False))
    assert report.percent == 100
    assert report.message == "1 of 1 done"
    assert [item.status for item in progress.items] == ["completed", "cancelled"]


def test_unknown_status_counts_as_pending():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "bogus", "1"),
    ], merge=False))
    assert progress.items[0].status == "pending"
    assert report.percent == 0
    assert report.message == "0 of 1 done"


def test_half_rounds_up():
    progress = TodoProgress()
    eighth = progress.apply(TodoUpdate(items=[
        _item("done", "completed", "d"),
        *[_item(f"p{i}", "pending", str(i)) for i in range(7)],
    ], merge=False))
    assert eighth.percent == 13
    half = progress.apply(TodoUpdate(items=[
        _item("done", "completed", "d"),
        _item("left", "pending", "p"),
    ], merge=False))
    assert half.percent == 50


def test_zero_countable_returns_none():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("gone", "cancelled", "1"),
    ], merge=False))
    assert report is None


def test_replace_drops_old_ids():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[_item("old", "pending", "old")], merge=False))
    progress.apply(TodoUpdate(items=[_item("new", "pending", "new")], merge=False))
    assert [item.id for item in progress.items] == ["new"]


def test_merge_patches_and_appends():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("A", "pending", "a"),
        _item("B", "pending", "b"),
    ], merge=False))
    progress.apply(TodoUpdate(items=[
        _item("A", "completed", "a"),
        _item("C", "pending", ""),
    ], merge=True))
    assert [(item.id, item.text, item.status) for item in progress.items] == [
        ("a", "A", "completed"),
        ("b", "B", "pending"),
        ("C", "C", "pending"),
    ]


def test_json_round_trip():
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("Write", "in_progress", "1", active="Writing"),
        _item("Later", "pending", "2"),
    ], merge=False))
    raw = progress.to_json()
    assert '"active":"Writing"' in raw or '"active": "Writing"' in raw
    assert "Later" in raw
    # active omitted on the row that has none
    loaded = TodoProgress.from_json(raw)
    assert loaded is not None
    assert [(i.id, i.text, i.status, i.active) for i in loaded.items] == [
        ("1", "Write", "in_progress", "Writing"),
        ("2", "Later", "pending", None),
    ]


def test_empty_json_loads():
    loaded = TodoProgress.from_json('{"items":[]}')
    assert loaded is not None
    assert loaded.report() is None


def test_garbage_json_is_none():
    assert TodoProgress.from_json("{") is None
    assert TodoProgress.from_json('{"items":[{}]}') is None
    assert TodoProgress.from_json("[]") is None


# --- payload, ACP, tracker -------------------------------------------------


from optio_agents.todos import (  # noqa: E402
    TODO_FILENAME,
    TODO_TOOL_NAMES,
    TodoTracker,
    extract_acp_todo,
    normalize_tool_name,
    todo_update_from_payload,
)


def test_payload_todos_replace():
    update = todo_update_from_payload({
        "todos": [{
            "content": "Write", "status": "in_progress", "activeForm": "Writing",
        }],
        "merge": False,
    })
    assert update is not None
    assert update.merge is False
    assert update.items[0].text == "Write"
    assert update.items[0].active == "Writing"
    assert update.items[0].status == "in_progress"


def test_payload_plan_array():
    update = todo_update_from_payload({
        "plan": [{"step": "Write", "status": "in_progress"}],
    })
    assert update is not None
    assert update.merge is False
    assert update.items[0].text == "Write"
    assert update.items[0].status == "in_progress"


def test_payload_merge_flag():
    update = todo_update_from_payload({
        "todos": [{"id": "1", "content": "A", "status": "pending"}],
        "merge": True,
    })
    assert update is not None and update.merge is True
    assert update.items[0].id == "1"


def test_payload_not_a_list():
    assert todo_update_from_payload({"todos": "nope"}) is None


def test_normalize_name():
    assert normalize_tool_name("TodoWrite") in TODO_TOOL_NAMES
    assert normalize_tool_name("todo_write") in TODO_TOOL_NAMES
    assert normalize_tool_name("write_todo") in TODO_TOOL_NAMES
    assert normalize_tool_name("update_plan") not in TODO_TOOL_NAMES


def _session_update(update):
    return {"jsonrpc": "2.0", "method": "session/update", "params": {"update": update}}


def test_acp_plan_meta_cancelled_is_not_done():
    """Grok's plan update cannot say cancelled. That row arrives as
    status completed plus _meta.cancelled, and the in-progress row stays
    in progress. Neither counts as done."""
    progress = TodoProgress()
    update = extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [
            {"content": "Review the custom UI layout", "priority": "medium", "status": "completed"},
            {"content": "Check how in-progress items render", "priority": "medium", "status": "in_progress"},
            {"content": "Confirm pending items show up correctly", "priority": "medium", "status": "pending"},
            {"content": "Verify completed items stay visible", "priority": "medium", "status": "pending"},
            {
                "content": "Drop a cancelled item to test that state",
                "priority": "medium",
                "status": "completed",
                "_meta": {"cancelled": True},
            },
        ],
    }))
    assert update is not None
    report = progress.apply(update)
    assert [item.status for item in progress.items] == [
        "completed", "in_progress", "pending", "pending", "cancelled",
    ]
    assert report.percent == 25
    assert report.message == 'Now working on Task 2/4, "Check how in-progress items render"'


def test_plan_can_complete_an_in_progress_row_when_no_todo_tool_said_so():
    """A plan-only agent (no todo_write) is allowed to finish the current row."""
    progress = TodoProgress()
    progress.apply(extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "in_progress"}],
    })))
    report = progress.apply(extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "completed"}],
    })))
    assert progress.items[0].status == "completed"
    assert report.percent == 100
    assert report.message == "1 of 1 done"


def test_turn_end_plan_keeps_the_todo_tool_in_progress_row():
    """Grok titles the todo_write call 'Updating plan', then a later plan
    snapshot marks that in-progress row completed. The tool's status stands:
    the row keeps spinning and is not counted done."""
    progress = TodoProgress()
    tool = extract_acp_todo(_session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": "call-1",
        "title": "Updating plan",
        "kind": "think",
        "status": "completed",
        "rawInput": {
            "variant": "TodoWrite",
            "merge": False,
            "todos": [
                {"id": "1", "content": "Review the custom UI layout", "status": "completed"},
                {"id": "2", "content": "Check how in-progress items render", "status": "in_progress"},
                {"id": "3", "content": "Confirm pending items show up correctly", "status": "pending"},
                {"id": "4", "content": "Verify completed items stay visible", "status": "pending"},
                {"id": "5", "content": "Drop a cancelled item to test that state", "status": "cancelled"},
            ],
        },
        "_meta": {"x.ai/tool": {"name": "todo_write", "kind": "plan"}},
    }))
    assert tool is not None
    progress.apply(tool)
    progress.apply(extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [
            {"content": "Review the custom UI layout", "priority": "medium", "status": "completed"},
            {"content": "Check how in-progress items render", "priority": "medium", "status": "in_progress"},
            {"content": "Confirm pending items show up correctly", "priority": "medium", "status": "pending"},
            {"content": "Verify completed items stay visible", "priority": "medium", "status": "pending"},
            {
                "content": "Drop a cancelled item to test that state",
                "priority": "medium",
                "status": "completed",
                "_meta": {"cancelled": True},
            },
        ],
    })))
    report = progress.apply(extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [
            {"content": "Review the custom UI layout", "priority": "medium", "status": "completed"},
            {"content": "Check how in-progress items render", "priority": "medium", "status": "completed"},
            {"content": "Confirm pending items show up correctly", "priority": "medium", "status": "pending"},
            {"content": "Verify completed items stay visible", "priority": "medium", "status": "pending"},
            {
                "content": "Drop a cancelled item to test that state",
                "priority": "medium",
                "status": "completed",
                "_meta": {"cancelled": True},
            },
        ],
    })))
    assert [item.status for item in progress.items] == [
        "completed", "in_progress", "pending", "pending", "cancelled",
    ]
    assert report.percent == 25
    assert report.message == 'Now working on Task 2/4, "Check how in-progress items render"'


def test_acp_plan_update():
    update = extract_acp_todo(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "pending", "priority": "high"}],
    }))
    assert update is not None and update.merge is False
    assert update.items[0].text == "Write"
    assert update.items[0].status == "pending"
    assert not hasattr(update.items[0], "priority") or True
    assert "priority" not in update.items[0].__dict__


def test_acp_tool_call_when_raw_input_object():
    update = extract_acp_todo(_session_update({
        "sessionUpdate": "tool_call",
        "title": "todo_write",
        "rawInput": {
            "todos": [{"id": "1", "content": "A", "status": "pending"}],
            "merge": True,
        },
    }))
    assert update is not None and update.merge is True
    assert update.items[0].id == "1"


def test_acp_tool_call_streaming_raw_input():
    assert extract_acp_todo(_session_update({
        "sessionUpdate": "tool_call",
        "title": "todo_write",
        "rawInput": '{"todos"',
    })) is None
    assert extract_acp_todo(_session_update({
        "sessionUpdate": "tool_call",
        "title": "TodoWrite",
    })) is None


def test_acp_unrelated_event():
    assert extract_acp_todo(_session_update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hi"},
    })) is None


class FakeCtx:
    def __init__(self):
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append((percent, message))


class FakeHost:
    def __init__(self, body=None):
        self.workdir = "/work"
        self.body = body
        self.written = None

    async def fetch_bytes_from_host(self, path):
        if self.body is None:
            raise FileNotFoundError(path)
        return self.body.encode()

    async def write_text(self, relpath, content):
        self.written = (relpath, content)


class FakeConversation:
    def __init__(self):
        self.handler = None

    def on_event(self, handler):
        self.handler = handler
        def unsub():
            self.handler = None
        return unsub


def test_the_same_status_line_is_logged_once():
    """Grok sends the todo tool call, then the same call again as
    "Updating plan", then a plan snapshot. One sentence is one log line.
    A later change of the sentence is logged, including the done count
    once nothing is in progress."""
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    todos = [
        {"id": "1", "content": "Review", "status": "completed"},
        {"id": "2", "content": "Check", "status": "in_progress"},
    ]
    tracker.observe(_todo_write(todos))
    tracker.observe(_session_update({
        "sessionUpdate": "tool_call_update",
        "title": "Updating plan",
        "rawInput": {"variant": "TodoWrite", "merge": False, "todos": todos},
        "_meta": {"x.ai/tool": {"name": "todo_write"}},
    }))
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [
            {"content": "Review", "status": "completed"},
            {"content": "Check", "status": "in_progress"},
        ],
    }))
    assert ctx.calls == [(50, 'Now working on Task 2/2, "Check"')]
    tracker.observe(_todo_write([
        {"id": "1", "content": "Review", "status": "completed"},
        {"id": "2", "content": "Check", "status": "completed"},
    ]))
    assert ctx.calls == [
        (50, 'Now working on Task 2/2, "Check"'),
        (100, "2 of 2 done"),
    ]


def test_replay_announces_the_list_once():
    """A resumed transcript walks every old todo change. None of those
    sentences are logged. The line after replay is the list as it stands."""
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    conversation = FakeConversation()
    tracker.begin_replay(reconcile=False)
    tracker.arm(conversation)
    conversation.handler(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "First", "status": "in_progress"}],
    }))
    conversation.handler(_session_update({
        "sessionUpdate": "plan",
        "entries": [
            {"content": "First", "status": "completed"},
            {"content": "Second", "status": "in_progress"},
        ],
    }))
    assert ctx.calls == []
    tracker.end_replay()
    assert ctx.calls == [(50, 'Now working on Task 2/2, "Second"')]


def test_clearing_the_list_sets_indeterminate_progress():
    """An empty list removes the checklist. The bar must not keep the old
    percentage: percent None means active, with no percentage."""
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "in_progress"}],
    }))
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [],
    }))
    assert ctx.calls == [
        (0, 'Now working on Task 1/1, "Write"'),
        (None, None),
    ]
    assert tracker.progress.items == []


def test_tracker_observe_reports():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "in_progress"}],
    }))
    assert ctx.calls == [(0, 'Now working on Task 1/1, "Write"')]


def test_tracker_none_extract_does_not_report():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hi"},
    }))
    assert ctx.calls == []


def test_tracker_raising_extract_does_not_escape():
    ctx = FakeCtx()

    def boom(_event):
        raise RuntimeError("bad event")

    tracker = TodoTracker(boom, ctx)
    tracker.observe({"anything": True})
    assert ctx.calls == []


async def test_tracker_restore_reports_then_save_round_trip():
    body = '{"items":[{"id":"1","text":"Write","status":"in_progress","active":"Writing"}]}'
    host = FakeHost(body)
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(host) is True
    assert ctx.calls == [(0, 'Now working on Task 1/1, "Write"')]
    await tracker.save(host)
    assert host.written[0] == TODO_FILENAME
    other = TodoTracker(extract_acp_todo, FakeCtx())
    other_host = FakeHost(host.written[1])
    assert await other.restore(other_host) is True
    assert other.progress.items[0].text == "Write"
    assert other.progress.items[0].active == "Writing"


async def test_tracker_missing_file():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost(None)) is False
    assert ctx.calls == []


async def test_tracker_garbage_file():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost("{}")) is False
    assert ctx.calls == []


async def test_tracker_empty_file_loads_without_report():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost('{"items":[]}')) is True
    assert ctx.calls == []


async def test_save_is_noop_until_restore_or_observe():
    # A resume that dies during widget setup must not overwrite the file
    # the workdir restore just put back.
    host = FakeHost('{"items":[{"id":"1","text":"Write","status":"pending"}]}')
    tracker = TodoTracker(extract_acp_todo, FakeCtx())
    await tracker.save(host)
    assert host.written is None

    await tracker.restore(host)
    await tracker.save(host)
    assert host.written is not None
    assert "Write" in host.written[1]


async def test_save_after_observe_without_restore():
    host = FakeHost(None)
    tracker = TodoTracker(extract_acp_todo, FakeCtx())
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "pending"}],
    }))
    await tracker.save(host)
    assert host.written is not None
    assert host.written[0] == TODO_FILENAME
    assert "Write" in host.written[1]


def test_saved_list_keeps_its_rows_while_replay_restores_in_progress():
    """Resume loads a list whose in-progress row was saved as completed.
    The replayed todo tool corrects that row and does not rebuild the list.
    The live end-of-turn plan then cannot finish it again."""
    saved = json.dumps({"items": [
        {"id": "Review the custom UI layout", "text": "Review the custom UI layout", "status": "completed"},
        {"id": "Check how in-progress items render", "text": "Check how in-progress items render", "status": "completed"},
        {"id": "Confirm pending items show up correctly", "text": "Confirm pending items show up correctly", "status": "pending"},
        {"id": "Verify completed items stay visible", "text": "Verify completed items stay visible", "status": "pending"},
        {"id": "Drop a cancelled item to test that state", "text": "Drop a cancelled item to test that state", "status": "completed"},
        {"id": "only-in-the-file", "text": "Kept from the saved list", "status": "pending"},
    ]})
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    conversation = FakeConversation()

    async def run():
        tracker.begin_replay(reconcile=True)
        assert await tracker.restore(FakeHost(saved)) is True
        tracker.arm(conversation)
        conversation.handler(_session_update({
            "sessionUpdate": "tool_call",
            "title": "Updating plan",
            "rawInput": {
                "variant": "TodoWrite",
                "merge": False,
                "todos": [
                    {"id": "1", "content": "Review the custom UI layout", "status": "completed"},
                    {"id": "2", "content": "Check how in-progress items render", "status": "in_progress"},
                    {"id": "3", "content": "Confirm pending items show up correctly", "status": "pending"},
                    {"id": "4", "content": "Verify completed items stay visible", "status": "pending"},
                    {"id": "5", "content": "Drop a cancelled item to test that state", "status": "cancelled"},
                ],
            },
            "_meta": {"x.ai/tool": {"name": "todo_write"}},
        }))
        conversation.handler(_session_update({
            "sessionUpdate": "plan",
            "entries": [
                {"content": "Check how in-progress items render", "status": "completed"},
            ],
        }))
        # Historical steps stay out of the log. One line, for the list now.
        assert ctx.calls == []
        tracker.end_replay()
        assert [item.status for item in tracker.progress.items] == [
            "completed", "in_progress", "pending", "pending", "cancelled", "pending",
        ]
        assert tracker.progress.items[-1].text == "Kept from the saved list"
        assert ctx.calls == [(20, 'Now working on Task 2/5, "Check how in-progress items render"')]
        conversation.handler(_session_update({
            "sessionUpdate": "plan",
            "entries": [
                {"content": "Review the custom UI layout", "status": "completed"},
                {"content": "Check how in-progress items render", "status": "completed"},
                {"content": "Confirm pending items show up correctly", "status": "pending"},
                {"content": "Verify completed items stay visible", "status": "pending"},
                {
                    "content": "Drop a cancelled item to test that state",
                    "status": "completed",
                    "_meta": {"cancelled": True},
                },
            ],
        }))

    asyncio.run(run())
    # A live plan is a full snapshot, so the file-only row is gone. The plan
    # still cannot finish the row the todo tool left in progress: 1 of 4.
    assert [item.text for item in tracker.progress.items] == [
        "Review the custom UI layout",
        "Check how in-progress items render",
        "Confirm pending items show up correctly",
        "Verify completed items stay visible",
        "Drop a cancelled item to test that state",
    ]
    assert [item.status for item in tracker.progress.items] == [
        "completed", "in_progress", "pending", "pending", "cancelled",
    ]
    assert ctx.calls[-1] == (25, 'Now working on Task 2/4, "Check how in-progress items render"')


def test_saved_tool_status_blocks_a_plan_from_finishing_in_progress():
    """A resume whose history replay never arrives still remembers that the
    todo tool left the row in progress. The end-of-turn plan cannot finish it.
    The checklist payload does not carry that authority field."""
    progress = TodoProgress()
    progress.apply(TodoUpdate(items=[
        _item("Check how in-progress items render", "in_progress", "2"),
        _item("Later", "pending", "3"),
    ], merge=False))
    raw = progress.to_json()
    assert '"tool":"in_progress"' in raw or '"tool": "in_progress"' in raw
    loaded = TodoProgress.from_json(raw)
    assert loaded is not None
    report = loaded.apply(TodoUpdate(
        items=[
            _item("Check how in-progress items render", "completed", "2"),
            _item("Later", "pending", "3"),
        ],
        merge=False,
        source="plan",
    ))
    assert [item.status for item in loaded.items] == ["in_progress", "pending"]
    assert report is not None
    assert report.percent == 0
    assert report.message == 'Now working on Task 1/2, "Check how in-progress items render"'
    assert "tool" not in loaded.widget_items()[0]


def test_load_corrects_completed_when_saved_tool_says_in_progress():
    loaded = TodoProgress.from_json(json.dumps({"items": [{
        "id": "2",
        "text": "Check how in-progress items render",
        "status": "completed",
        "tool": "in_progress",
    }]}))
    assert loaded is not None
    assert loaded.items[0].status == "in_progress"
    report = loaded.report()
    assert report is not None
    assert report.percent == 0
    assert report.message == 'Now working on Task 1/1, "Check how in-progress items render"'


def test_tracker_arm_is_idempotent():
    ctx = FakeCtx()
    conversation = FakeConversation()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.arm(conversation)
    first = conversation.handler
    tracker.arm(conversation)
    assert conversation.handler is first
    tracker.disarm()
    assert conversation.handler is None


class PublishingCtx(FakeCtx):
    """Records the checklist the conversation view should show."""

    def __init__(self):
        super().__init__()
        self.published: list[list] = []

    async def set_widget_todos(self, items):
        self.published.append(items)


async def _wait_published(ctx: PublishingCtx, n: int = 1) -> None:
    """Yield until n checklist publishes have landed. A missing publish fails
    immediately; the loop is a hang ceiling, not a timer the assertion uses."""
    for _ in range(200):
        if len(ctx.published) >= n:
            return
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        if pending:
            await asyncio.wait(pending)
        else:
            await asyncio.sleep(0)
    raise AssertionError(f"expected {n} todo publishes, got {ctx.published!r}")


def _todo_write(todos, merge=False):
    return _session_update({
        "sessionUpdate": "tool_call",
        "title": "TodoWrite",
        "rawInput": {"todos": todos, "merge": merge},
    })


async def test_observe_publishes_the_todo_list():
    ctx = PublishingCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_todo_write([
        {"id": "1", "content": "Write", "status": "in_progress", "activeForm": "Writing"},
        {"id": "2", "content": "Ship", "status": "pending"},
    ]))
    await _wait_published(ctx)
    assert ctx.calls == [(0, 'Now working on Task 1/2, "Write"')]
    assert ctx.published[-1] == [
        {"id": "1", "text": "Write", "status": "in_progress", "active": "Writing"},
        {"id": "2", "text": "Ship", "status": "pending"},
    ]


async def test_observe_publishes_an_empty_list():
    ctx = PublishingCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [],
    }))
    await _wait_published(ctx)
    assert ctx.calls == []
    assert ctx.published[-1] == []


async def test_observe_publishes_a_cancelled_only_list():
    ctx = PublishingCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Drop", "status": "cancelled"}],
    }))
    await _wait_published(ctx)
    assert ctx.calls == []
    assert ctx.published[-1] == [
        {"id": "Drop", "text": "Drop", "status": "cancelled"},
    ]


async def test_restore_publishes_the_loaded_list():
    body = '{"items":[{"id":"1","text":"Write","status":"in_progress","active":"Writing"}]}'
    ctx = PublishingCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost(body)) is True
    assert ctx.calls == [(0, 'Now working on Task 1/1, "Write"')]
    assert ctx.published == [[
        {"id": "1", "text": "Write", "status": "in_progress", "active": "Writing"},
    ]]


async def test_restore_of_an_empty_list_publishes_it():
    ctx = PublishingCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost('{"items":[]}')) is True
    assert ctx.calls == []
    assert ctx.published == [[]]


class BoomCtx(FakeCtx):
    async def set_widget_todos(self, items):
        raise RuntimeError("mongo down")


async def test_a_failed_publish_keeps_the_progress_report():
    ctx = BoomCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_todo_write([
        {"id": "1", "content": "Write", "status": "in_progress", "activeForm": "Writing"},
    ]))
    pending = [
        task for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    ]
    assert pending, "publish was not scheduled"
    done = await asyncio.gather(*pending, return_exceptions=True)
    assert ctx.calls == [(0, 'Now working on Task 1/1, "Write"')]
    assert not any(isinstance(item, Exception) for item in done)


async def test_an_older_publish_does_not_cover_a_newer_list():
    class SlowCtx(FakeCtx):
        def __init__(self):
            super().__init__()
            self.published: list[list] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self._entered = 0

        async def set_widget_todos(self, items):
            self._entered += 1
            if self._entered == 1:
                self.started.set()
                await self.release.wait()
            self.published.append(items)

    ctx = SlowCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_todo_write([
        {"id": "1", "content": "Write", "status": "pending"},
    ]))
    await ctx.started.wait()
    tracker.observe(_todo_write([
        {"id": "1", "content": "Write", "status": "completed"},
        {"id": "2", "content": "Ship", "status": "pending"},
    ]))
    ctx.release.set()
    for _ in range(200):
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        if not pending and ctx.published:
            break
        if pending:
            await asyncio.wait(pending)
        else:
            await asyncio.sleep(0)
    else:
        raise AssertionError(f"publish did not finish: {ctx.published!r}")
    assert ctx.published[-1] == [
        {"id": "1", "text": "Write", "status": "completed"},
        {"id": "2", "text": "Ship", "status": "pending"},
    ]


async def test_a_failed_restore_publish_still_returns_the_list():
    body = '{"items":[{"id":"1","text":"Write","status":"in_progress","active":"Writing"}]}'
    ctx = BoomCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    assert await tracker.restore(FakeHost(body)) is True
    assert ctx.calls == [(0, 'Now working on Task 1/1, "Write"')]
    assert tracker.progress.items[0].text == "Write"
