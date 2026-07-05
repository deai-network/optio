"""GrokConversation.set_control unit tests (engine-neutral session controls).

grok exposes only the ``model`` control, switched INLINE over ACP with
``session/set_model`` (Stage-7 Task-0 probe: no process restart). These tests
drive the fake ACP handle (same shape as test_conversation.py) and assert the
wire the driver emits for a set_control("model", …) round-trip, plus that
unknown control ids are a no-op.
"""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed
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
    boot = asyncio.create_task(c.bootstrap())
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req1["method"] == "initialize"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req2["method"] == "session/new"
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
async def test_set_control_model_sends_set_model(convo):
    # set_control("model", x) emits an INLINE session/set_model ACP request
    # (no process restart) and updates the model optimistically.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    ctrl = asyncio.create_task(c.set_control("model", "grok-build"))
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert msg["method"] == "session/set_model"
    assert msg["params"]["sessionId"] == "s1"
    assert msg["params"]["modelId"] == "grok-build"
    assert c.current_model_id == "grok-build"  # optimistic
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"],
                        "result": {"_meta": {"model": {"Ok": "grok-build-0.1"}}}})
    await asyncio.wait_for(ctrl, 1)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_unknown_id_is_noop(convo):
    # grok exposes only the model control; any other id is silently ignored
    # (no ACP request written, model unchanged).
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    c.current_model_id = "grok-composer-2.5-fast"
    await c.set_control("thinking", "high")
    assert handle.stdin.lines.empty()
    assert c.current_model_id == "grok-composer-2.5-fast"
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_after_close_raises(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    handle.stdout.eof()
    await reader
    with pytest.raises(ConversationClosed):
        await c.set_control("model", "grok-build")
