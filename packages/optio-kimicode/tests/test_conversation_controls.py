"""Session-controls surface for optio-kimicode (Task 3 of the session-controls
migration).

Covers the three kimi-specific pieces the migration adds on top of the shared
contract:

  * ``KimiCodeConversation.set_control`` routing — ``model`` -> ``session/set_model``
    (``unstable_setSessionModel``), everything else -> the generic
    ``session/set_config_option`` with params ``{sessionId, configId, value}``
    (the ``configId`` key is VERIFIED against the kimi-code fork's
    ``acp-adapter/src/server.ts`` + ``@agentclientprotocol/sdk`` 0.23.0, NOT the
    plan's guessed ``optionId``).
  * a live ``config_option_update`` notification fanned out as a synthetic
    ``x-optio-control-update`` full-snapshot event.
  * ``models.parse_all_controls`` projecting the ACP ``configOptions`` surface
    into model(select) + thinking(segmented off/on) + mode(select).

Uses the same in-process fake ACP handle as test_conversation.py for
deterministic wire assertions.
"""

import asyncio
import json

import pytest

from optio_agents.conversation import ConversationClosed
from optio_kimicode.conversation import KimiCodeConversation
from optio_kimicode.models import parse_all_controls


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


async def _bootstrap(c, handle, session_id="s1", config_options=None):
    boot = asyncio.create_task(c.bootstrap())
    req1 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req1["method"] == "initialize"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req1["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    req2 = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert req2["method"] == "session/new"
    handle.stdout.feed({"jsonrpc": "2.0", "id": req2["id"],
                        "result": {"sessionId": session_id,
                                   "configOptions": config_options or []}})
    await asyncio.wait_for(boot, 1)


@pytest.fixture
def convo():
    handle = _FakeHandle()
    c = KimiCodeConversation(cwd="/w", permission_gate=True)
    c.attach(handle)
    return c, handle


# --- set_control routing ----------------------------------------------------


@pytest.mark.asyncio
async def test_set_control_model_sends_set_model(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    task = asyncio.create_task(c.set_control("model", "kimi-k2-thinking"))
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert msg["method"] == "session/set_model"
    assert msg["params"] == {"sessionId": "s1", "modelId": "kimi-k2-thinking"}
    assert c.current_model_id == "kimi-k2-thinking"  # optimistic
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
    await asyncio.wait_for(task, 1)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_thinking_sends_set_config_option(convo):
    # Non-model controls route through the generic session/set_config_option;
    # the option key is `configId` (verified), value is the raw string.
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    task = asyncio.create_task(c.set_control("thinking", "on"))
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert msg["method"] == "session/set_config_option"
    assert msg["params"] == {"sessionId": "s1", "configId": "thinking", "value": "on"}
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
    await asyncio.wait_for(task, 1)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_set_control_mode_sends_set_config_option(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    task = asyncio.create_task(c.set_control("mode", "yolo"))
    msg = await asyncio.wait_for(handle.stdin.lines.get(), 1)
    assert msg["method"] == "session/set_config_option"
    assert msg["params"] == {"sessionId": "s1", "configId": "mode", "value": "yolo"}
    handle.stdout.feed({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
    await asyncio.wait_for(task, 1)
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
        await c.set_control("model", "kimi-k2-thinking")


# --- config_option_update -> synthetic control snapshot ---------------------


@pytest.mark.asyncio
async def test_config_option_update_emits_control_snapshot(convo):
    c, handle = convo
    reader = asyncio.create_task(c.run_reader())
    await _bootstrap(c, handle)
    events: list = []
    c.on_event(events.append)

    handle.stdout.feed({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": "s1", "update": {
            "sessionUpdate": "config_option_update",
            "configOptions": [
                {"type": "select", "id": "model", "name": "Model", "category": "model",
                 "currentValue": "kimi-k2-thinking",
                 "options": [{"value": "kimi-k2", "name": "Kimi K2"},
                             {"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"}]},
                {"type": "select", "id": "thinking", "name": "Thinking",
                 "category": "thought_level", "currentValue": "on",
                 "options": [{"value": "off", "name": "Thinking Off"},
                             {"value": "on", "name": "Thinking On"}]},
            ],
        },
    }})

    async def _wait_for_synthetic():
        while True:
            for ev in events:
                if isinstance(ev, dict) and ev.get("type") == "x-optio-control-update":
                    return ev
            await asyncio.sleep(0.01)

    synthetic = await asyncio.wait_for(_wait_for_synthetic(), 2)
    controls = synthetic["controls"]
    assert [c["id"] for c in controls] == ["model", "thinking"]
    assert controls[0]["value"] == "kimi-k2-thinking"
    assert controls[1]["kind"] == "segmented" and controls[1]["value"] == "on"
    # current_model_id tracks the live snapshot.
    assert c.current_model_id == "kimi-k2-thinking"
    handle.stdout.eof()
    await reader


# --- parse_all_controls projection ------------------------------------------


def test_parse_all_controls_model_thinking_mode():
    config_options = [
        {"type": "select", "id": "model", "name": "Model", "category": "model",
         "currentValue": "kimi-k2",
         "options": [{"value": "kimi-k2", "name": "Kimi K2"},
                     {"value": "kimi-k2-thinking", "name": "Kimi K2 Thinking"}]},
        {"type": "select", "id": "thinking", "name": "Thinking",
         "category": "thought_level", "currentValue": "off",
         "options": [{"value": "off", "name": "Thinking Off"},
                     {"value": "on", "name": "Thinking On"}]},
        {"type": "select", "id": "mode", "name": "Mode", "category": "mode",
         "currentValue": "default",
         "options": [{"value": "default", "name": "Default"},
                     {"value": "yolo", "name": "Yolo"}]},
    ]
    controls = parse_all_controls(config_options)
    kinds = {c.id: c.kind for c in controls}
    assert kinds == {"model": "select", "thinking": "segmented", "mode": "select"}

    by_id = {c.id: c for c in controls}
    assert by_id["model"].category == "model" and by_id["model"].value == "kimi-k2"
    assert by_id["thinking"].levels == ["off", "on"]
    assert by_id["thinking"].category == "thought_level" and by_id["thinking"].value == "off"
    assert by_id["mode"].value == "default"
    # to_dict is camelCase-serializable for widgetData.
    d = by_id["model"].to_dict()
    assert d["options"][1] == {"value": "kimi-k2-thinking", "label": "Kimi K2 Thinking", "disabled": False}


def test_parse_all_controls_default_model_override():
    config_options = [
        {"type": "select", "id": "model", "currentValue": "kimi-k2",
         "options": [{"value": "kimi-k2", "name": "Kimi K2"}]},
    ]
    controls = parse_all_controls(config_options, default_model="kimi-k2-thinking")
    assert controls[0].value == "kimi-k2-thinking"


def test_parse_all_controls_empty_and_malformed():
    assert parse_all_controls(None) == []
    assert parse_all_controls([]) == []
    assert parse_all_controls(["bogus", 3, None]) == []
