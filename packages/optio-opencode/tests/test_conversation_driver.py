"""OpencodeConversation unit tests against an in-process fake opencode server."""

import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp import web

from optio_agents.conversation import (
    Conversation,
    ConversationClosed,
    PermissionDecision,
)
from optio_opencode.conversation import OpencodeConversation

SID = "ses_test"


class FakeServer:
    """Minimal opencode-server fake: scripted SSE events + request journal."""

    def __init__(self):
        self.journal: list[tuple[str, dict]] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self.pending_permissions: list[dict] = []
        self.app = web.Application()
        self.app.router.add_get("/event", self._event)
        self.app.router.add_post("/session/{sid}/prompt_async", self._prompt)
        self.app.router.add_post("/session/{sid}/abort", self._abort)
        self.app.router.add_get("/permission", self._perm_list)
        self.app.router.add_post("/permission/{rid}/reply", self._perm_reply)
        self.runner: web.AppRunner | None = None
        self.port: int | None = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        # Short shutdown_timeout: the SSE handler blocks on events.get() and
        # never notices client disconnect, so cleanup() would otherwise wait
        # aiohttp's default 60s per test before cancelling it.
        site = web.TCPSite(self.runner, "127.0.0.1", 0, shutdown_timeout=0.1)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()

    def emit(self, type_: str, properties: dict):
        self.events.put_nowait({"id": f"evt_{type_}", "type": type_, "properties": properties})

    async def _event(self, request):
        resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n')
        try:
            while True:
                ev = await self.events.get()
                await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError):
            return resp

    async def _prompt(self, request):
        self.journal.append(("prompt", await request.json()))
        return web.Response(status=204)

    async def _abort(self, request):
        self.journal.append(("abort", {}))
        return web.json_response(True)

    async def _perm_list(self, request):
        return web.json_response(self.pending_permissions)

    async def _perm_reply(self, request):
        self.journal.append(
            ("perm_reply", {"rid": request.match_info["rid"], **(await request.json())})
        )
        return web.json_response(True)


@pytest_asyncio.fixture
async def server():
    s = FakeServer()
    await s.start()
    yield s
    await s.stop()


@pytest_asyncio.fixture
async def conv(server):
    c = OpencodeConversation(
        port=server.port, password="pw", session_id=SID, directory="/work",
    )
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.05)  # let the SSE connect
    yield c
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass


def test_implements_protocol():
    c = OpencodeConversation(port=1, password="x", session_id=SID, directory="/w")
    assert isinstance(c, Conversation)


async def test_send_posts_prompt_async(conv, server):
    await conv.send("hello")
    await asyncio.sleep(0.05)
    assert ("prompt", {"parts": [{"type": "text", "text": "hello"}]}) in server.journal


