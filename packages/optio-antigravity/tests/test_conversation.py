"""AntigravityConversation unit tests against the fake ``agy`` binary.

Unlike grok (ACP/JSON-RPC over a live stdio subprocess), Antigravity has no
live transport: the conversation is **synthetic**, driven by repeated one-shot
``agy -p`` invocations under a PTY, with events read from the per-conversation
transcript at ``<home>/.gemini/antigravity-cli/brain/<uuid>/.system_generated/
logs/transcript.jsonl`` (the real ``agy`` layout, captured 2026-07-06). These
tests exercise that driver over ``fake_agy.py`` plus the committed real-agy
fixture.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from optio_antigravity.conversation import (
    AntigravityConversation,
    ConversationClosed,
    unwrap_user_request,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "real-transcript.jsonl"


async def test_send_runs_print_turn_and_emits_final_message(fake_agy_conv):
    conv = fake_agy_conv
    events = []
    conv.on_event(lambda e: events.append(e))
    msgs = []
    conv.on_message(lambda m: msgs.append(m))
    await conv.send("say PONG")
    # One coalesced final answer per completed turn (turn = one -p invocation).
    assert any(m.text.strip() == "PONG" for m in msgs)
    # Transcript events fanned out live to on_event (raw dicts, real schema).
    assert any(e.get("type") == "PLANNER_RESPONSE" for e in events)
    assert conv.closed is False


async def test_second_turn_uses_conversation_id(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    cid = conv.conversation_id
    assert cid  # discovered from last_conversations.json after turn 1
    await conv.send("second")
    # Turn 2 resumes the discovered conversation.
    assert conv.last_argv_contains(f"--conversation {cid}")


async def test_first_turn_has_no_conversation_flag(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    # Turn 1 passes NO --conversation (a fresh workdir → agy mints one); the
    # uuid is then discovered from last_conversations.json.
    assert not conv.last_argv_contains("--conversation")
    assert conv.conversation_id  # discovered after the turn


async def test_turns_run_skip_permissions(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    # -p turns run non-interactive, so permissions are skipped (design §7).
    assert conv.last_argv_contains("--dangerously-skip-permissions")


async def test_interrupt_kills_inflight_turn(fake_agy_slow):
    conv = fake_agy_slow
    task = asyncio.ensure_future(conv.send("slow"))
    await conv.interrupt()
    with pytest.raises(Exception):
        await task
    # The conversation itself survives an interrupt (only the turn dies).
    assert conv.closed is False


async def test_send_after_close_raises(fake_agy_conv):
    conv = fake_agy_conv
    await conv.close()
    assert conv.closed is True
    with pytest.raises(ConversationClosed):
        await conv.send("nope")


async def test_permission_request_is_noop(fake_agy_conv):
    conv = fake_agy_conv
    # on_permission_request is a no-op seam (turns are skip-permissions); it
    # must still return an unsubscribe callable and never fire.
    unsub = conv.on_permission_request(lambda req: None)
    assert callable(unsub)
    unsub()


async def test_driver_parses_real_agy_fixture(tmp_path):
    """The driver extracts the right facts from a REAL captured ``agy``
    transcript (multi-turn, committed at tests/fixtures/real-transcript.jsonl):
    raw lines fan out to on_event unmodified, the final answer coalesces to the
    last non-empty PLANNER_RESPONSE content, tool_calls and reasoning survive,
    and USER_INPUT content unwraps to the bare request."""
    home = tmp_path / "home"
    conv = AntigravityConversation(
        host=None, agy_path="agy", cwd=str(tmp_path / "work"), home=str(home),
    )
    # Point the driver at the real fixture as this conversation's transcript.
    conv._conversation_id = "conv-uuid-fixture"
    dest = pathlib.Path(conv._transcript_path())
    dest.parent.mkdir(parents=True)
    dest.write_bytes(_FIXTURE.read_bytes())

    events: list[dict] = []
    conv.on_event(events.append)
    msgs = []
    conv.on_message(msgs.append)
    await conv._consume_turn(0)

    # Raw lines, unmodified (the real schema keys are present).
    assert len(events) == 12
    assert events[0]["type"] == "USER_INPUT"
    assert events[0]["source"] == "USER_EXPLICIT"

    # Final answer = last non-empty PLANNER_RESPONSE content.
    assert len(msgs) == 1
    assert msgs[0].text.strip() == "PONG"

    # User requests unwrap out of the <USER_REQUEST> envelope.
    requests = [
        unwrap_user_request(e["content"])
        for e in events if e.get("type") == "USER_INPUT"
    ]
    assert requests == [
        "Reply with exactly the word PONG, then use a tool to list files in "
        "the current directory.",
        "What single word did I ask you to reply with in my previous message?",
    ]

    # Tool calls survive on the raw PLANNER_RESPONSE lines.
    tool_names = [
        tc["name"]
        for e in events if e.get("type") == "PLANNER_RESPONSE"
        for tc in (e.get("tool_calls") or [])
    ]
    assert "list_dir" in tool_names
    assert "list_permissions" in tool_names

    # Reasoning (thinking) is present on at least one assistant line.
    assert any(e.get("thinking") for e in events)
