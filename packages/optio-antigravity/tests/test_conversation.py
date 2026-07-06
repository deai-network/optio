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
    TurnMessage,
    _TurnAccum,
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
    # Parse the fixture as one turn's worth of transcript via the incremental
    # drain (the live-tail path), then emit the coalesced message like send().
    accum = _TurnAccum(offset=0)
    await conv._emit_events(conv._read_new_events(0), accum)
    await conv._emit(
        conv._message_handlers,
        TurnMessage(text=accum.answer, events=tuple(accum.events)),
        "on_message",
    )

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


def test_build_argv_prompt_is_print_value_with_flags_before():
    # REGRESSION: agy's --print/-p TAKES the prompt as its VALUE (real binary:
    # `agy -p` with no arg → "flag needs an argument: -p"). So the text must
    # immediately follow -p and every bool flag must precede it — otherwise -p
    # swallowed `--dangerously-skip-permissions` as the prompt and the real text
    # leaked as a second user message.
    from optio_antigravity.conversation import AntigravityConversation
    conv = AntigravityConversation(
        host=None, agy_path="/x/agy", cwd="/w", home="/w/home", skip_permissions=True,
    )
    argv = conv._build_argv("hello world")
    i = argv.index("-p")
    assert argv[i + 1] == "hello world"                         # text is -p's value
    assert argv.index("--dangerously-skip-permissions") < i     # flag BEFORE -p
    assert argv.count("hello world") == 1                       # no stray positional

    conv._conversation_id = "uuid-1"
    conv._model = "gemini-3"
    argv2 = conv._build_argv("q")
    j = argv2.index("-p")
    assert argv2[j + 1] == "q"
    assert argv2.index("--conversation") < j and argv2.index("--model") < j


def test_read_complete_events_leaves_partial_line_for_next_poll(tmp_path):
    # Live tailing must not consume a half-written line. _read_complete_events
    # reads only up to the last newline; the trailing partial waits for the next
    # poll (when agy finishes writing it).
    conv = AntigravityConversation(
        host=None, agy_path="agy", cwd=str(tmp_path / "w"), home=str(tmp_path / "home"),
    )
    conv._conversation_id = "cid"
    t = pathlib.Path(conv._transcript_path())
    t.parent.mkdir(parents=True)
    line1 = '{"type":"USER_INPUT","content":"hi"}\n'
    line2 = '{"type":"PLANNER_RESPONSE","content":"PONG"}\n'
    # write line1 fully + a PARTIAL line2 (no trailing newline yet)
    t.write_bytes((line1 + line2.rstrip("\n")).encode())
    events, off = conv._read_complete_events(0)
    assert [e.get("type") for e in events] == ["USER_INPUT"]   # only the complete line
    assert off == len(line1.encode())                          # advanced past line1 only
    # agy finishes line2 → next poll from the returned offset picks it up once.
    t.write_bytes((line1 + line2).encode())
    events2, off2 = conv._read_complete_events(off)
    assert [e.get("type") for e in events2] == ["PLANNER_RESPONSE"]
    assert off2 == len((line1 + line2).encode())


def test_resume_from_disk_adopts_prior_conversation(tmp_path):
    # On resume the restored workdir carries last_conversations.json (cwd->uuid);
    # resume_from_disk preloads it so the first turn CONTINUES via --conversation
    # instead of minting a new conversation.
    import json as _json
    conv = AntigravityConversation(
        host=None, agy_path="agy", cwd=str(tmp_path / "w"), home=str(tmp_path / "home"),
    )
    cache = pathlib.Path(conv._cache_path())
    cache.parent.mkdir(parents=True)
    cache.write_text(_json.dumps({conv._cwd: "restored-uuid"}))

    assert conv.conversation_id is None
    assert conv.resume_from_disk() == "restored-uuid"
    assert conv.conversation_id == "restored-uuid"
    # The next turn continues the restored conversation.
    argv = conv._build_argv("hi")
    j = argv.index("-p")
    assert "--conversation" in argv and "restored-uuid" in argv
    assert argv.index("--conversation") < j          # flag before -p (value semantics)
