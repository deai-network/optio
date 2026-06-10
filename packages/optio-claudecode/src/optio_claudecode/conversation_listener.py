"""Per-task conversation listener — the opt-in dashboard gate.

Exposes one running ClaudeCodeConversation over HTTP, reached through the
optio-api widget proxy (which injects the basic-auth credential):

  GET  /events     — SSE: replay buffer first, then live tail (live includes
                     partial-message events; the buffer never does). SSE id:
                     is a monotonic seq; Last-Event-ID resumes without dupes.
  POST /send       — {text}                      -> conversation.send
  POST /interrupt  — {}                          -> conversation.interrupt
  POST /permission — {request_id, behavior, updated_input?, message?}
                     resolves the pending can_use_tool future.

Projection principle: this listener only observes and forwards; attaching or
detaching viewers never influences task state. See the Phase II design doc.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque

from aiohttp import web

from optio_agents.conversation import ConversationClosed, PermissionDecision

_LOG = logging.getLogger(__name__)

BUFFER_MAXLEN = 1000
UNBUFFERED_TYPES = {"stream_event"}
PING_INTERVAL_S = 15.0
# Bound aiohttp's graceful-shutdown wait. The /events SSE handler is a
# long-lived loop; without this, runner.cleanup() would block on it for the
# 60s default, stalling the session's cooperative-cancel teardown past its
# grace period (→ forced "failed", snapshot never captured, not resumable).
SHUTDOWN_TIMEOUT_S = 2.0
# Sentinel pushed into each subscriber queue on stop() so the SSE handler
# loop returns immediately instead of parking until the next ping timeout.
_STOP = object()


class ConversationListener:
    def __init__(
        self, conversation, *, password: str,
        initial_events: "list[tuple[int, dict]] | None" = None,
    ) -> None:
        self._conversation = conversation
        self._password = password
        self._buffer: deque[tuple[int, dict]] = deque(maxlen=BUFFER_MAXLEN)
        # Re-prime the replay buffer from a previous run (resume) so a viewer
        # attaching after a resume still sees the prior conversation history.
        # seq continues monotonically from the highest restored value.
        self._seq = 0
        if initial_events:
            for seq, event in initial_events:
                self._buffer.append((seq, event))
            self._seq = max(seq for seq, _ in initial_events)
        self._subscribers: set[asyncio.Queue] = set()
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._runner: web.AppRunner | None = None
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)

    def export_buffer(self) -> "list[list]":
        """Serializable snapshot of the replay buffer ([[seq, event], …]) for
        persistence across a resume.

        Excludes the terminal ``x-optio-closed`` marker: it records the END of
        this run, not conversation content. Persisting it would replay on resume
        and make the UI treat the live resumed session as already closed
        (disabling the input)."""
        return [
            [seq, event] for seq, event in self._buffer
            if event.get("type") != "x-optio-closed"
        ]

    # -- event intake --------------------------------------------------------

    def _broadcast(self, event: dict) -> None:
        self._seq += 1
        item = (self._seq, event)
        if event.get("type") not in UNBUFFERED_TYPES:
            self._buffer.append(item)
        for q in list(self._subscribers):
            q.put_nowait(item)

    def _on_event(self, event: dict) -> None:
        self._broadcast(event)

    # -- permission gate -------------------------------------------------------

    async def _on_permission_request(self, request) -> PermissionDecision:
        # The raw control_request already reached viewers via _on_event; we
        # only park until some operator POSTs /permission with its request_id.
        request_id = str(request.raw.get("request_id"))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_permissions[request_id] = fut
        try:
            decision: PermissionDecision = await fut
        finally:
            self._pending_permissions.pop(request_id, None)
        self._broadcast({
            "type": "x-optio-permission-answered",
            "request_id": request_id,
            "behavior": decision.behavior,
        })
        return decision

    # -- HTTP handlers ---------------------------------------------------------

    def _authorized(self, request: web.Request) -> bool:
        # The widget proxy injects BasicAuth(username="optio", password=...).
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            userpass = base64.b64decode(auth[6:]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        return userpass == f"optio:{self._password}"

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await resp.prepare(request)

        async def send_item(seq: int, event: dict) -> None:
            payload = json.dumps(event)
            await resp.write(f"id: {seq}\ndata: {payload}\n\n".encode("utf-8"))

        last_id = 0
        raw_last = request.headers.get("Last-Event-ID", "")
        if raw_last.isdigit():
            last_id = int(raw_last)

        queue: asyncio.Queue = asyncio.Queue()
        # Subscribe BEFORE replay so no event falls between replay and tail;
        # the seq check below dedupes any overlap.
        self._subscribers.add(queue)
        try:
            sent_through = last_id
            for seq, event in list(self._buffer):
                if seq > sent_through:
                    await send_item(seq, event)
                    sent_through = seq
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=PING_INTERVAL_S,
                    )
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
                if item is _STOP:
                    break  # stop() asked us to close so teardown can proceed
                seq, event = item
                if seq > sent_through:
                    await send_item(seq, event)
                    sent_through = seq
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._subscribers.discard(queue)
        return resp

    async def _handle_send(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return web.json_response({"ok": False, "reason": "bad-text"}, status=400)
        try:
            await self._conversation.send(text)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})

    async def _handle_interrupt(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            await self._conversation.interrupt()
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})

    async def _handle_permission(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        request_id = str(payload.get("request_id", ""))
        behavior = payload.get("behavior")
        if behavior not in ("allow", "deny"):
            return web.json_response({"ok": False, "reason": "bad-behavior"}, status=400)
        fut = self._pending_permissions.get(request_id)
        if fut is None or fut.done():
            return web.json_response({"ok": False, "reason": "unknown-request"}, status=404)
        fut.set_result(PermissionDecision(
            behavior=behavior,
            updated_input=payload.get("updated_input"),
            message=payload.get("message"),
        ))
        return web.json_response({"ok": True})

    # -- lifecycle ---------------------------------------------------------------

    async def start(self, bind_iface: str) -> int:
        app = web.Application()
        app.router.add_get("/events", self._handle_events)
        app.router.add_post("/send", self._handle_send)
        app.router.add_post("/interrupt", self._handle_interrupt)
        app.router.add_post("/permission", self._handle_permission)
        self._runner = web.AppRunner(app, shutdown_timeout=SHUTDOWN_TIMEOUT_S)
        await self._runner.setup()
        site = web.TCPSite(self._runner, bind_iface, 0)
        await site.start()
        # Read the OS-assigned port back from the bound server socket.
        server = site._server  # aiohttp exposes the asyncio.Server here
        return server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        # Idempotent: teardown paths may call stop() more than once. Make the
        # unsubscribe one-shot so a second call can't double-remove the handler.
        unsubscribe = self._unsubscribe
        self._unsubscribe = lambda: None
        unsubscribe()
        for fut in self._pending_permissions.values():
            if not fut.done():
                fut.set_result(PermissionDecision(
                    behavior="deny", message="optio harness: session ending",
                ))
        # Wake every open /events handler so it returns now, instead of
        # letting runner.cleanup() wait for the long-lived SSE loops.
        for queue in list(self._subscribers):
            queue.put_nowait(_STOP)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
