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
from optio_opencode import model_probe, session as sess

SID = "ses_test"


class FakeServer:
    """Minimal opencode-server fake: scripted SSE events + request journal."""

    def __init__(self):
        self.journal: list[tuple[str, dict]] = []
        self.events: asyncio.Queue = asyncio.Queue()
        self.pending_permissions: list[dict] = []
        # Opt-in probe scripting: when answer_probes is set, each prompt drives a
        # turn — models in `errored` end in a session.error, all others answer.
        self.answer_probes = False
        self.errored: set[str] = set()
        # Force /global/event to fail with this status (so the reader never
        # connects and never marks the conversation ready).
        self.event_status = 200
        self._turn = 0
        self.app = web.Application()
        self.app.router.add_get("/global/event", self._event)
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

    def emit(self, type_: str, properties: dict, directory: str = "/work"):
        # /global/event frame shape (Task 8 fixtures): instance events are
        # wrapped {"directory", "project", "payload": {id, type, properties}}.
        self.events.put_nowait({
            "directory": directory,
            "project": "global",
            "payload": {"id": f"evt_{type_}", "type": type_, "properties": properties},
        })

    async def _event(self, request):
        if self.event_status != 200:
            return web.Response(status=self.event_status)
        resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"payload":{"id":"evt_0","type":"server.connected","properties":{}}}\n\n')
        try:
            while True:
                ev = await self.events.get()
                await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError):
            return resp

    async def _prompt(self, request):
        body = await request.json()
        self.journal.append(("prompt", body))
        if self.answer_probes:
            model = body.get("model") or {}
            mid = f"{model.get('providerID')}/{model.get('modelID')}" if model else None
            self._answer_turn(mid)
        return web.Response(status=204)

    def _answer_turn(self, mid):
        """Emit one scripted turn over SSE: a session.error for a model in
        `errored`, otherwise a completed assistant answer."""
        self._turn += 1
        m = f"m{self._turn}"
        if mid in self.errored:
            self.emit("session.error",
                      {"sessionID": SID, "error": {"message": "not supported"}})
            return
        self.emit("message.part.updated",
                  {"part": {"id": f"p{self._turn}", "messageID": m, "sessionID": SID,
                            "type": "text", "text": "Budapest is the capital."}})
        self.emit("message.updated",
                  {"info": {"id": m, "sessionID": SID, "role": "assistant",
                            "time": {"created": 1, "completed": 2}}})

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


async def test_set_active_model_attaches_model_to_prompt(conv, server):
    # The model probe drives a throwaway conversation and needs each turn to run
    # under a specific model. opencode attaches the model inline on prompt_async
    # ({providerID, modelID}); set_active_model records the "providerID/modelID"
    # string and send() splits it back out.
    await conv.set_active_model("xai/grok-5")
    assert conv.current_model_id == "xai/grok-5"
    await conv.send("hi")
    await asyncio.sleep(0.05)
    assert (
        "prompt",
        {"parts": [{"type": "text", "text": "hi"}],
         "model": {"providerID": "xai", "modelID": "grok-5"}},
    ) in server.journal


async def test_send_without_model_omits_model_field(conv, server):
    # Default (no model set) must keep the historical body shape byte-for-byte.
    await conv.send("plain")
    await asyncio.sleep(0.05)
    assert ("prompt", {"parts": [{"type": "text", "text": "plain"}]}) in server.journal


async def test_on_event_is_raw_passthrough(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    server.emit("message.part.delta",
                {"sessionID": SID, "messageID": "m1", "partID": "p1", "delta": "He"})
    await asyncio.sleep(0.05)
    assert any(e.get("type") == "message.part.delta" for e in seen)
    raw = next(e for e in seen if e.get("type") == "message.part.delta")
    assert raw["properties"]["delta"] == "He"  # unmodified native payload


async def test_other_directory_frames_are_dropped(conv, server):
    seen: list[dict] = []
    conv.on_event(seen.append)
    server.emit("message.part.delta",
                {"sessionID": SID, "messageID": "m1", "partID": "p1",
                 "field": "text", "delta": "not ours"},
                directory="/elsewhere")
    await asyncio.sleep(0.05)
    assert not any(e.get("type") == "message.part.delta" for e in seen)


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


# --- startup-race guard: probe must wait for the reader to be connected -------


async def test_send_before_reader_raises_conversation_closed():
    # A freshly-constructed conversation has no aiohttp session yet (run_reader
    # creates it). Sending before the reader started must fail loudly with
    # ConversationClosed — NOT an opaque AttributeError on `self._http is None`,
    # which the probe's per-model except would silently swallow as "unusable".
    c = OpencodeConversation(port=1, password="x", session_id=SID, directory="/w")
    with pytest.raises(ConversationClosed):
        await c.send("hi")


async def test_probe_flow_gates_on_ready_and_returns_mix(server):
    # Reproduce the _run_model_probe timing (start the reader, THEN probe), but
    # gate on the readiness Event first. With the gate, turns are sent only once
    # the SSE stream is live, so probe_models distinguishes a good model (answers)
    # from a bad one (session.error) — a MIX, not the all-False startup-race bug.
    server.answer_probes = True
    server.errored = {"prov/bad"}
    c = OpencodeConversation(
        port=server.port, password="pw", session_id=SID, directory="/work",
    )
    reader = asyncio.create_task(c.run_reader())
    await asyncio.wait_for(c._ready.wait(), timeout=5.0)
    try:
        usable = await model_probe.probe_models(
            c, ["prov/good", "prov/bad"], per_model_timeout=2.0,
        )
    finally:
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
    assert usable == {"prov/good": True, "prov/bad": False}


async def test_run_model_probe_returns_empty_when_reader_never_connects(
    server, monkeypatch,
):
    # If the reader never connects (here: /global/event errors), the readiness
    # gate times out and _run_model_probe returns {} (unfiltered picker) rather
    # than probing against a dead stream and marking every model unusable.
    server.event_status = 503

    async def _fake_create(port, password, directory):
        return "probe_sid"

    async def _fake_delete(port, password, session_id, directory):
        return None

    monkeypatch.setattr(sess, "_create_opencode_session", _fake_create)
    monkeypatch.setattr(sess, "_delete_opencode_session", _fake_delete)
    monkeypatch.setattr(sess, "_PROBE_READY_TIMEOUT", 0.3)

    result = await sess._run_model_probe(
        server.port, "pw", "/work", ["prov/good", "prov/bad"], report=None,
    )
    assert result == {}
