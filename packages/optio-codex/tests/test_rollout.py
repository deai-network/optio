"""Unit tests for optio_codex.rollout — the PURE rollout-JSONL -> app-server
wire-event reconstruction that lets a fresh viewer replay the FULL codex
conversation history (the on-disk rollout is authoritative; the live buffer's
deque only holds a bounded tail).

Fixture: ``fixtures/rollout_sample.jsonl`` is a small realistic 2-turn codex
rollout (verified against a real codex-cli 0.142.5 rollout shape): session_meta,
injected developer/environment context (must be filtered), a real user prompt,
an assistant message, an exec_command tool, a reasoning item, a second assistant
message, then a second turn. The reader reduces it to the SAME item/completed +
turn/completed notifications the live listener/UI reducer consume.
"""

from __future__ import annotations

import pathlib

from optio_codex import rollout

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE = str(FIXTURES / "rollout_sample.jsonl")


def _methods(events):
    return [e["method"] for e in events]


def _items(events):
    return [e["params"]["item"] for e in events if e["method"] == "item/completed"]


def test_read_rollout_events_shapes_and_order():
    events = rollout.read_rollout_events(SAMPLE)
    # Turn A: userMessage, agentMessage, commandExecution, reasoning,
    # agentMessage -> 5 item/completed + 1 turn/completed.
    # Turn B: userMessage, agentMessage -> 2 item/completed + 1 turn/completed.
    assert _methods(events) == [
        "item/completed", "item/completed", "item/completed",
        "item/completed", "item/completed", "turn/completed",
        "item/completed", "item/completed", "turn/completed",
    ]
    items = _items(events)
    assert [it["type"] for it in items] == [
        "userMessage", "agentMessage", "commandExecution", "reasoning",
        "agentMessage",
        "userMessage", "agentMessage",
    ]


def test_injected_context_is_filtered():
    # The developer <permissions instructions> message and the role=user
    # <environment_context> message are codex-internal injections — never a real
    # user prompt, so they must NOT surface as userMessage items.
    events = rollout.read_rollout_events(SAMPLE)
    user_texts = [
        it["text"] for it in _items(events) if it["type"] == "userMessage"
    ]
    assert user_texts == ["hello", "second question"]
    for it in _items(events):
        assert "environment_context" not in str(it.get("text", ""))
        assert "permissions instructions" not in str(it.get("text", ""))


def test_message_and_tool_field_mapping():
    events = rollout.read_rollout_events(SAMPLE)
    items = _items(events)
    # assistant message -> agentMessage carrying the output_text.
    assert items[1] == {"type": "agentMessage", "id": items[1]["id"], "text": "hi there"}
    assert items[4]["type"] == "agentMessage" and items[4]["text"] == "done"
    # exec_command function_call -> commandExecution the UI toolRow() renders.
    cmd = items[2]
    assert cmd["type"] == "commandExecution"
    assert cmd["command"] == "ls -la"
    assert cmd["cwd"] == "/w"
    assert cmd["status"] == "completed"
    # reasoning -> reasoning item (rendered as a no-op by the reducer, emitted
    # for parity with the resume replay path).
    assert items[3]["type"] == "reasoning"


def test_turn_ids_and_thread_id():
    events = rollout.read_rollout_events(SAMPLE)
    # threadId comes from session_meta.session_id.
    for e in events:
        assert e["params"]["threadId"] == "thread-xyz"
    # every item/completed carries its real codex turn_id (the same id the live
    # wire uses — the dedup key the listener relies on).
    tids = [e["params"]["turnId"] for e in events if e["method"] == "item/completed"]
    assert tids == ["turn-aaa"] * 5 + ["turn-bbb"] * 2
    tc = [e for e in events if e["method"] == "turn/completed"]
    assert [e["params"]["turn"]["id"] for e in tc] == ["turn-aaa", "turn-bbb"]
    assert all(e["params"]["turn"]["status"] == "completed" for e in tc)


def test_read_rollout_events_malformed_is_soft():
    # A malformed / unreadable rollout must never raise into the caller (the SSE
    # attach path is fail-soft): unparseable lines are skipped, a missing file
    # yields an empty list.
    assert rollout.read_rollout_events(str(FIXTURES / "does-not-exist.jsonl")) == []


def test_read_rollout_events_skips_bad_lines(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        '{"type":"session_meta","payload":{"session_id":"t1"}}\n'
        "not json at all\n"
        '{"type":"event_msg","payload":{"type":"task_started","turn_id":"tz"}}\n'
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"hi"}],'
        '"internal_chat_message_metadata_passthrough":{"turn_id":"tz"}}}\n'
    )
    events = rollout.read_rollout_events(str(p))
    assert _methods(events) == ["item/completed", "turn/completed"]
    assert _items(events)[0]["text"] == "hi"


def test_resolve_latest_rollout_picks_newest_across_date_tree(tmp_path):
    home = tmp_path / ".codex"
    d1 = home / "sessions" / "2026" / "07" / "02"
    d2 = home / "sessions" / "2026" / "07" / "06"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    old = d1 / "rollout-2026-07-02T00-00-00-aaaa.jsonl"
    new = d2 / "rollout-2026-07-06T00-00-00-bbbb.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    assert rollout.resolve_latest_rollout(str(home)) == str(new)


def test_resolve_latest_rollout_none_when_empty(tmp_path):
    home = tmp_path / ".codex"
    (home / "sessions").mkdir(parents=True)
    assert rollout.resolve_latest_rollout(str(home)) is None
    # Also None when the sessions tree does not exist at all.
    assert rollout.resolve_latest_rollout(str(tmp_path / "nope")) is None
