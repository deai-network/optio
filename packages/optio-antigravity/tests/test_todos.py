from optio_antigravity.todos import extract_antigravity_todo


def test_planner_todo_call():
    update = extract_antigravity_todo({
        "type": "PLANNER_RESPONSE",
        "tool_calls": [{
            "name": "todo_write",
            "args": {"todos": [{"content": "Write", "status": "pending"}]},
        }],
    })
    assert update is not None
    assert update.items[0].text == "Write"
    assert update.items[0].status == "pending"


def test_args_json_string():
    update = extract_antigravity_todo({
        "type": "PLANNER_RESPONSE",
        "tool_calls": [{
            "name": "todo_write",
            "args": '{"todos":[{"content":"Write","status":"completed"}]}',
        }],
    })
    assert update is not None
    assert update.items[0].status == "completed"


def test_other_tool_is_ignored():
    assert extract_antigravity_todo({
        "type": "PLANNER_RESPONSE",
        "tool_calls": [{"name": "list_dir", "args": {}}],
    }) is None


def test_user_input_is_ignored():
    assert extract_antigravity_todo({"type": "USER_INPUT"}) is None


def test_second_call_wins_when_first_is_not_a_todo():
    update = extract_antigravity_todo({
        "type": "PLANNER_RESPONSE",
        "tool_calls": [
            {"name": "list_dir", "args": {}},
            {"name": "write_todo", "args": {"todos": [{
                "content": "Write", "status": "in_progress",
            }]}},
        ],
    })
    assert update is not None
    assert update.items[0].status == "in_progress"
