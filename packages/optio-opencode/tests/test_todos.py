from optio_opencode.todos import extract_opencode_todo


def test_tool_part_becomes_an_update():
    update = extract_opencode_todo({
        "id": "p1",
        "type": "message.part.updated",
        "properties": {"part": {
            "type": "tool",
            "tool": "todowrite",
            "state": {"input": {"todos": [{
                "content": "Write", "status": "pending",
            }]}},
        }},
    })
    assert update is not None
    assert update.items[0].text == "Write"
    assert update.items[0].status == "pending"


def test_empty_input_is_ignored():
    assert extract_opencode_todo({
        "type": "message.part.updated",
        "properties": {"part": {
            "type": "tool", "tool": "todowrite", "state": {"input": {}},
        }},
    }) is None
    assert extract_opencode_todo({
        "type": "message.part.updated",
        "properties": {"part": {"type": "tool", "tool": "todowrite", "state": {}}},
    }) is None


def test_text_part_is_ignored():
    assert extract_opencode_todo({
        "type": "message.part.updated",
        "properties": {"part": {"type": "text", "text": "hi"}},
    }) is None
