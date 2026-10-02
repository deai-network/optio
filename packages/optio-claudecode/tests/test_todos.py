from optio_claudecode.todos import extract_claude_todo


def test_todowrite_becomes_an_update():
    update = extract_claude_todo({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "name": "TodoWrite",
            "input": {"todos": [{
                "content": "Write",
                "status": "in_progress",
                "activeForm": "Writing",
            }]},
        }]},
    })
    assert update is not None and update.merge is False
    assert update.items[0].text == "Write"
    assert update.items[0].active == "Writing"
    assert update.items[0].status == "in_progress"


def test_text_block_is_ignored():
    assert extract_claude_todo({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi"}]},
    }) is None


def test_input_json_delta_is_ignored():
    assert extract_claude_todo({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": "{"},
        },
    }) is None


def test_malformed_input_is_ignored():
    assert extract_claude_todo({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite", "input": ["nope"],
        }]},
    }) is None


def test_other_tool_is_ignored():
    assert extract_claude_todo({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "Bash", "input": {"command": "ls"},
        }]},
    }) is None


def _use(tool_id, name, payload):
    return {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": tool_id, "name": name, "input": payload,
        }]},
    }


def _result(tool_id, content, *, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


def _rows(progress):
    return [(item.id, item.text, item.status, item.active) for item in progress.items]


def _apply(extractor, progress, event):
    update = extractor(event)
    if update is None:
        return None
    return progress.apply(update)


def test_task_create_adds_the_row_from_the_result():
    """The id is not in the tool input. It arrives on the next tool result,
    and the row stays pending until a later update."""
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    assert _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Get the source",
        "description": "Ask where the data lives.",
        "activeForm": "Waiting for the source",
    })) is None
    report = _apply(extractor, progress, _result(
        "c1", "Task #1 created successfully: Get the source",
    ))
    assert _rows(progress) == [("1", "Get the source", "pending", "Waiting for the source")]
    assert report is not None and report.percent == 0
    assert report.message is None


def test_a_task_description_survives_an_update_and_a_list():
    """The id comes back on the result. The description stays on the row,
    including when a later update sends only that field and a TaskList
    line does not mention it."""
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c10", "TaskCreate", {
        "subject": "Get the source",
        "description": "Ask where the data lives.",
        "activeForm": "Waiting for the source",
    }))
    _apply(extractor, progress, _result(
        "c10", "Task #10 created successfully: Get the source",
    ))
    assert progress.items[0].description == "Ask where the data lives."
    _apply(extractor, progress, _use("u10", "TaskUpdate", {
        "taskId": "10",
        "description": "Before any **exploration**, ask where it lives.",
    }))
    _apply(extractor, progress, _result("u10", "Updated task #10 description"))
    assert progress.items[0].description == "Before any **exploration**, ask where it lives."
    assert progress.items[0].status == "pending"
    assert progress.items[0].active == "Waiting for the source"
    _apply(extractor, progress, _use("l10", "TaskList", {}))
    _apply(extractor, progress, _result(
        "l10", "#10 [pending] Get the source (demo-agent)",
    ))
    assert _rows(progress) == [
        ("10", "Get the source", "pending", "Waiting for the source"),
    ]
    assert progress.items[0].description == "Before any **exploration**, ask where it lives."
    assert progress.widget_items()[0]["description"] == progress.items[0].description
    loaded = TodoProgress.from_json(progress.to_json())
    assert loaded is not None
    assert loaded.items[0].description == progress.items[0].description


def test_task_create_result_without_the_tool_use_still_adds_the_row():
    from optio_claudecode.todos import ClaudeTodoExtractor

    update = ClaudeTodoExtractor()(_result(
        "c9", "Task #3 created successfully: Minimal",
    ))
    assert update is not None and update.source == "task" and update.merge is True
    assert [(item.id, item.text, item.status, item.active) for item in update.items] == [
        ("3", "Minimal", "pending", None),
    ]


