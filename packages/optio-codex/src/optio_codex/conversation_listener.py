"""Per-task conversation listener — the opt-in dashboard gate for optio-codex.

Exposes one running CodexConversation over HTTP, reached through the optio-api
widget proxy (which injects the basic-auth credential):

  GET  /events     — SSE: replay buffer first, then live tail. SSE id: is a
                     monotonic seq; Last-Event-ID resumes without dupes.
  POST /send       — {text}                 -> conversation.send
  POST /interrupt  — {}                     -> conversation.interrupt
  POST /control    — {id, value}            -> conversation.set_control
                     (model: INLINE — pins the next turn/start's model, no restart)
  GET  /download   — ?path=<relpath>        -> download_reader; returns the
                     bytes with Content-Disposition: attachment
  POST /permission — {request_id, behavior, updated_input?, message?}
                     resolves the pending requestApproval future.

Structurally mirrors optio-grok's ConversationListener (itself from
optio-claudecode's). Permissions are correlated by the JSON-RPC ``id`` of the
``item/commandExecution/requestApproval`` / ``item/fileChange/requestApproval``
server request — CodexConversation hands the whole JSON-RPC object to the
handler as ``PermissionRequest.raw``.

Projection principle: this listener only observes and forwards; attaching or
detaching viewers never influences task state.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from typing import Awaitable, Callable

from aiohttp import web

from optio_agents.conversation import ConversationClosed, PermissionDecision

from optio_codex import rollout as _rollout

_LOG = logging.getLogger(__name__)

BUFFER_MAXLEN = 1000
PING_INTERVAL_S = 15.0
# Bound aiohttp's graceful-shutdown wait so the long-lived /events SSE loop
# cannot stall the session's cooperative-cancel teardown past its grace period.
SHUTDOWN_TIMEOUT_S = 2.0
# Sentinel pushed into each subscriber queue on stop() so the SSE handler loop
# returns immediately instead of parking until the next ping timeout.
_STOP = object()


def _event_turn_id(event: dict) -> str | None:
    """The codex turn id an event belongs to, or None (synthetic/handshake
    events carry no turn). Used to dedup live-buffer events against the rollout
    history on a fresh attach — the rollout records the SAME turn ids the live
    wire uses (verified: response_item.internal_chat_message_metadata_passthrough
    .turn_id == the app-server turn id)."""
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    turn = params.get("turn")
    if isinstance(turn, dict) and turn.get("id"):
        return turn["id"]          # turn/started, turn/completed
    return params.get("turnId")    # item/*, requestApproval, deltas


class ConversationListener:
    def __init__(
        self, conversation, *, password: str,
        download_reader: "Callable[[str], Awaitable[tuple[bytes, str]]] | None" = None,
        max_download_bytes: int = 10_000_000,
        codex_home: str | None = None,
    ) -> None:
        self._conversation = conversation
        self._password = password
        self._download_reader = download_reader
        self._max_download_bytes = max_download_bytes
        # CODEX_HOME (=<workdir>/home/.codex) — the on-disk rollout store that is
        # the AUTHORITATIVE full history a fresh viewer attach replays before the
        # in-flight live tail. None ⇒ no rollout replay (buffer-only, as before).
        self._codex_home = codex_home
        self._buffer: deque[tuple[int, dict]] = deque(maxlen=BUFFER_MAXLEN)
        self._seq = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._runner: web.AppRunner | None = None
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)

    # -- event intake --------------------------------------------------------

    def _broadcast(self, event: dict) -> None:
        self._seq += 1
        item = (self._seq, event)
        self._buffer.append(item)
        for q in list(self._subscribers):
            q.put_nowait(item)

    def _on_event(self, event: dict) -> None:
        self._broadcast(event)

    # -- permission gate -----------------------------------------------------

    async def _on_permission_request(self, request) -> PermissionDecision:
        # The raw requestApproval request already reached viewers via _on_event;
        # here we only park until some operator POSTs /permission with its
        # JSON-RPC id. CodexConversation stores the whole JSON-RPC request object
        # as PermissionRequest.raw, so `id` is the correlation key.
        request_id = str(request.raw.get("id"))
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

    # -- rollout history -----------------------------------------------------

    def _load_history(self) -> "tuple[list[dict], set[str]]":
        """Reconstruct the FULL conversation from the on-disk rollout, and the
        set of turn ids it covers (the dedup key for the live buffer).

        Fail-soft by contract: any error (no codex_home, no rollout, an
        unreadable/malformed file) yields ``([], set())`` so a fresh attach
        silently falls back to today's buffer-only replay — it must NEVER raise
        into the SSE handler."""
        if not self._codex_home:
            return [], set()
        try:
            path = _rollout.resolve_latest_rollout(self._codex_home)
            if path is None:
                return [], set()
            events = _rollout.read_rollout_events(path)
        except Exception:  # noqa: BLE001 — attach must never fail on the rollout
            _LOG.exception("rollout history reconstruction failed; buffer only")
            return [], set()
        turn_ids = {t for e in events if (t := _event_turn_id(e)) is not None}
        return events, turn_ids

    # -- HTTP handlers -------------------------------------------------------

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
            if last_id == 0:
                # FRESH attach: replay the FULL history from the on-disk rollout
                # (authoritative — the bounded buffer deque has evicted early
                # turns on a long session), THEN the in-flight turn from the live
                # buffer, deduped against the rollout by turnId.
                #
                # SSE-id scheme: history events get ids -N..-1 (a reserved range
                # strictly below every live seq, which start at 1). They are
                # per-attach ephemeral — NOT stored in self._buffer and they do
                # NOT advance self._seq, so live Last-Event-ID resumption is
                # untouched. A client that saw only history and reconnects sends
                # a negative Last-Event-ID; ``raw_last.isdigit()`` is False for
                # it, so last_id falls back to 0 and the full history replays
                # again (idempotent). A client that reached the live tail sends a
                # positive id, taking the reconnect branch below (no rollout).
                history, hist_turn_ids = self._load_history()
                hid = -len(history)
                for event in history:
                    await send_item(hid, event)
                    hid += 1
                for seq, event in list(self._buffer):
                    # Skip buffered events for turns the rollout already covers;
                    # keep the in-flight turn (and any turn not yet flushed to
                    # the rollout) plus synthetic events (no turnId).
                    if _event_turn_id(event) not in hist_turn_ids:
                        await send_item(seq, event)
                    sent_through = seq  # buffer is seq-ordered; advance past all
            else:
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

    # -- lifecycle -----------------------------------------------------------

    async def start(self, bind_iface: str) -> int:
        app = web.Application()
        app.router.add_get("/events", self._handle_events)
        app.router.add_post("/send", self._handle_send)
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
        for fut in self._pending_permissions.values():
            if not fut.done():
                fut.set_result(PermissionDecision(
                    behavior="deny", message="optio harness: session ending",
                ))
        # Wake every open /events handler so it returns now, instead of letting
        # runner.cleanup() wait for the long-lived SSE loops.
        for queue in list(self._subscribers):
            queue.put_nowait(_STOP)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
