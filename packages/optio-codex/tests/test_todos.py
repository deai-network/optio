from optio_codex.todos import extract_codex_todo


def test_item_completed_update_plan():
    update = extract_codex_todo({
        "method": "item/completed",
        "params": {"item": {
            "type": "update_plan",
            "arguments": {"plan": [{"step": "Write", "status": "in_progress"}]},
        }},
    })
    assert update is not None and update.merge is False
    assert update.items[0].text == "Write"
    assert update.items[0].status == "in_progress"


def test_arguments_json_string():
    update = extract_codex_todo({
        "method": "item/completed",
        "params": {"item": {
            "type": "update_plan",
            "arguments": '{"plan":[{"step":"Write","status":"pending"}]}',
        }},
    })
    assert update is not None
    assert update.items[0].text == "Write"
    assert update.items[0].status == "pending"


def test_function_call_shape():
    update = extract_codex_todo({
        "type": "function_call",
        "name": "update_plan",
        "arguments": {"plan": [{"step": "Write", "status": "completed"}]},
    })
    assert update is not None
    assert update.items[0].status == "completed"


def test_command_execution_is_ignored():
    assert extract_codex_todo({
        "method": "item/completed",
        "params": {"item": {"type": "commandExecution", "command": "ls"}},
    }) is None


def test_broken_arguments_are_ignored():
    assert extract_codex_todo({
        "method": "item/completed",
        "params": {"item": {"type": "update_plan", "arguments": "{"}},
    }) is None