def test_task_result_text_blocks_are_read():
    from optio_claudecode.todos import ClaudeTodoExtractor

    update = ClaudeTodoExtractor()(_result("c2", [
        {"type": "text", "text": "Task #2 created successfully: Second"},
    ]))
    assert update is not None
    assert update.items[0].id == "2"
    assert update.items[0].text == "Second"


def test_status_update_and_rename_keep_fields_the_call_omitted():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Get the source",
        "description": "Ask.",
        "activeForm": "Waiting for the source",
    }))
    _apply(extractor, progress, _result(
        "c1", "Task #1 created successfully: Get the source",
    ))
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "1", "status": "in_progress",
    }))
    report = _apply(extractor, progress, _result("u1", "Updated task #1 status"))
    assert _rows(progress) == [("1", "Get the source", "in_progress", "Waiting for the source")]
    assert report is not None
    assert report.message == 'Now working on Task 1/1, "Get the source"'

    _apply(extractor, progress, _use("u2", "TaskUpdate", {
        "taskId": "1", "subject": "Get the source (renamed)",
    }))
    _apply(extractor, progress, _result("u2", "Updated task #1 subject"))
    assert _rows(progress) == [
        ("1", "Get the source (renamed)", "in_progress", "Waiting for the source"),
    ]

    _apply(extractor, progress, _use("u3", "TaskUpdate", {
        "taskId": "1", "activeForm": "Still asking",
    }))
    _apply(extractor, progress, _result("u3", "Updated task #1 activeForm"))
    assert _rows(progress) == [
        ("1", "Get the source (renamed)", "in_progress", "Still asking"),
    ]


def test_metadata_and_block_updates_do_not_change_the_row():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "DEMO B: minimal task", "description": "x",
    }))
    _apply(extractor, progress, _result(
        "c1", "Task #8 created successfully: DEMO B: minimal task",
    ))
    before = _rows(progress)
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "8", "addBlockedBy": ["7"],
    }))
    assert _apply(extractor, progress, _result("u1", "Updated task #8 blockedBy")) is None
    assert _rows(progress) == before


def test_task_update_without_the_tool_use_is_ignored():
    from optio_claudecode.todos import ClaudeTodoExtractor

    assert ClaudeTodoExtractor()(_result("u1", "Updated task #1 status")) is None


def test_a_rejected_status_does_not_change_the_row():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Ship it", "description": "x",
    }))
    _apply(extractor, progress, _result("c1", "Task #6 created successfully: Ship it"))
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "6", "status": "failed",
    }))
    assert _apply(extractor, progress, _result(
        "u1",
        '<tool_use_error>InputValidationError: expected "pending"|"in_progress"|"completed"',
        is_error=True,
    )) is None
    assert _rows(progress) == [("6", "Ship it", "pending", None)]


def test_deleted_status_removes_the_row():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "DEMO C: blocked via addBlocks", "description": "x",
    }))
    _apply(extractor, progress, _result(
        "c1", "Task #9 created successfully: DEMO C: blocked via addBlocks",
    ))
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "9", "status": "deleted",
    }))
    report = _apply(extractor, progress, _result("u1", "Updated task #9 deleted"))
    assert progress.items == []
    assert report is None


def test_task_get_and_a_missing_task_do_not_change_the_list():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "DEMO A: full-field task", "description": "x",
    }))
    _apply(extractor, progress, _result(
        "c1", "Task #7 created successfully: DEMO A: full-field task",
    ))
    before = _rows(progress)
    _apply(extractor, progress, _use("g1", "TaskGet", {"taskId": "7"}))
    assert _apply(extractor, progress, _result(
        "g1", "Task #7: DEMO A: full-field task\nStatus: pending\nDescription: x",
    )) is None
    _apply(extractor, progress, _use("g2", "TaskGet", {"taskId": "7"}))
    assert _apply(extractor, progress, _result("g2", "Task not found")) is None
    assert _rows(progress) == before


