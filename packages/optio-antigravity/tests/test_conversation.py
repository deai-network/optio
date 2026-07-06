"""AntigravityConversation unit tests against the fake ``agy`` binary.

Unlike grok (ACP/JSON-RPC over a live stdio subprocess), Antigravity has no
live transport: the conversation is **synthetic**, driven by repeated one-shot
``agy -p`` invocations under a PTY, with events read from the tailed
``~/.gemini/antigravity/transcript.jsonl`` (design §5). These tests exercise
that driver over ``fake_agy.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from optio_antigravity.conversation import AntigravityConversation, ConversationClosed


async def test_send_runs_print_turn_and_emits_final_message(fake_agy_conv):
    conv = fake_agy_conv
    events = []
    conv.on_event(lambda e: events.append(e))
    msgs = []
    conv.on_message(lambda m: msgs.append(m))
    await conv.send("say PONG")
    # One coalesced final answer per completed turn (turn = one -p invocation).
    assert any(m.text.strip() == "PONG" for m in msgs)
    # Transcript events fanned out live to on_event (raw dicts).
    assert any(e.get("type") == "assistant" for e in events)
    assert conv.closed is False


async def test_second_turn_uses_conversation_id(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    cid = conv.conversation_id
    assert cid  # captured from turn 1
    await conv.send("second")
    # Turn 2 resumes the captured conversation, not a fresh --new-project.
    assert conv.last_argv_contains(f"--conversation {cid}")


async def test_first_turn_starts_new_project(fake_agy_conv):
    conv = fake_agy_conv
    await conv.send("first")
    # Turn 1 mints a new conversation, so it must NOT carry --conversation.
    assert conv.last_argv_contains("--new-project")
    assert not conv.last_argv_contains("--conversation")


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
