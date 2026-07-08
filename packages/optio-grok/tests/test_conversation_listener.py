"""ConversationListener unit tests against a fake Grok (ACP) Conversation.

Adapted from optio-claudecode's test_conversation_listener.py. The grok
listener exposes /control (engine-neutral session controls) instead of the old
/model route, and correlates permissions by the ACP JSON-RPC
``id`` of the ``session/request_permission`` request (grok's PermissionRequest
carries the whole JSON-RPC object as ``raw``).
"""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_grok.conversation_listener import ConversationListener


async def _wait_until(pred, timeout: float = 60.0):
    """Poll an observable predicate until true, bounded by a generous hang-ceiling.

    Waits for the EVENT rather than a fixed duration, so it stays correct when the
    box is CPU-starved and the awaited coroutine simply hasn't been scheduled yet.
    """
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
        self.controls = []
        self.closed = False
        # Prior conversation grok would replay on resume via session/load
        # (replay_history re-emits it through on_event).
        self.history = []

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    async def send(self, text):
        if self.closed:
            raise ConversationClosed("closed")
        self.sent.append(text)

    async def interrupt(self):
        if self.closed:
            raise ConversationClosed("closed")
        self.interrupts += 1

    async def set_control(self, control_id, value):
        if self.closed:
            raise ConversationClosed("closed")
        self.controls.append((control_id, value))

    def fire(self, event):
        for h in list(self.handlers):
            h(event)

    async def replay_history(self, session_id):
        # Resume backfill: session/load makes grok re-emit the prior conversation
        # as session/update notifications through the SAME on_event fan-out live
        # turns use, so the historic events land in the listener's replay buffer.
        for e in list(self.history):
            self.fire(e)
        return (True, "")


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def listener():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    yield conv, lst, f"http://127.0.0.1:{port}"
    await lst.stop()


async def _read_events(resp, n, timeout=60):
    """Parse n SSE data frames from an open aiohttp response."""
    out = []
    buf = b""

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
                if data:
                    out.append(json.loads(b"".join(data).strip()))

    await asyncio.wait_for(_go(), timeout)
    return out


def _u(kind, text):
    return {"method": "session/update", "params": {"update": {
        "sessionUpdate": kind, "content": {"type": "text", "text": text}}}}


async def test_durable_replay_survives_live_flood_for_late_subscriber(listener):
    # The resume backfill (durable tier) must NOT be evicted by a live
    # thought-chunk flood — a late-reconnecting viewer still sees full history.
    # grok floods the bounded ring with agent_thought_chunk, which would
    # otherwise evict the replayed session/load history (emitted first).
    from optio_grok.conversation_listener import BUFFER_MAXLEN
    conv, lst, url = listener
    lst.begin_replay()
    conv.fire(_u("user_message_chunk", "prior-q"))
    conv.fire(_u("agent_message_chunk", "prior-a"))
    lst.end_replay()
    for i in range(BUFFER_MAXLEN + 50):  # flood the bounded live ring
        conv.fire(_u("agent_thought_chunk", f"t{i}"))

    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            got = await _read_events(resp, 3)
    # Full replay served FIRST (survived eviction), in order, then recent live.
    assert got[0]["params"]["update"]["content"]["text"] == "prior-q"
    assert got[1]["params"]["update"]["content"]["text"] == "prior-a"
    assert got[2]["params"]["update"]["sessionUpdate"] == "agent_thought_chunk"


async def test_last_event_id_resumes_past_durable_replay_no_dup(listener):
    conv, lst, url = listener
    lst.begin_replay()
    conv.fire(_u("user_message_chunk", "prior-q"))   # seq 1
    conv.fire(_u("agent_message_chunk", "prior-a"))   # seq 2
    lst.end_replay()
    conv.fire(_u("agent_message_chunk", "live-1"))     # seq 3
    conv.fire(_u("agent_message_chunk", "live-2"))     # seq 4

    # A reconnect that already saw through the replay (seq 2) gets ONLY live —
    # the durable tier is not re-sent.
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "2"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            got = await _read_events(resp, 2)
    assert [g["params"]["update"]["content"]["text"] for g in got] == ["live-1", "live-2"]


