"""Per-task conversation listener — the opt-in dashboard gate.

Exposes one running ClaudeCodeConversation over HTTP, reached through the
optio-api widget proxy (which injects the basic-auth credential):

  GET  /events     — SSE: replay buffer first, then live tail (live includes
                     partial-message events; the buffer never does). SSE id:
                     is a monotonic seq; Last-Event-ID resumes without dupes.
                     After a resume the restored history is followed by one
                     {"type": "x-optio-resumed"} marker, carrying
                     "requeued": [ids] when the run re-sends messages the
                     previous run left undelivered (Fix 19).
  POST /send       — {text}  -> steering.send_when_ready; {ok, id, queued}
  POST /steer      — {text, upTo?} -> steering.interrupt_and_send; {ok, id}
                     (empty text: Send now, deliver what is queued; id is
                     null. upTo: the queued id Send now was clicked on; it
                     needs nothing beyond the interrupt since Fix 17, as
                     queued messages reach Claude one at a time, in order)
  POST /interrupt  — {}      -> steering.interrupt (stop only)
  POST /control    — {id, value}                  -> conversation.set_control
  GET  /download   — ?path=<relpath>              -> download_reader; returns
                     the bytes with Content-Disposition: attachment.
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
import time
from collections import deque
from typing import Awaitable, Callable

from aiohttp import web

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_agents.steering import INTERRUPT_EVENT, Steering

from optio_claudecode.steering import make_steering, undelivered_queued

_LOG = logging.getLogger(__name__)

BUFFER_MAXLEN = 1000
# Synthetic marker appended after a re-primed (resumed) history: the prior
# run's process is gone, so anything it left running will never report.
RESUMED_EVENT_TYPE = "x-optio-resumed"
# Synthetic marker this listener stamps for every streamed message (Fix 12,
# owner ruling 2026-09-14): stream_event frames carry no time and are never
# buffered (see UNBUFFERED_TYPES below), so a streamed message's true start
# would otherwise be lost to replay. Emitted once per message_start, BEFORE
# that stream_event is forwarded; buffered and persisted like a native event,
# so the exact same value comes back on replay.
MESSAGE_START_EVENT_TYPE = "x-optio-message-start"
UNBUFFERED_TYPES = {"stream_event"}
# Fix 19 (owner rulings 2026-09-15, finding 6; see
# docs/2026-09-15-steering-session-end-design.md): the text a message had
# streamed when the session ended, if its final assistant event never came:
# {"type": "x-optio-partial", "id": <message id>, "text": <text so far>}.
# stream_event deltas are never buffered, so without it a reload or a resume
# would lose that text.
PARTIAL_EVENT_TYPE = "x-optio-partial"
# The session ended while a turn ran: the UI's "⏹ Interrupted: session ended".
SESSION_END_MARKER = {"type": INTERRUPT_EVENT, "by": "session"}


def _wall_clock_ms() -> float:
    return time.time() * 1000
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
        download_reader: "Callable[[str], Awaitable[tuple[bytes, str]]] | None" = None,
        max_download_bytes: int = 10_000_000,
        steering: "Steering | None" = None,
        clock: "Callable[[], float]" = _wall_clock_ms,
    ) -> None:
        self._conversation = conversation
        self._password = password
        self._download_reader = download_reader
        self._max_download_bytes = max_download_bytes
        # Injectable so x-optio-message-start's ts is deterministic in tests
        # (see MESSAGE_START_EVENT_TYPE); the real clock is a server-side wall
        # clock read at receipt, per the owner ruling.
        self._clock = clock
        self._buffer: deque[tuple[int, dict]] = deque(maxlen=BUFFER_MAXLEN)
        # Re-prime the replay buffer from a previous run (resume) so a viewer
        # attaching after a resume still sees the prior conversation history.
        # seq continues monotonically from the highest restored value.
        self._seq = 0
        # Fix 19: what the previous run left queued and undelivered, as
        # (old id, text); requeue_undelivered() re-sends it once.
        self._undelivered: list[tuple[str, str]] = []
        if initial_events:
            for seq, event in initial_events:
                self._buffer.append((seq, event))
            self._seq = max(seq for seq, _ in initial_events)
            self._undelivered = undelivered_queued(initial_events)
            # Mark where the prior run's history ends, so the widget stops the
            # rows that run left running (a background task, a call with no
            # result). Buffered and persisted like any event, so a later
            # resume replays it at this point. It lists the ids this run
            # re-queues, so the widget keeps those bubbles queued.
            resumed: dict = {"type": RESUMED_EVENT_TYPE}
            if self._undelivered:
                resumed["requeued"] = [qid for qid, _ in self._undelivered]
            self._seq += 1
            self._buffer.append((self._seq, resumed))
        # Fix 19, session end. Whether a turn runs (session_state_changed
        # running or a message_start, until its result or idle); the message
        # being streamed and the text of its open block (reset at its
        # message_start and at each of its final assistant events); whether
        # the session-end marker has passed; whether announce_session_end()
        # already emitted it.
        self._turn_open = False
        self._partial_id: str | None = None
        self._partial_text = ""
        self._session_end_marked = False
        self._session_end_announced = False
        self._subscribers: set[asyncio.Queue] = set()
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._runner: web.AppRunner | None = None
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)
        # Send when ready / Interrupt and send / Interrupt. Its synthetic
        # events come back through conversation.emit_event -> _on_event, so
        # they are buffered and persisted like native events.
        self._steering = steering if steering is not None else make_steering(conversation)

    def export_buffer(self) -> "list[list]":
        """Serializable snapshot of the replay buffer ([[seq, event], …]) for
        persistence across a resume.

        Excludes the terminal ``x-optio-closed`` marker: it records the END of
        this run, not conversation content. Persisting it would replay on resume
        and make the UI treat the live resumed session as already closed
        (disabling the input). The ``x-optio-resumed`` marker is kept: it
        records where an earlier run ended inside the history."""
        return [
            [seq, event] for seq, event in self._buffer
            if event.get("type") != "x-optio-closed"
        ]

    async def requeue_undelivered(self) -> list[str]:
        """Resume (Fix 19, owner ruling 2026-09-15, finding 6 #2): re-send,
        in their original order, the messages the previous run left queued
        and never delivered (``undelivered_queued`` over the restored
        buffer), each under a NEW id through Steering's one-at-a-time path,
        announced by one x-optio-requeued {id, new_id} each. Nothing the CLI
        already started is re-sent. The session calls it once, right after
        its "you have been resumed" notice, so the resumed turn goes first.
        Returns the OLD ids; a second call re-sends nothing."""
        pending, self._undelivered = self._undelivered, []
        if not pending:
            return []
        try:
            await self._steering.requeue(pending)
        except ConversationClosed:
            _LOG.warning(
                "conversation closed before %d undelivered message(s) could be re-queued",
                len(pending),
            )
            return []
        return [qid for qid, _ in pending]

    async def reset_transport(self) -> None:
        """Fix 25 (final-review-2 I3 / ledger line 399): the session calls
        this right after re-attaching a fresh ``claude`` process for a
        model/effort relaunch — reaching this ``Steering`` through the
        listener, the same way ``requeue_undelivered`` does for a resume,
        rather than a new global. The dead process took whatever message
        was in flight with it, so without this ``Steering._in_flight``
        would stay set forever and the queue behind it would never drain —
        silent, permanent message loss. Delegates to
        ``Steering.reset_transport``, which re-writes that message (to the
        transport just re-attached) instead of dropping it."""
        try:
            await self._steering.reset_transport()
        except ConversationClosed:
            _LOG.warning(
                "conversation closed before the steering queue could be reset "
                "across a relaunch",
            )

    # -- event intake --------------------------------------------------------

    def _broadcast(self, event: dict) -> None:
        self._seq += 1
        item = (self._seq, event)
        if event.get("type") not in UNBUFFERED_TYPES:
            self._buffer.append(item)
        for q in list(self._subscribers):
            q.put_nowait(item)

    def _on_event(self, event: dict) -> None:
        if event.get("type") == "x-optio-closed":
            self._before_closed()
        elif self._is_message_start(event):
            message = event["event"].get("message")
            msg_id = message.get("id") if isinstance(message, dict) else None
            if isinstance(msg_id, str):
                # Stamped and broadcast BEFORE the stream_event it announces,
                # so a subscriber (or the replay buffer) always sees the
                # start marker first — see MESSAGE_START_EVENT_TYPE.
                self._broadcast({
                    "type": MESSAGE_START_EVENT_TYPE,
                    "id": msg_id,
                    "ts": self._clock(),
                })
        self._track(event)
        self._broadcast(event)

    # -- session end (Fix 19) ------------------------------------------------

    def _turn_running(self) -> bool:
        return self._turn_open or self._conversation.is_pending()

    def _track(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "session_state_changed":
            if event.get("state") == "running":
                self._turn_open = True
            elif event.get("state") == "idle":
                self._turn_open = False
                self._partial_id, self._partial_text = None, ""
        elif kind == "result":
            self._turn_open = False
            self._partial_id, self._partial_text = None, ""
        elif kind == "assistant":
            # Each assistant event carries one completed block: that block's
            # text is buffered now, so nothing of it is partial any more.
            self._partial_text = ""
        elif kind == INTERRUPT_EVENT and event.get("by") == "session":
            self._session_end_marked = True
        elif kind == "stream_event":
            inner = event.get("event")
            if not isinstance(inner, dict):
                return
            if inner.get("type") == "message_start":
                message = inner.get("message")
                msg_id = message.get("id") if isinstance(message, dict) else None
                self._turn_open = True
                self._partial_id = msg_id if isinstance(msg_id, str) else None
                self._partial_text = ""
            elif inner.get("type") == "content_block_delta" and self._partial_id is not None:
                delta = inner.get("delta")
                if not isinstance(delta, dict):
                    return
                # A text-bearing thinking block is narration the UI renders
                # in the bubble too; hidden thinking streams "" and adds
                # nothing. tool_use input_json deltas are not text.
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text")
                elif delta.get("type") == "thinking_delta":
                    chunk = delta.get("thinking")
                else:
                    chunk = None
                if isinstance(chunk, str):
                    self._partial_text += chunk

    def _before_closed(self) -> None:
        """Runs synchronously inside ClaudeCodeConversation._finish's drain,
        before x-optio-closed is broadcast, so what it adds is buffered
        before session.py's export_buffer(). The partial first (only if the
        open block's final assistant event was not seen), then the marker
        (only if announce_session_end() did not already send it and a turn
        runs)."""
        marked = self._session_end_marked
        if not marked and not self._turn_running():
            return
        if self._partial_id is not None and self._partial_text:
            self._broadcast({
                "type": PARTIAL_EVENT_TYPE, "id": self._partial_id, "text": self._partial_text,
            })
        if not marked:
            self._session_end_marked = True
            self._broadcast(dict(SESSION_END_MARKER))

    def announce_session_end(self) -> None:
        """Fix 19, the graceful path (session.py ``_end_turn_gracefully``):
        the session is about to interrupt the running turn. The marker goes
        out now, through the conversation's own event stream, so it follows
        every event already read and precedes the CLI's own interrupt
        artefacts (its final partial assistant event, "[Request interrupted
        by user]", the aborted result), which the UI then renders as this
        interruption. At most once."""
        if self._session_end_announced:
            return
        self._session_end_announced = True
        self._conversation.emit_event(dict(SESSION_END_MARKER))

    @staticmethod
    def _is_message_start(event: dict) -> bool:
        inner = event.get("event")
        return (
            event.get("type") == "stream_event"
            and isinstance(inner, dict)
            and inner.get("type") == "message_start"
        )

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
            outcome = await self._steering.send_when_ready(text)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True, "id": outcome.id, "queued": outcome.queued})

    async def _handle_steer(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        text = payload.get("text", "")
        if not isinstance(text, str):
            return web.json_response({"ok": False, "reason": "bad-text"}, status=400)
        up_to = payload.get("upTo")
        if up_to is not None and not isinstance(up_to, str):
            return web.json_response({"ok": False, "reason": "bad-upTo"}, status=400)
        try:
            qid = await self._steering.interrupt_and_send(text, up_to=up_to)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True, "id": qid})

    async def _handle_interrupt(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            await self._steering.interrupt()
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})

    async def _handle_control(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "reason": "bad-json"}, status=400)
        cid = payload.get("id")
        if not isinstance(cid, str) or not cid:
            return web.json_response({"ok": False, "reason": "bad-id"}, status=400)
        value = payload.get("value")
        try:
            await self._conversation.set_control(cid, value)
        except ConversationClosed:
            return web.json_response({"ok": False, "reason": "closed"}, status=409)
        return web.json_response({"ok": True})

    async def _handle_download(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"ok": False}, status=401)
        if self._download_reader is None:
            return web.json_response({"ok": False, "reason": "no-reader"}, status=409)
        path = request.query.get("path")
        if not path:
            return web.json_response({"ok": False, "reason": "bad-path"}, status=400)
        try:
            data, mime = await self._download_reader(path)
        except FileNotFoundError:
            return web.json_response({"ok": False, "reason": "not-found"}, status=404)
        except ValueError as e:
            reason = str(e)
            status = 413 if reason == "too-large" else 403
            return web.json_response({"ok": False, "reason": reason}, status=status)
        base = path.split("/")[-1] or "file"
        return web.Response(
            body=data,
            headers={
                "Content-Type": mime,
                "Content-Disposition": f'attachment; filename="{base}"',
            },
        )

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
        app.router.add_post("/steer", self._handle_steer)
        app.router.add_post("/interrupt", self._handle_interrupt)
        app.router.add_post("/control", self._handle_control)
        app.router.add_get("/download", self._handle_download)
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
        self._steering.close()
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
