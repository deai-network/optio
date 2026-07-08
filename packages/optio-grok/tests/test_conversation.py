"""GrokConversation unit tests against an in-process fake ACP handle.

The fake handle feeds JSON-RPC lines (like `grok agent stdio` would emit on
stdout) and captures what the driver writes to stdin. Mirrors
optio-claudecode's test_conversation_driver.py, but frames ACP JSON-RPC 2.0
instead of claude stream-json.
"""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_grok.conversation import GrokConversation


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
    c = GrokConversation(cwd="/w", permission_gate=True)
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
async def test_skills_reload_noise_dropped_but_turn_end_forwarded(convo):
    """grok emits ~1/sec ``{id:'skills-reload', result:{reloaded:1}}`` — responses
    to a request we never sent. They must NOT reach viewers (they flood the SSE and
    evict real events from the bounded replay buffer), while real session/update
    notifications AND the turn-end response (which the UI needs to clear busy) must
    still be forwarded."""
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    events: list = []
    c.on_event(events.append)

    for _ in range(5):
        handle.stdout.feed({"jsonrpc": "2.0", "id": "skills-reload",
                            "result": {"result": {"reloaded": 1}}})

    await c.send("hi")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": "s1", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "yo"}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": prompt["id"],
                        "result": {"stopReason": "end_turn"}})
    await _wait_for(lambda: not c.is_pending())

    # skills-reload noise suppressed
    assert not any(e.get("id") == "skills-reload" for e in events)
    # real session/update still forwarded
    assert any(e.get("method") == "session/update" for e in events)
    # turn-end response still forwarded (UI needs stopReason to clear busy)
    assert any(e.get("id") == prompt["id"] and (e.get("result") or {}).get("stopReason")
               for e in events)

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
    c = GrokConversation(cwd="/w")  # permission_gate=False (default)
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
    # session/new returns the ACP model block; GrokConversation captures it so
    # the session can push the picker options without a separate `grok models`
    # subprocess.
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
            "currentModelId": "grok-composer-2.5-fast",
            "availableModels": [
                {"modelId": "grok-composer-2.5-fast", "name": "Composer 2.5"},
                {"modelId": "grok-build", "name": "Grok Build"},
            ],
        },
    }})
    await asyncio.wait_for(boot, 60)
    assert c.current_model_id == "grok-composer-2.5-fast"
    assert c.session_models["availableModels"][1]["modelId"] == "grok-build"
    handle.stdout.eof()
    await reader


# NOTE: model switching moved from request_model_change() to the engine-neutral
# set_control("model", …) surface — see test_conversation_controls.py.


@pytest.mark.asyncio
async def test_replay_history_loads_session_and_emits_updates(convo):
    """On resume replay_history issues ``session/load(id)``; grok re-emits the
    prior conversation as ``session/update`` notifications (reaching on_event, so
    a listener's replay buffer backfills) and the loaded id is adopted so
    subsequent turns continue the prior thread."""
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)   # fresh session/new -> s1

    events: list = []
    c.on_event(events.append)

    replay = asyncio.create_task(c.replay_history("old-session"))
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"
    assert load["params"]["sessionId"] == "old-session"

    # grok replays the prior turn's update, THEN answers the load request.
    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": "old-session", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "prior-answer"}}}})
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"], "result": {}})

    ok, detail = await asyncio.wait_for(replay, 60)
    assert ok is True
    assert detail == ""
    # The loaded session is adopted so subsequent turns continue the prior thread.
    assert c.session_id == "old-session"
    # The replayed update reached the on_event fan-out (listener replay buffer).
    await _wait_for(lambda: any(
        e.get("params", {}).get("update", {}).get("sessionUpdate")
        == "agent_message_chunk"
        for e in events if e.get("method") == "session/update"
    ))

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_replay_history_error_falls_back_keeping_fresh_session(convo):
    """If session/load fails (unknown id / no loadable store after restore),
    replay_history returns ``(False, reason)`` and KEEPS the fresh bootstrap
    session so resume degrades to no-history — it never breaks the conversation."""
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)   # fresh session/new -> s1

    replay = asyncio.create_task(c.replay_history("gone"))
    load = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert load["method"] == "session/load"
    handle.stdout.feed({"jsonrpc": "2.0", "id": load["id"],
                        "error": {"code": -32000, "message": "no such session"}})

    ok, detail = await asyncio.wait_for(replay, 60)
    assert ok is False
    assert "no such session" in detail
    # Fresh session preserved -> the conversation is still usable.
    assert c.session_id == "s1"
    await c.send("still works")
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    assert prompt["params"]["sessionId"] == "s1"

    handle.stdout.eof()
    await reader


# --- tiny polling helpers ---------------------------------------------------

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