def test_task_list_replaces_membership_without_losing_the_subject():
    """A list line appends the owner and the blockers after the subject.
    A subject we already stored, including one that ends in (failed), stays."""
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Get the source",
        "description": "Ask.",
        "activeForm": "Waiting for the source",
    }))
    _apply(extractor, progress, _result(
        "c1", "Task #1 created successfully: Get the source",
    ))
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "1", "subject": "Get the source (renamed)", "status": "in_progress",
    }))
    _apply(extractor, progress, _result("u1", "Updated task #1 subject, status"))
    _apply(extractor, progress, _use("c8", "TaskCreate", {
        "subject": "DEMO B: minimal task", "description": "x",
    }))
    _apply(extractor, progress, _result(
        "c8", "Task #8 created successfully: DEMO B: minimal task",
    ))
    _apply(extractor, progress, _use("u8", "TaskUpdate", {
        "taskId": "8",
        "subject": "DEMO B: minimal task (failed)",
        "status": "completed",
    }))
    _apply(extractor, progress, _result("u8", "Updated task #8 subject, status"))
    _apply(extractor, progress, _use("l1", "TaskList", {}))
    report = _apply(extractor, progress, _result("l1", "\n".join([
        "#1 [in_progress] Get the source (renamed) (demo-agent)",
        "#8 [completed] DEMO B: minimal task (failed) [blocked by #1]",
        "#4 [pending] Phase 2: explore the source",
    ])))
    assert _rows(progress) == [
        ("1", "Get the source (renamed)", "in_progress", "Waiting for the source"),
        ("8", "DEMO B: minimal task (failed)", "completed", None),
        ("4", "Phase 2: explore the source", "pending", None),
    ]
    assert report is not None and report.percent == 33
    assert report.message == 'Now working on Task 1/3, "Get the source (renamed)"'


def test_no_tasks_found_clears_an_earlier_list():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "name": "TodoWrite",
            "input": {"todos": [{"content": "Old step", "status": "in_progress"}]},
        }]},
    })
    assert progress.items
    _apply(extractor, progress, _use("l1", "TaskList", {}))
    report = _apply(extractor, progress, _result("l1", "No tasks found"))
    assert progress.items == []
    assert report is None


def test_a_task_list_that_does_not_parse_does_not_wipe():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Keep me", "description": "x",
    }))
    _apply(extractor, progress, _result("c1", "Task #1 created successfully: Keep me"))
    _apply(extractor, progress, _use("l1", "TaskList", {}))
    assert _apply(extractor, progress, _result("l1", "#1 [pending] Keep me\nnot a task")) is None
    assert _rows(progress) == [("1", "Keep me", "pending", None)]


def test_an_unrelated_tool_result_does_not_clear_the_list():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("c1", "TaskCreate", {
        "subject": "Keep me", "description": "x",
    }))
    _apply(extractor, progress, _result("c1", "Task #1 created successfully: Keep me"))
    assert _apply(extractor, progress, _result("bash", "No tasks found")) is None
    assert _rows(progress) == [("1", "Keep me", "pending", None)]


def test_results_in_one_event_apply_in_order():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, _use("l1", "TaskList", {}))
    _apply(extractor, progress, _use("u1", "TaskUpdate", {
        "taskId": "1", "status": "in_progress",
    }))
    event = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "l1", "content": "#1 [pending] Get the source"},
        {"type": "tool_result", "tool_use_id": "u1", "content": "Updated task #1 status"},
    ]}}
    _apply(extractor, progress, event)
    assert _rows(progress) == [("1", "Get the source", "in_progress", None)]


def test_the_stateful_extractor_still_replaces_on_todowrite():
    from optio_agents.todos import TodoProgress
    from optio_claudecode.todos import ClaudeTodoExtractor

    extractor = ClaudeTodoExtractor()
    progress = TodoProgress()
    _apply(extractor, progress, {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite",
            "input": {"todos": [
                {"content": "Old", "status": "pending"},
                {"content": "Also old", "status": "pending"},
            ]},
        }]},
    })
    _apply(extractor, progress, {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite",
            "input": {"todos": [{"content": "Only", "status": "in_progress", "activeForm": "Doing"}]},
        }]},
    })
    assert _rows(progress) == [("Only", "Only", "in_progress", "Doing")]


