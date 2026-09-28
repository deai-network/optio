"""Progress rule for a conversation's todo list."""

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
    assert report.message == "Writing"


def test_several_in_progress_join():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("A", "in_progress", "1"),
        _item("B", "in_progress", "2"),
    ], merge=False))
    assert report.message == "A; B"


def test_all_complete():
    progress = TodoProgress()
    report = progress.apply(TodoUpdate(items=[
        _item("a", "completed", "1"),
        _item("b", "completed", "2"),
    ], merge=False))
    assert report.percent == 100
    assert report.message == "2 of 2 done"


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


def test_tracker_observe_reports():
    ctx = FakeCtx()
    tracker = TodoTracker(extract_acp_todo, ctx)
    tracker.observe(_session_update({
        "sessionUpdate": "plan",
        "entries": [{"content": "Write", "status": "in_progress"}],
    }))
    assert ctx.calls == [(0, "Write")]


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
    assert ctx.calls == [(0, "Writing")]
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
