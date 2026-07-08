"""CursorConversation unit tests against an in-process fake ACP handle.

The fake handle feeds JSON-RPC lines (like `cursor-agent acp` would emit on
stdout) and captures what the driver writes to stdin. Adapted from
optio-grok's test_conversation.py (same public ACP protocol; Stage-7
features — model switching — are deferred, so those tests are absent).
"""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_cursor.conversation import CursorConversation
from optio_cursor.conversation_listener import ConversationListener


class _FakeStdin:
    def __init__(self):
        self.lines: asyncio.Queue[dict] = asyncio.Queue()

    def write(self, data: bytes) -> None:
        self.lines.put_nowait(json.loads(data.decode()))

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self):
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def feed(self, obj: dict) -> None:
        self.queue.put_nowait((json.dumps(obj) + "\n").encode())

    def eof(self) -> None:
        self.queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeHandle:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()


async def _bootstrap(c, handle, session_id="s1"):
    """Drive the initialize + session/new handshake by feeding responses."""
    boot = asyncio.create_task(c.bootstrap())
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert req1["method"] == "initialize"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert req2["method"] == "session/new"
    assert req2["params"]["cwd"] == "/w"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req2["id"],
                        "result": {"sessionId": session_id}})
    await asyncio.wait_for(boot, 60)


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = CursorConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