def test_transcript_without_task_tools_returns_none():
    import json
    from optio_claudecode.todos import ClaudeTodoExtractor, todo_progress_from_transcript

    text = json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite",
            "input": {"todos": [{"content": "Old", "status": "pending"}]},
        }]},
    })
    assert todo_progress_from_transcript(text, ClaudeTodoExtractor()) is None


def test_transcript_rebuilds_past_an_earlier_todowrite():
    import json
    from optio_claudecode.todos import ClaudeTodoExtractor, todo_progress_from_transcript

    events = [
        {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite",
            "input": {"todos": [{"content": "Old step", "status": "in_progress"}]},
        }]}},
        _use("l0", "TaskList", {}),
        _result("l0", "No tasks found"),
        _use("c1", "TaskCreate", {
            "subject": "Only task", "description": "x", "activeForm": "Working",
        }),
        _result("c1", "Task #1 created successfully: Only task"),
        _use("l1", "TaskList", {}),
        _result("l1", "#1 [pending] Only task"),
    ]
    text = "not json\n" + "\n".join(json.dumps(event) for event in events)
    progress = todo_progress_from_transcript(text, ClaudeTodoExtractor())
    assert progress is not None
    assert _rows(progress) == [("1", "Only task", "pending", "Working")]


def test_an_empty_task_list_in_the_transcript_is_not_a_missing_list():
    import json
    from optio_claudecode.todos import ClaudeTodoExtractor, todo_progress_from_transcript

    events = [
        {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "name": "TodoWrite",
            "input": {"todos": [{"content": "Old step", "status": "pending"}]},
        }]}},
        _use("l0", "TaskList", {}),
        _result("l0", "No tasks found"),
    ]
    text = "\n".join(json.dumps(event) for event in events)
    progress = todo_progress_from_transcript(text, ClaudeTodoExtractor())
    assert progress is not None and progress.items == []


def test_an_unanswered_task_create_stays_ready_for_the_live_result():
    import json
    from optio_claudecode.todos import ClaudeTodoExtractor, todo_progress_from_transcript

    extractor = ClaudeTodoExtractor()
    text = json.dumps(_use("c4", "TaskCreate", {
        "subject": "Later", "description": "x", "activeForm": "Doing it",
    }))
    assert todo_progress_from_transcript(text, extractor) is None
    update = extractor(_result("c4", "Task #4 created successfully: Later"))
    assert update is not None
    assert [(item.id, item.text, item.active) for item in update.items] == [
        ("4", "Later", "Doing it"),
    ]


class _CommandResult:
    def __init__(self, stdout):
        self.stdout = stdout


class _Host:
    def __init__(self, found="", body=""):
        self.workdir = "/work"
        self.found = found
        self.body = body
        self.commands = []

    async def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command.startswith("find "):
            return _CommandResult(self.found)
        if command.startswith("cat "):
            return _CommandResult(self.body)
        raise AssertionError(command)


async def test_resume_reads_the_whole_transcript():
    import json
    from optio_claudecode.session import task_todos_from_transcript
    from optio_claudecode.todos import ClaudeTodoExtractor

    body = "\n".join(json.dumps(event) for event in (
        _use("l0", "TaskList", {}),
        _result("l0", "#1 [in_progress] Get the source"),
    ))
    host = _Host(found="10\t/work/home/.claude/projects/s/session.jsonl", body=body)
    progress = await task_todos_from_transcript(host, ClaudeTodoExtractor())
    assert progress is not None
    assert _rows(progress) == [("1", "Get the source", "in_progress", None)]
    assert all("tail -c" not in command for command in host.commands)
    assert any(command.startswith("cat ") for command in host.commands)


async def test_resume_without_a_transcript_does_not_read_a_file():
    from optio_claudecode.session import task_todos_from_transcript
    from optio_claudecode.todos import ClaudeTodoExtractor

    host = _Host(found="")
    assert await task_todos_from_transcript(host, ClaudeTodoExtractor()) is None
    assert len(host.commands) == 1