async def test_replay_to_late_subscriber(listener):
    # A viewer that attaches AFTER events were fired still sees the buffered
    # history (the replay buffer), in order.
    conv, lst, url = listener
    conv.fire({"method": "session/update", "params": {"update": {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hi"}}}})
    conv.fire({"jsonrpc": "2.0", "id": 1,
               "result": {"stopReason": "end_turn"}})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert replay[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
            assert replay[1]["result"]["stopReason"] == "end_turn"
            # Live tail continues after the replay.
            conv.fire({"method": "session/update", "params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "more"}}}})
            live = await _read_events(resp, 1)
            assert live[0]["params"]["update"]["content"]["text"] == "more"


async def test_replay_history_backfills_buffer_for_late_subscriber(listener):
    # Resume backfill: replay_history (driven by session/load) re-emits the prior
    # conversation through on_event AFTER the listener subscribed (its
    # constructor), landing it in the replay buffer BEFORE any viewer attaches. A
    # viewer connecting post-resume then sees the full prior history, not just new
    # turns — the crux of the resume-history fix. Mirrors how session.py calls
    # replay_history after ConversationListener(...) is constructed.
    conv, lst, url = listener
    conv.history = [
        {"method": "session/update", "params": {"update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "prior-q"}}}},
        {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}},
    ]
    ok, _ = await conv.replay_history("old-session")
    assert ok is True
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert replay[0]["params"]["update"]["content"]["text"] == "prior-q"
            assert replay[1]["result"]["stopReason"] == "end_turn"


async def test_last_event_id_resume(listener):
    conv, lst, url = listener
    conv.fire({"n": 1})    # seq 1
    conv.fire({"n": 2})    # seq 2
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "1"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            events = await _read_events(resp, 1)
            assert events[0]["n"] == 2  # seq 1 skipped


async def test_send_forwards_to_conversation(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        assert r.status == 200 and conv.sent == ["hi"]
        r = await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert r.status == 200 and conv.interrupts == 1
        conv.closed = True
        r = await s.post(f"{url}/send", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_control_route_forwards_to_conversation(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/control", json={"id": "model", "value": "grok-build"}, headers=_auth("pw"))
        assert r.status == 200 and conv.controls == [("model", "grok-build")]
        # bad payloads (missing id)
        r = await s.post(f"{url}/control", json={"value": "x"}, headers=_auth("pw"))
        assert r.status == 400
        conv.closed = True
        r = await s.post(f"{url}/control", json={"id": "model", "value": "grok-build"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_by_jsonrpc_id(listener):
    # Grok's PermissionRequest.raw is the full ACP session/request_permission
    # JSON-RPC object; the listener correlates by its `id`.
    conv, lst, url = listener

    class Req:
        raw = {"id": 99, "method": "session/request_permission"}
        tool_name = "Shell"
        input = {"command": "echo hi"}

    task = asyncio.create_task(conv.perm_handler(Req()))
    # Wait for the handler to register the pending request rather than assuming
    # it ran within a fixed sleep (fragile under CPU starvation).
    await _wait_until(lambda: "99" in lst._pending_permissions)
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "99", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 60)
        assert isinstance(decision, PermissionDecision)
        assert decision.behavior == "allow"
        # A second answer for the resolved request is a 404.
        r = await s.post(f"{url}/permission",
                         json={"request_id": "99", "behavior": "deny"},
                         headers=_auth("pw"))
        assert r.status == 404
    # answered broadcast landed in the buffer
    assert any(e.get("type") == "x-optio-permission-answered"
               and e.get("request_id") == "99"
               for _, e in lst._buffer)


async def test_auth_rejected(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{url}/events", headers=_auth("WRONG"))
        assert r.status == 401
        r = await s.post(f"{url}/send", json={"text": "x"})
        assert r.status == 401


async def test_stop_returns_promptly_with_open_sse(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            conv.fire({"method": "session/update", "params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "x"}}}})
            await _read_events(resp, 1)  # handler is now in its live loop
            await asyncio.wait_for(lst.stop(), timeout=60)