@pytest.mark.asyncio
async def test_emit_event_reaches_on_event_subscribers(convo):
    """The synthetic resume-notice injected at the replay->live boundary reaches
    on_event through the same queue+dispatch path as routed wire events."""
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    events: list = []
    c.on_event(events.append)
    c.emit_event({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {
            "sessionUpdate": "user_message_chunk",
            "content": {"type": "text", "text": "System: you have been resumed"}}},
    })
    await _wait_for(lambda: any(
        (e.get("params") or {}).get("update", {}).get("sessionUpdate")
        == "user_message_chunk"
        for e in events
    ))
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_drain_waits_for_all_events_dispatched(convo):
    """drain() blocks until every queued event has reached on_event — so the
    resume replay window can close (end_replay) only after all replayed/injected
    events are delivered, none leaking into the live ring."""
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    seen: list = []
    c.on_event(seen.append)
    for i in range(6):
        c.emit_event({"synthetic": i})
    await c.drain()
    assert [e.get("synthetic") for e in seen if "synthetic" in e] == [0, 1, 2, 3, 4, 5]
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_receive_and_on_event_transparent(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)

    assert not c.is_pending()
    await c.send("say PONG")
    assert c.is_pending()
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["method"] == "session/prompt"
    assert prompt["params"]["prompt"][0]["text"] == "say PONG"
    assert prompt["params"]["sessionId"] == "s1"

    # Two agent_message_chunk notifications then the turn-end response.
    for piece in ("PO", "NG"):
        handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                            "params": {"sessionId": "s1", "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": piece}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": prompt["id"],
                        "result": {"stopReason": "end_turn"}})

    reply = await asyncio.wait_for(_first(texts), 60)
    assert reply == "PONG"
    await _wait_for(lambda: not c.is_pending())

    kinds = [e.get("params", {}).get("update", {}).get("sessionUpdate")
             for e in events if e.get("method") == "session/update"]
    assert kinds.count("agent_message_chunk") >= 1

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_thought_chunks_not_folded_into_answer(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    texts = []
    c.on_message(texts.append)
    await c.send("think then answer")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": "s1", "update": {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": "hmm"}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": "s1", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "ANSWER"}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": prompt["id"],
                        "result": {"stopReason": "end_turn"}})
    reply = await asyncio.wait_for(_first(texts), 60)
    assert reply == "ANSWER"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_roundtrip_deny(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    seen = {}

    async def handler(req):
        seen["tool"] = req.tool_name
        seen["input"] = req.input
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    # Agent -> client permission request (has id + method).
    handle.stdout.feed({"jsonrpc": "2.0", "id": 99,
                        "method": "session/request_permission",
                        "params": {"sessionId": "s1", "toolCall": {
                            "toolCallId": "tc1", "kind": "execute",
                            "title": "Execute `echo hi`",
                            "rawInput": {"command": "echo hi"}},
                            "options": [
                                {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
                                {"optionId": "reject-once", "name": "No", "kind": "reject_once"}]}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert resp["id"] == 99
    assert resp["result"]["outcome"]["outcome"] == "selected"
    assert resp["result"]["outcome"]["optionId"] == "reject-once"
    assert seen["tool"]  # populated from the toolCall
    assert seen["input"] == {"command": "echo hi"}
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_permission_request_allow_selects_allow_option(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    handle.stdout.feed({"jsonrpc": "2.0", "id": 7,
                        "method": "session/request_permission",
                        "params": {"sessionId": "s1", "toolCall": {
                            "toolCallId": "tc2", "title": "Shell",
                            "rawInput": {"command": "ls"}},
                            "options": [
                                {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
                                {"optionId": "reject-once", "name": "No", "kind": "reject_once"}]}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert resp["result"]["outcome"]["optionId"] == "allow-once"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_gate_off_denies_permission_defensively():
    handle = _FakeHandle()
    c = CursorConversation(cwd="/w")  # permission_gate=False (default)
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.feed({"jsonrpc": "2.0", "id": 5,
                        "method": "session/request_permission",
                        "params": {"sessionId": "s1", "toolCall": {
                            "toolCallId": "tc", "title": "Shell", "rawInput": {}},
                            "options": [
                                {"optionId": "reject-once", "name": "No", "kind": "reject_once"}]}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert resp["id"] == 5
    # Defensive deny: a reject option is selected (or cancelled if none).
    assert resp["result"]["outcome"]["outcome"] in ("selected", "cancelled")
    if resp["result"]["outcome"]["outcome"] == "selected":
        assert resp["result"]["outcome"]["optionId"] == "reject-once"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_sends_session_cancel(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await c.send("count to 100")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert c.is_pending()
    await c.interrupt()
    cancel = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert cancel["method"] == "session/cancel"
    assert cancel["params"]["sessionId"] == "s1"
    assert "id" not in cancel  # notification, no response expected
    # The cancelled prompt response ends the turn.
    handle.stdout.feed({"jsonrpc": "2.0", "id": prompt["id"],
                        "result": {"stopReason": "cancelled"}})
    await _wait_for(lambda: not c.is_pending())
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert any(e.get("type") == "x-optio-unparseable" for e in events)


@pytest.mark.asyncio
async def test_eof_closes_and_emits_synthetic_closed(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    assert c.closed
    assert events[-1].get("type") == "x-optio-closed"
    with pytest.raises(ConversationClosed):
        await c.send("too late")


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()


@pytest.mark.asyncio
async def test_bootstrap_captures_session_models(convo):
    # session/new returns the ACP model block ([grok-pinned, cursor
    # runtime-unverified]); CursorConversation captures it so the session can
    # push the picker options without a separate `cursor-agent models`
    # subprocess (which is auth-gated anyway).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    boot = asyncio.create_task(c.bootstrap())
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"jsonrpc": "2.0", "id": req2["id"], "result": {
        "sessionId": "s1",
        "models": {
            "currentModelId": "composer-1",
            "availableModels": [
                {"modelId": "composer-1", "name": "Composer 1"},
                {"modelId": "gpt-5", "name": "GPT-5"},
            ],
        },
    }})
    await asyncio.wait_for(boot, 60)
    assert c.current_model_id == "composer-1"
    assert c.session_models["availableModels"][1]["modelId"] == "gpt-5"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_model_sends_set_model(convo):
    # The engine-neutral model control switches INLINE via ACP session/set_model
    # — grok's live-pinned mechanism; the method is present in the cursor binary
    # [cursor-verified] but a logged-in probe wasn't possible (see models.py).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    change = asyncio.create_task(c.set_control("model", "gpt-5"))
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert msg["method"] == "session/set_model"
    assert msg["params"]["sessionId"] == "s1"
    assert msg["params"]["modelId"] == "gpt-5"
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
    await asyncio.wait_for(change, 60)
    assert c.current_model_id == "gpt-5"  # set after the round-trip
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_unknown_id_is_noop(convo):
    # cursor exposes only the model control; any other id must not touch the wire.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    await asyncio.wait_for(c.set_control("thinking", "high"), 60)
    assert handle.stdin.lines.empty()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_model_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    with pytest.raises(ConversationClosed):
        await c.set_control("model", "gpt-5")


# --- resume: conversation-history replay via session/list + session/load -----


@pytest.mark.asyncio
async def test_resume_lists_then_loads_prior_session(convo):
    # On resume the wrapper enumerates the restored on-disk ACP sessions
    # (session/list), skips the fresh session/new it just minted, picks the
    # prior conversation, and session/load's it — cursor then replays that
    # conversation via session/update notifications (which reach on_event) and
    # the driver adopts the loaded session so the next prompt continues the
    # prior thread.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    events = []
    c.on_event(events.append)

    replay = asyncio.create_task(c.replay_history())
    listing = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert listing["method"] == "session/list"
    handle.stdout.feed({"jsonrpc": "2.0", "id": listing["id"], "result": {
        "sessions": [
            {"sessionId": "fresh-sess", "cwd": "/w"},   # the one bootstrap minted
            {"sessionId": "prior-sess", "cwd": "/w"},   # the restored prior chat
        ]}})
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"
    assert load["params"]["sessionId"] == "prior-sess"
    # Replay notifications arrive BEFORE the load response (wire order).
    for kind, text in (("user_message_chunk", "prior question"),
                       ("agent_message_chunk", "prior answer")):
        handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                            "params": {"sessionId": "prior-sess", "update": {
                                "sessionUpdate": kind,
                                "content": {"type": "text", "text": text}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"], "result": {}})

    assert await asyncio.wait_for(replay, 60) == "prior-sess"
    # The replayed session/update events reached on_event.
    await _wait_for(lambda: sum(
        1 for e in events if e.get("method") == "session/update") >= 2)

    # The replayed agent_message_chunk must NOT leak into the first live turn's
    # answer: adopt the loaded session and drop the replay's accumulated text.
    msgs = []
    c.on_message(msgs.append)
    assert msgs == []                       # replay synthesised no turn message
    await c.send("continue please")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["params"]["sessionId"] == "prior-sess"   # continues prior thread
    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": "prior-sess", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "NEW"}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": prompt["id"],
                        "result": {"stopReason": "end_turn"}})
    reply = await asyncio.wait_for(_first(msgs), 60)
    assert reply == "NEW"                    # NOT "prior answerNEW"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_resume_replays_prior_history_to_late_subscriber(convo):
    # End-to-end resume backfill: with the ConversationListener subscribed to
    # on_event (its constructor — the subscribe-before-replay ordering session.py
    # guarantees), replay_history drives session/list + session/load and cursor's
    # replay notifications flow through the SAME on_event fan-out into the replay
    # buffer. A viewer attaching AFTER the replay then reconstructs the full prior
    # history — the crux of the resume-history fix.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    listener = ConversationListener(c, password="pw")
    port = await listener.start("127.0.0.1")
    try:
        replay = asyncio.create_task(c.replay_history())
        listing = await asyncio.wait_for(handle.stdin.lines.get(), 60)
        assert listing["method"] == "session/list"
        handle.stdout.feed({"jsonrpc": "2.0", "id": listing["id"], "result": {
            "sessions": [{"sessionId": "prior-sess", "cwd": "/w"}]}})
        load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
        assert load["method"] == "session/load"
        for text in ("prior-q", "prior-a"):
            handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                                "params": {"sessionId": "prior-sess", "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": text}}}})
        handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"], "result": {}})
        assert await asyncio.wait_for(replay, 60) == "prior-sess"

        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/events",
                             headers=_auth("pw")) as resp:
                assert resp.status == 200
                texts = await _collect_update_texts(resp, 2)
                assert texts == ["prior-q", "prior-a"]
    finally:
        await listener.stop()
        handle.stdout.eof()
        await reader


@pytest.mark.asyncio
async def test_resume_empty_session_list_falls_back_to_new(convo):
    # Graceful fallback: session/list yields nothing loadable (only the fresh
    # session we just minted) → no session/load, replay_history returns None,
    # and the conversation still works on the fresh session (resume never breaks;
    # it just shows no history).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    replay = asyncio.create_task(c.replay_history())
    listing = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert listing["method"] == "session/list"
    handle.stdout.feed({"jsonrpc": "2.0", "id": listing["id"], "result": {
        "sessions": [{"sessionId": "fresh-sess", "cwd": "/w"}]}})
    assert await asyncio.wait_for(replay, 60) is None
    assert handle.stdin.lines.empty()       # no session/load attempted
    await c.send("hello")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["method"] == "session/prompt"
    assert prompt["params"]["sessionId"] == "fresh-sess"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_resume_session_load_error_falls_back_to_new(convo):
    # Graceful fallback: a prior session is found but session/load errors →
    # replay_history returns None and the conversation keeps the fresh session,
    # so a subsequent send still works and no exception escapes.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    replay = asyncio.create_task(c.replay_history())
    listing = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"jsonrpc": "2.0", "id": listing["id"], "result": {
        "sessions": [{"sessionId": "prior-sess", "cwd": "/w"}]}})
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"],
                        "error": {"code": -32000, "message": "cannot load"}})
    assert await asyncio.wait_for(replay, 60) is None
    await c.send("hello")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["params"]["sessionId"] == "fresh-sess"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_session_id_property_reflects_current_working_id(convo):
    # The session_id property exposes the live ACP session id (the session/new id
    # on a fresh task, the adopted prior id after a resume) — the seam session.py
    # threads into the snapshot so the NEXT resume can session/load it directly.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="s1")
    assert c.session_id == "s1"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_resume_with_persisted_id_loads_directly_skipping_list(convo):
    # When the snapshot persisted the prior ACP session id, replay_history loads
    # it DIRECTLY — NO session/list heuristic (which can mispick an empty session
    # as they accumulate). cursor replays that conversation via session/update
    # notifications (reaching on_event) and the loaded id is adopted so the next
    # prompt continues the prior thread.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    events = []
    c.on_event(events.append)

    replay = asyncio.create_task(c.replay_history("persisted-sess"))
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"        # NOT session/list
    assert load["params"]["sessionId"] == "persisted-sess"
    for kind, text in (("user_message_chunk", "prior question"),
                       ("agent_message_chunk", "prior answer")):
        handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                            "params": {"sessionId": "persisted-sess", "update": {
                                "sessionUpdate": kind,
                                "content": {"type": "text", "text": text}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"], "result": {}})

    assert await asyncio.wait_for(replay, 60) == "persisted-sess"
    # The replayed session/update events reached on_event (listener replay buffer).
    await _wait_for(lambda: sum(
        1 for e in events if e.get("method") == "session/update") >= 2)
    # Adopted -> a subsequent send continues the prior thread.
    assert c.session_id == "persisted-sess"
    await c.send("continue please")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["params"]["sessionId"] == "persisted-sess"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_resume_persisted_id_load_error_keeps_fresh_no_list(convo):
    # Graceful fallback with a persisted id: session/load errors -> replay_history
    # returns None and KEEPS the fresh session; it does NOT fall back to the
    # session/list heuristic (the persisted id is authoritative). Resume never
    # breaks; a subsequent send still works on the fresh session.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle, session_id="fresh-sess")
    replay = asyncio.create_task(c.replay_history("gone-sess"))
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"],
                        "error": {"code": -32000, "message": "cannot load"}})
    assert await asyncio.wait_for(replay, 60) is None
    assert handle.stdin.lines.empty()      # did NOT fall back to session/list
    await c.send("hello")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["params"]["sessionId"] == "fresh-sess"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_fresh_start_never_lists_or_loads(convo):
    # A fresh (non-resume) conversation bootstraps with session/new and sends
    # via session/prompt — it must NEVER touch session/list or session/load
    # (those are the resume-only backfill path; session.py gates replay_history
    # on resuming). Locks bootstrap unchanged.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    boot = asyncio.create_task(c.bootstrap())
    methods = []
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    methods.append(req1["method"])
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    methods.append(req2["method"])
    handle.stdout.feed({"jsonrpc": "2.0", "id": req2["id"],
                        "result": {"sessionId": "s1"}})
    await asyncio.wait_for(boot, 60)
    await c.send("hi")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    methods.append(prompt["method"])
    assert methods == ["initialize", "session/new", "session/prompt"]
    handle.stdout.eof()
    await reader


# --- tiny polling helpers ---------------------------------------------------


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _collect_update_texts(resp, n, timeout=60):
    """Read SSE data frames and collect the text of the first n session/update
    events (ignoring the request-response objects the driver also broadcasts)."""
    out, buf = [], b""

    async def _go():
        nonlocal buf
        while len(out) < n:
            chunk = await resp.content.read(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if not data:
                    continue
                ev = json.loads(b"".join(data).strip())
                if ev.get("method") == "session/update":
                    text = (((ev.get("params") or {}).get("update") or {})
                            .get("content") or {}).get("text")
                    if text is not None:
                        out.append(text)

    await asyncio.wait_for(_go(), timeout)
    return out

async def _first(bucket: list, timeout: float = 60.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if bucket:
            return bucket[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no item arrived")


async def _wait_for(pred, timeout: float = 60.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")
