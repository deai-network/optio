"""ConversationListener unit tests against a fake Kimi Code (ACP) Conversation.

Ported from optio-grok's test_conversation_listener.py (both listeners are the
same ACP-shaped structure). Permissions are correlated by the ACP JSON-RPC
``id`` of the ``session/request_permission`` request — KimiCodeConversation
carries the whole JSON-RPC object as ``PermissionRequest.raw``. The ``/model``
route drives kimi's inline ``session/set_model`` switch.
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
        self.model_changes = []
        self.closed = False

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

    def request_model_change(self, model):
        if self.closed:
            raise ConversationClosed("closed")
        self.model_changes.append(model)

    def fire(self, event):
        for h in list(self.handlers):
            h(event)


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


async def test_model_route_forwards_to_conversation(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/model", json={"model": "kimi-k2-thinking"}, headers=_auth("pw"))
        assert r.status == 200 and conv.model_changes == ["kimi-k2-thinking"]
        # bad payloads
        r = await s.post(f"{url}/model", json={}, headers=_auth("pw"))
        assert r.status == 400
        conv.closed = True
        r = await s.post(f"{url}/model", json={"model": "kimi-k2-thinking"}, headers=_auth("pw"))
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
