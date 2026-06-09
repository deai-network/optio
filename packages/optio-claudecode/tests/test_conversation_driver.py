"""ClaudeCodeConversation unit tests against an in-process fake handle."""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation import ClaudeCodeConversation


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


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = ClaudeCodeConversation(permission_gate=True)
    c.attach(handle)
    return c, handle


@pytest.mark.asyncio
async def test_gate_off_denies_can_use_tool_defensively():
    handle = _FakeHandle()
    c = ClaudeCodeConversation()  # permission_gate=False (default)
    c.attach(handle)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "control_request", "request_id": "perm-x",
                        "request": {"subtype": "can_use_tool",
                                    "tool_name": "Bash", "input": {}}})
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert resp["type"] == "control_response"
    assert resp["response"]["request_id"] == "perm-x"
    assert resp["response"]["response"]["behavior"] == "deny"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_writes_user_message_and_pending(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("hello")
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert sent["type"] == "user"
    assert sent["message"]["content"][0]["text"] == "hello"
    assert c.is_pending()
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "hi back", "is_error": False})
    await asyncio.sleep(0.05)
    assert not c.is_pending()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_on_event_transparent_and_on_message(convo):
    c, handle = convo
    events, texts = [], []
    c.on_event(events.append)
    c.on_message(texts.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init", "session_id": "s"})
    handle.stdout.feed({"type": "assistant", "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "partial"}]}})
    handle.stdout.feed({"type": "result", "subtype": "success",
                        "result": "final answer", "is_error": False})
    handle.stdout.eof()
    await reader
    types = [e["type"] for e in events]
    assert types[:3] == ["system", "assistant", "result"]
    assert types[-1] == "x-optio-closed"
    assert texts == ["final answer"]


@pytest.mark.asyncio
async def test_unparseable_line_becomes_synthetic_event(convo):
    c, handle = convo
    events = []
    c.on_event(events.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.queue.put_nowait(b"this is not json\n")
    handle.stdout.eof()
    await reader
    assert events[0]["type"] == "x-optio-unparseable"


@pytest.mark.asyncio
async def test_raising_handler_does_not_kill_dispatch(convo):
    c, handle = convo
    good = []
    c.on_event(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    c.on_event(good.append)
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "system", "subtype": "init"})
    handle.stdout.eof()
    await reader
    assert any(e["type"] == "system" for e in good)


@pytest.mark.asyncio
async def test_permission_roundtrip_and_late_registration(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.feed({"type": "control_request", "request_id": "perm-1",
                        "request": {"subtype": "can_use_tool",
                                    "tool_name": "Bash",
                                    "input": {"command": "rm -rf /"}}})
    await asyncio.sleep(0.05)  # arrives before any handler → queued

    async def handler(req):
        assert req.tool_name == "Bash"
        return PermissionDecision(behavior="deny", message="nope")

    c.on_permission_request(handler)
    resp = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert resp["type"] == "control_response"
    assert resp["response"]["request_id"] == "perm-1"
    assert resp["response"]["response"]["behavior"] == "deny"
    assert resp["response"]["response"]["message"] == "nope"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_interrupt_handshake(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await c.send("long task")
    intr = asyncio.create_task(c.interrupt())
    sent = await asyncio.wait_for(handle.stdin.lines.get(), 1)   # user msg
    ctrl = await asyncio.wait_for(handle.stdin.lines.get(), 1)   # control_request
    assert ctrl["request"]["subtype"] == "interrupt"
    handle.stdout.feed({"type": "control_response", "response": {
        "subtype": "success", "request_id": ctrl["request_id"]}})
    await asyncio.wait_for(intr, 1)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_send_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    handle.stdout.eof()
    await reader
    assert c.closed
    with pytest.raises(ConversationClosed):
        await c.send("too late")


@pytest.mark.asyncio
async def test_close_sets_close_requested(convo):
    c, handle = convo
    await c.close()
    assert c.close_requested.is_set()