async def test_on_event_is_raw_passthrough(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    server.emit("message.part.delta",
                {"sessionID": SID, "messageID": "m1", "partID": "p1", "delta": "He"})
    await asyncio.sleep(0.05)
    assert any(e.get("type") == "message.part.delta" for e in seen)
    raw = next(e for e in seen if e.get("type") == "message.part.delta")
    assert raw["properties"]["delta"] == "He"  # unmodified native payload


async def test_on_message_fires_on_completed_assistant_message(conv, server):
    msgs: list[str] = []
    conv.on_message(msgs.append)
    server.emit("message.part.updated",
                {"part": {"id": "p1", "messageID": "m1", "sessionID": SID,
                          "type": "text", "text": "final answer"}})
    server.emit("message.updated",
                {"info": {"id": "m1", "sessionID": SID, "role": "assistant",
                          "time": {"created": 1, "completed": 2}}})
    await asyncio.sleep(0.05)
    assert msgs == ["final answer"]


async def test_busy_tracking_via_session_status(conv, server):
    assert conv.is_pending() is False
    await conv.send("q")
    assert conv.is_pending() is True
    server.emit("session.status", {"sessionID": SID, "status": {"type": "idle"}})
    await asyncio.sleep(0.05)
    assert conv.is_pending() is False


async def test_interrupt_posts_abort(conv, server):
    await conv.send("q")
    await conv.interrupt()
    await asyncio.sleep(0.05)
    assert ("abort", {}) in server.journal


async def test_permission_flow_allow_maps_to_once(conv, server):
    async def handler(req):
        assert req.tool_name == "bash"
        assert req.raw["id"] == "per_1"
        return PermissionDecision(behavior="allow")

    conv.on_permission_request(handler)
    server.emit("permission.asked",
                {"id": "per_1", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_1", "reply": "once"}) in server.journal


async def test_permission_deny_maps_to_reject_with_message(conv, server):
    async def handler(req):
        return PermissionDecision(behavior="deny", message="nope")

    conv.on_permission_request(handler)
    server.emit("permission.asked",
                {"id": "per_2", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_2", "reply": "reject", "message": "nope"}) in server.journal


async def test_permission_asked_before_handler_is_queued(conv, server):
    server.emit("permission.asked",
                {"id": "per_3", "sessionID": SID, "permission": "bash",
                 "patterns": [], "metadata": {}, "always": []})
    await asyncio.sleep(0.05)

    async def handler(req):
        return PermissionDecision(behavior="allow")

    conv.on_permission_request(handler)
    await asyncio.sleep(0.1)
    assert ("perm_reply", {"rid": "per_3", "reply": "once"}) in server.journal


async def test_pending_permissions_swept_on_connect(server):
    server.pending_permissions = [
        {"id": "per_old", "sessionID": SID, "permission": "bash",
         "patterns": [], "metadata": {}, "always": []},
    ]
    c = OpencodeConversation(
        port=server.port, password="pw", session_id=SID, directory="/work",
    )

    async def handler(req):
        return PermissionDecision(behavior="allow")

    c.on_permission_request(handler)
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.15)
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass
    assert ("perm_reply", {"rid": "per_old", "reply": "once"}) in server.journal


async def test_close_sets_close_requested(conv):
    await conv.close()
    assert conv.close_requested.is_set()
    assert conv.closed is False  # close() requests; _finish() concludes


async def test_finish_emits_x_optio_closed_and_send_raises(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    await conv._finish("test over")  # what the session body's finally does
    assert conv.closed is True
    assert {"type": "x-optio-closed", "reason": "test over"} in seen
    with pytest.raises(ConversationClosed):
        await conv.send("more")


# --- fake_opencode.py subprocess parity --------------------------------

import os
import subprocess
import sys

FAKE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def fake_proc(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, FAKE, "--port", "0", "--scenario", "conversation"],
        stdout=subprocess.PIPE, cwd=tmp_path, text=True,
    )
    line = proc.stdout.readline()  # "Listening on http://127.0.0.1:<port>/"
    port = int(line.rsplit(":", 1)[1].strip().rstrip("/"))
    yield port, tmp_path
    proc.terminate()
    proc.wait(timeout=5)


async def test_driver_against_fake_opencode_subprocess(fake_proc):
    port, workdir = fake_proc
    c = OpencodeConversation(
        port=port, password="pw", session_id="fake-session-id", directory=str(workdir),
    )
    seen: list[dict] = []
    c.on_event(seen.append)
    reader = asyncio.create_task(c.run_reader())
    await asyncio.sleep(0.3)  # scenario emits its scripted events
    await c.send("hi there")
    await c.interrupt()
    await asyncio.sleep(0.2)
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass
    # Scenario's scripted event arrived over SSE:
    assert any(e.get("type") == "message.part.delta" for e in seen)
    # The fake journaled our POSTs:
    journal = (workdir / "conv_journal.jsonl").read_text().splitlines()
    kinds = [json.loads(l)["kind"] for l in journal]
    assert "prompt_async" in kinds and "abort" in kinds
