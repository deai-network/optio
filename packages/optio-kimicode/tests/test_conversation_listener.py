"""ConversationListener unit tests against a fake Kimi Code (ACP) Conversation.

Ported from optio-grok's test_conversation_listener.py (both listeners are the
same ACP-shaped structure). Permissions are correlated by the ACP JSON-RPC
``id`` of the ``session/request_permission`` request — KimiCodeConversation
carries the whole JSON-RPC object as ``PermissionRequest.raw``. The ``/control``
route drives kimi's inline session-control switch (model -> session/set_model,
thinking/mode -> session/set_config_option).
"""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_kimicode.conversation_listener import ConversationListener


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
        self.controls = []  # list[(id, value)] set via set_control
        self.closed = False
        # Prior session/updates the driver backfills on resume (replay_history).
        self.history = []
        self.loaded = None  # sessionId passed to replay_history

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
        # Resume backfill: session/load re-emits the restored prior session's
        # turns through the SAME on_event fan-out live turns use (what
        # KimiCodeConversation does), landing them in the listener's replay
        # buffer. Returns True when it replayed (mirrors the real bool contract).
        self.loaded = session_id
        for e in list(self.history):
            self.fire(e)
        return bool(self.history)


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


async def _read_events(resp, n, timeout=5):
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
    # Resume backfill: the driver's replay_history (session/load) re-emits the
    # restored prior turns through on_event AFTER the listener has subscribed (its
    # constructor), landing them in the replay buffer BEFORE any viewer attaches.
    # A viewer connecting post-resume then sees the full prior history, not just
    # new turns — the crux of the resume-history fix. Mirrors how session.py wires
    # replay_history after ConversationListener(...) is constructed.
    conv, lst, url = listener
    conv.history = [
        {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
            "sessionUpdate": "user_message_chunk",
            "content": {"type": "text", "text": "prior-q"}}}},
        {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "prior-a"}}}},
    ]
    replayed = await conv.replay_history("prior-sess")
    assert replayed is True
    assert conv.loaded == "prior-sess"
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert [e["params"]["update"]["content"]["text"]
                    for e in replay] == ["prior-q", "prior-a"]


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
        # model control -> set_control("model", ...)
        r = await s.post(f"{url}/control", json={"id": "model", "value": "kimi-k2-thinking"}, headers=_auth("pw"))
        assert r.status == 200 and conv.controls == [("model", "kimi-k2-thinking")]
        # a non-model control (thinking) round-trips the same route
        r = await s.post(f"{url}/control", json={"id": "thinking", "value": "on"}, headers=_auth("pw"))
        assert r.status == 200 and conv.controls[-1] == ("thinking", "on")
        # missing id -> 400
        r = await s.post(f"{url}/control", json={"value": "x"}, headers=_auth("pw"))
        assert r.status == 400
        conv.closed = True
        r = await s.post(f"{url}/control", json={"id": "model", "value": "kimi-k2-thinking"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_by_jsonrpc_id(listener):
    # KimiCode's PermissionRequest.raw is the full ACP session/request_permission
    # JSON-RPC object; the listener correlates by its `id`.
    conv, lst, url = listener

    class Req:
        raw = {"id": 99, "method": "session/request_permission"}
        tool_name = "Shell"
        input = {"command": "echo hi"}

    task = asyncio.create_task(conv.perm_handler(Req()))
    await asyncio.sleep(0.05)
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "99", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 2)
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
            await asyncio.wait_for(lst.stop(), timeout=5)
