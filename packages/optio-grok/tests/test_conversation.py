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
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req1["method"] == "initialize"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req2["method"] == "session/new"
    assert req2["params"]["cwd"] == "/w"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req2["id"],
                        "result": {"sessionId": session_id}})
    await asyncio.wait_for(boot, 1)


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = GrokConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


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
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 1)
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

    reply = await asyncio.wait_for(_first(texts), 2)
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
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 1)
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
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 1)
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
    reply = await asyncio.wait_for(_first(texts), 2)
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
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
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
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
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
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 2)
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
    prompt = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert c.is_pending()
    await c.interrupt()
    cancel = await asyncio.wait_for(handle.stdin.lines.get(), 1)
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
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
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
    await asyncio.wait_for(boot, 1)
    assert c.current_model_id == "grok-composer-2.5-fast"
    assert c.session_models["availableModels"][1]["modelId"] == "grok-build"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_request_model_change_sends_set_model(convo):
    # Stage-7 Task-0 probe pinned INLINE switching: request_model_change emits a
    # session/set_model ACP request (no process restart).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    c.request_model_change("grok-build")
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert msg["method"] == "session/set_model"
    assert msg["params"]["sessionId"] == "s1"
    assert msg["params"]["modelId"] == "grok-build"
    assert c.current_model_id == "grok-build"  # optimistic
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"],
                        "result": {"_meta": {"model": {"Ok": "grok-build-0.1"}}}})
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_request_model_change_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    with pytest.raises(ConversationClosed):
        c.request_model_change("grok-build")


# --- tiny polling helpers ---------------------------------------------------

async def _first(bucket: list, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if bucket:
            return bucket[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no item arrived")


async def _wait_for(pred, timeout: float = 2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")
