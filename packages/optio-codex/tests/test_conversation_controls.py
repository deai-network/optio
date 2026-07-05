"""SessionControls surface for optio-codex.

Codex exposes a single, model-only control (``id="model"``), switched INLINE:
``set_control("model", …)`` pins the model on the next ``turn/start`` with no
wire write, and any other control id is ignored. These tests drive a
CodexConversation against an in-process fake app-server handle (mirrors
test_conversation.py's harness) and check the widget-control projection built
from the captured ``model/list`` via ``optio_agents.session_controls``.
"""

import asyncio
import json

import pytest

from optio_agents.session_controls import model_control
from optio_codex import models as codex_models
from optio_codex.conversation import CodexConversation


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


MODEL_LIST = {
    "data": [
        {"id": "gpt-5.5", "displayName": "GPT-5.5", "hidden": False, "isDefault": True},
        {"id": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini", "hidden": False, "isDefault": False},
    ],
    "nextCursor": None,
}


async def _bootstrap(c, handle, thread_id="t1"):
    boot = asyncio.create_task(c.bootstrap())
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "initialize"
    handle.stdout.feed({"id": req["id"], "result": {"userAgent": "fake"}})
    note = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert note == {"method": "initialized"}
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "account/read"
    handle.stdout.feed({"id": req["id"], "result": {"account": {"type": "apikey"}}})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "model/list"
    handle.stdout.feed({"id": req["id"], "result": MODEL_LIST})
    req = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req["method"] == "thread/start"
    handle.stdout.feed({"id": req["id"], "result": {
        "thread": {"id": thread_id}, "model": "gpt-5.5"}})
    await asyncio.wait_for(boot, 1)


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = CodexConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


@pytest.mark.asyncio
async def test_set_control_model_pins_next_turn(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    await c.set_control("model", "gpt-5.4-mini")
    assert c.current_model_id == "gpt-5.4-mini"     # optimistic
    assert handle.stdin.lines.empty()                # INLINE — no wire write

    await c.send("hi")
    turn = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert turn["method"] == "turn/start"
    assert turn["params"]["model"] == "gpt-5.4-mini"

    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_non_model_id_ignored(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)

    await c.set_control("thinking", "high")          # codex has no such control
    assert c.current_model_id == "gpt-5.5"           # unchanged
    assert c._requested_model is None                # no inline override armed

    await c.send("hi")
    turn = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert "model" not in turn["params"]             # nothing pinned

    handle.stdout.eof()
    await reader


def test_model_control_projection_from_model_list():
    # The widget control the session emits is built from parse_model_list via
    # the shared model_control helper: one id="model" select.
    parsed = codex_models.parse_model_list(MODEL_LIST)
    control = model_control(models=parsed["models"], current=parsed["default"])
    d = control.to_dict()
    assert d["id"] == "model" and d["kind"] == "select"
    assert d["category"] == "model"
    assert d["value"] == "gpt-5.5"
    assert [o["value"] for o in d["options"]] == ["gpt-5.5", "gpt-5.4-mini"]
    assert [o["label"] for o in d["options"]] == ["GPT-5.5", "GPT-5.4 Mini"]
    assert all(o["disabled"] is False for o in d["options"])
