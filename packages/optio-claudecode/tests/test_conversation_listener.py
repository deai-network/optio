"""ConversationListener unit tests against a fake Conversation."""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation_listener import ConversationListener


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
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


async def test_replay_then_live_and_partial_exclusion(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "stream_event", "n": "partial"})  # live-only
    conv.fire({"type": "result", "n": 2})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert [e["type"] for e in replay] == ["user", "result"]  # no partial
            conv.fire({"type": "stream_event", "n": "live-partial"})
            live = await _read_events(resp, 1)
            assert live[0]["type"] == "stream_event"  # partials DO flow live


async def test_last_event_id_resume(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})    # seq 1
    conv.fire({"type": "result", "n": 2})  # seq 2
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "1"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            events = await _read_events(resp, 1)
            assert events[0]["n"] == 2  # seq 1 skipped


async def test_send_interrupt_forwarding_and_closed(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        assert r.status == 200 and conv.sent == ["hi"]
        r = await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert r.status == 200 and conv.interrupts == 1
        conv.closed = True
        r = await s.post(f"{url}/send", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_and_second_answer_404(listener):
    conv, lst, url = listener

    class Req:
        raw = {"request_id": "perm-1"}
        tool_name = "Bash"
        input = {}

    task = asyncio.create_task(conv.perm_handler(Req()))
    # Wait until the handler has actually parked the pending request before we
    # answer it; polling this observable avoids a fixed wall-clock delay that
    # flakes when the handler task is CPU-starved and hasn't registered yet.
    import time
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if "perm-1" in lst._pending_permissions:
            break
        await asyncio.sleep(0.02)
    assert "perm-1" in lst._pending_permissions
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 60)
        assert isinstance(decision, PermissionDecision)
        assert decision.behavior == "allow"
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "deny"},
                         headers=_auth("pw"))
        assert r.status == 404
    # answered broadcast landed in the buffer
    assert any(e.get("type") == "x-optio-permission-answered"
               for _, e in lst._buffer)


async def test_auth_rejected(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{url}/events", headers=_auth("WRONG"))
        assert r.status == 401
        r = await s.post(f"{url}/send", json={"text": "x"})
        assert r.status == 401


async def test_stop_returns_promptly_with_open_sse(listener):
    # An open /events SSE handler is a long-lived loop. stop() must wake it so
    # runner.cleanup() does not block on aiohttp's graceful-shutdown wait —
    # otherwise the session's cooperative-cancel teardown overruns its grace
    # period and the resume snapshot is never captured.
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            conv.fire({"type": "system", "subtype": "init"})
            await _read_events(resp, 1)  # handler is now in its live loop
            # Must return well under aiohttp's 60s default graceful wait.
            await asyncio.wait_for(lst.stop(), timeout=5)


async def test_buffer_export_reprime_continues_seq():
    # Resume persistence: a listener's buffer can be exported and used to
    # re-prime a fresh listener, so a viewer after resume sees prior history;
    # new events continue the seq monotonically above the restored max.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "result", "n": 2})
    exported = lst.export_buffer()
    assert [e["n"] for _, e in [(x[0], x[1]) for x in exported]] == [1, 2]

    conv2 = FakeConversation()
    lst2 = ConversationListener(
        conv2, password="pw", initial_events=[(x[0], x[1]) for x in exported],
    )
    assert [e["n"] for _, e in lst2._buffer] == [1, 2]
    conv2.fire({"type": "user", "n": 3})
    assert lst2._buffer[-1][0] > exported[-1][0]


async def test_export_buffer_excludes_terminal_closed():
    # The terminal x-optio-closed marks THIS run's end, not conversation
    # content; persisting + replaying it on resume would make the UI treat the
    # live resumed session as already closed. export_buffer must drop it.
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "result", "n": 2})
    conv.fire({"type": "x-optio-closed", "reason": "process ended"})
    exported = lst.export_buffer()
    types = [e.get("type") for _, e in [(x[0], x[1]) for x in exported]]
    assert "x-optio-closed" not in types
    assert types == ["user", "result"]
