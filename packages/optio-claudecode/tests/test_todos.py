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
