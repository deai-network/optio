"""Unit tests for optio_codex.rollout — the PURE rollout-JSONL -> app-server
wire-event reconstruction (parse_rollout_events) plus the host-routed newest-
rollout discovery (resolve_latest_rollout). The on-disk rollout is the
authoritative full history; the live buffer's deque only holds a bounded tail.

Fixture: ``fixtures/rollout_sample.jsonl`` is a small realistic 2-turn codex
rollout (verified against a real codex-cli 0.142.5 rollout shape): session_meta,
injected developer/environment context (must be filtered), a real user prompt,
an assistant message, an exec_command tool, a reasoning item, a second assistant
message, then a second turn. parse_rollout_events reduces it to the SAME
item/completed + turn/completed notifications the live listener/UI reducer
consume.

Discovery + reads go through the Host abstraction (host.glob /
fetch_bytes_from_host), never a bare open() — so the same code path serves a
remote SSH worker. The fake host below mirrors LocalHost exactly (glob.glob +
read_bytes); LocalHost.glob itself is covered in optio-host's own tests.
"""

from __future__ import annotations

import glob as _glob
import pathlib

from optio_codex import rollout

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "rollout_sample.jsonl"


class _LocalGlobHost:
    """Minimal Host stand-in: routes glob + read through the same primitives
    LocalHost uses, so resolve_latest_rollout is exercised end-to-end without a
    full Host construction."""

    async def glob(self, pattern: str) -> list[str]:
        return sorted(_glob.glob(pattern))

    async def fetch_bytes_from_host(self, path: str, *, progress_cb=None) -> bytes:
        return pathlib.Path(path).read_bytes()


def _parse():
    return rollout.parse_rollout_events(SAMPLE.read_text())


def _methods(events):
    return [e["method"] for e in events]


def _items(events):
    return [e["params"]["item"] for e in events if e["method"] == "item/completed"]


def test_parse_rollout_events_shapes_and_order():
    events = _parse()
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
    events = _parse()
    user_texts = [
        it["text"] for it in _items(events) if it["type"] == "userMessage"
    ]
    assert user_texts == ["hello", "second question"]
    for it in _items(events):
        assert "environment_context" not in str(it.get("text", ""))
        assert "permissions instructions" not in str(it.get("text", ""))


def test_message_and_tool_field_mapping():
    events = _parse()
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
    events = _parse()
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


def test_parse_rollout_events_is_pure_and_soft():
    # PURE, no I/O: empty text -> no events; malformed lines are skipped, never
    # raising into the fail-soft SSE attach path.
    assert rollout.parse_rollout_events("") == []
    assert rollout.parse_rollout_events("not json\n\n{bad}\n") == []


def test_parse_rollout_events_skips_bad_lines():
    events = rollout.parse_rollout_events(
        '{"type":"session_meta","payload":{"session_id":"t1"}}\n'
        "not json at all\n"
        '{"type":"event_msg","payload":{"type":"task_started","turn_id":"tz"}}\n'
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"hi"}],'
        '"internal_chat_message_metadata_passthrough":{"turn_id":"tz"}}}\n'
    )
    assert _methods(events) == ["item/completed", "turn/completed"]
    assert _items(events)[0]["text"] == "hi"


async def test_resolve_latest_rollout_picks_newest_across_date_tree(tmp_path):
    home = tmp_path / ".codex"
    d1 = home / "sessions" / "2026" / "07" / "02"
    d2 = home / "sessions" / "2026" / "07" / "06"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    old = d1 / "rollout-2026-07-02T00-00-00-aaaa.jsonl"
    new = d2 / "rollout-2026-07-06T00-00-00-bbbb.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    got = await rollout.resolve_latest_rollout(_LocalGlobHost(), str(home))
    assert got == str(new)


async def test_resolve_latest_rollout_none_when_empty(tmp_path):
    home = tmp_path / ".codex"
    (home / "sessions").mkdir(parents=True)
    assert await rollout.resolve_latest_rollout(_LocalGlobHost(), str(home)) is None
    # Also None when the sessions tree does not exist at all.
    assert await rollout.resolve_latest_rollout(_LocalGlobHost(), str(tmp_path / "nope")) is None


async def test_resolve_latest_rollout_soft_on_host_error():
    # A host.glob that raises must yield None (discovery never raises into attach).
    class _BoomHost:
        async def glob(self, pattern):
            raise RuntimeError("ssh down")

    assert await rollout.resolve_latest_rollout(_BoomHost(), "/x/.codex") is None
