"""OpencodeConversation — engine-side driver for one opencode session over
the spawned server's native HTTP+SSE API.

The session body launches the opencode server (``launch_opencode``),
pre-creates a session, constructs this object with the same
``(worker_port, password, session_id)`` it already produces, publishes it via
``ctx.publish_result``, and runs ``run_reader()`` until teardown.

Live events come from ``GET /global/event`` — the per-instance
``/event?directory=…`` endpoint closes immediately after ``server.connected``
on the shipped server (verified empirically against 1.14.45, Task 8 fixtures).
Each ``/global/event`` frame wraps the event as
``{"directory"?, "project"?, "payload": {"id", "type", "properties"}}``
(``server.connected``/``server.heartbeat`` carry no ``directory``); the driver
drops frames for other directories and fans the unwrapped payload out to
``on_event`` subscribers as a dict, unmodified (``{"id", "type",
"properties"}``). Synthetic events use the ``x-optio-`` type prefix.
Permission requests are event-driven (``permission.asked``) with a
list-endpoint sweep on every SSE (re)connect, so requests that fired during a
stream gap are never lost.

See docs/2026-06-11-opencode-conversation-mode-design.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

_LOG = logging.getLogger(__name__)

# Reconnect backoff for the SSE reader (capped; the session body cancels the
# reader at teardown, so there is no give-up path while the task is alive).
_RECONNECT_DELAYS = (0.2, 0.5, 1.0, 2.0, 5.0)


class OpencodeConversation:
    """Implements optio_agents.conversation.Conversation for opencode."""

    def __init__(
        self, *, port: int, password: str, session_id: str, directory: str,
    ) -> None:
        self._base = f"http://127.0.0.1:{port}"
        self._auth = aiohttp.BasicAuth("opencode", password)
        self._session_id = session_id
        self._directory = directory
        # The server resolves instance directories (symlinks etc.) before
        # stamping them onto /global/event frames; compare against realpath too.
        self._directory_real = os.path.realpath(directory)
        # Active model as "providerID/modelID" (or None → let opencode pick its
        # own default). The operator UI attaches the model itself, client-side,
        # per prompt_async; this field is used by the server-side model probe,
        # which drives a throwaway conversation and must run each turn under a
        # specific model. Split back into {providerID, modelID} in send().
        self.current_model_id: str | None = None
        self._pending = False
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        self._answered_permissions: set[str] = set()
        # Text parts of the in-flight assistant message, keyed by part id —
        # joined and fired via on_message when the message completes.
        self._part_texts: dict[str, dict[str, str]] = {}
        self._dispatcher_task: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None
        # Set once the reader owns an aiohttp session AND the /global/event
        # stream is connected — i.e. it is safe to send() and expect the answer/
        # error events back. The model probe waits on this before its first turn
        # (otherwise it would send() while _http is still None and mark every
        # model unusable — a startup race).
        self._ready = asyncio.Event()

    # -- wiring ------------------------------------------------------------

    async def run_reader(self) -> None:
        """Connect to /global/event and dispatch frames until cancelled (by
        the session body at teardown) or closed. Reconnects with backoff."""
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        self._http = aiohttp.ClientSession(auth=self._auth)
        attempt = 0
        try:
            while not self._closed.is_set():
                try:
                    await self._consume_sse()
                    attempt = 0  # clean EOF: server still alive, reconnect fresh
                except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError) as exc:
                    _LOG.info("conversation: SSE drop (%s); reconnecting", exc)
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
        finally:
            await self._finish("process ended")
            await self._http.close()

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _params(self) -> dict:
        return {"directory": self._directory}

    async def _consume_sse(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        # /global/event, not /event?directory=…: the per-instance endpoint
        # ends its stream right after server.connected (observed on 1.14.45),
        # so we take the global firehose and filter by directory ourselves.
        async with self._http.get(
            self._url("/global/event"), timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            # The stream is live and _http exists: sends will now reach the
            # server and their answer/error events will arrive. On a reconnect
            # this is already set (harmless).
            self._ready.set()
            # A (re)connect can postdate permission.asked events we never saw.
            await self._sweep_permissions()
            data_lines: list[str] = []
            async for raw in resp.content:
                line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line == "" and data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        obj = json.loads(payload)
                    except ValueError:
                        _LOG.warning("conversation: unparseable SSE data: %.200s", payload)
                        self._event_queue.put_nowait(
                            {"type": "x-optio-unparseable", "line": payload},
                        )
                        continue
                    self._route_frame(obj)

    # -- event routing -------------------------------------------------------

    def _route_frame(self, obj: dict) -> None:
        """Unwrap one /global/event frame: drop other directories' events,
        route the inner ``{"id", "type", "properties"}`` payload. Bare
        (unwrapped) frames are routed as-is for fake/forward compatibility."""
        frame_dir = obj.get("directory")
        if (
            frame_dir is not None
            and frame_dir != self._directory
            and os.path.realpath(frame_dir) != self._directory_real
        ):
            return
        payload = obj.get("payload")
        self._route(payload if isinstance(payload, dict) else obj)

    def _for_this_session(self, props: dict) -> bool:
        sid = (
            props.get("sessionID")
            or (props.get("info") or {}).get("sessionID")
            or (props.get("part") or {}).get("sessionID")
        )
        return sid is None or sid == self._session_id

    def _route(self, obj: dict) -> None:
        t = obj.get("type") or ""
        props = obj.get("properties") or {}
        if t == "permission.asked" and self._for_this_session(props):
            self._on_permission_asked(props)
        elif t == "message.part.updated":
            part = props.get("part") or {}
            if part.get("type") == "text" and self._for_this_session(props):
                mid, pid = str(part.get("messageID")), str(part.get("id"))
                self._part_texts.setdefault(mid, {})[pid] = part.get("text") or ""
        elif t == "message.updated":
            info = props.get("info") or {}
            if (
                info.get("role") == "assistant"
                and (info.get("time") or {}).get("completed")
                and self._for_this_session(props)
            ):
                parts = self._part_texts.pop(str(info.get("id")), {})
                if parts:
                    self._fire_message("\n\n".join(parts.values()))
        elif t in ("session.status", "session.idle") and self._for_this_session(props):
            status = props.get("status") or {}
            if t == "session.idle" or status.get("type") == "idle":
                self._pending = False
            elif status.get("type") == "busy":
                self._pending = True
        self._event_queue.put_nowait(obj)

    async def _dispatch_loop(self) -> None:
        while True:
            obj = await self._event_queue.get()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")

    async def _call_handler(self, handler, arg, label: str) -> None:
        try:
            result = handler(arg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
            _LOG.exception("conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate -------------------------------------------------------

    def _on_permission_asked(self, props: dict) -> None:
        rid = str(props.get("id") or "")
        if not rid or rid in self._answered_permissions:
            return
        if self._permission_handler is None:
            # Queue until a handler is registered: opencode blocks the session
            # on the unanswered ask, which closes the publish/registration
            # race. Documented caller contract: register promptly.
            self._queued_permission_requests.append(props)
            return
        asyncio.ensure_future(self._answer_permission(props))

    async def _sweep_permissions(self) -> None:
        """Fetch pending permission requests and feed unanswered ones for our
        session to the gate. Gap-safety: covers asks fired while the SSE
        stream was down (opencode's /global/event has no server-side replay)."""
        try:
            async with self._http.get(
                self._url("/permission"), params=self._params(),
            ) as resp:
                resp.raise_for_status()
                pending = await resp.json()
        except (aiohttp.ClientError, ConnectionError, ValueError) as exc:
            _LOG.warning("conversation: permission sweep failed: %s", exc)
            return
        for props in pending:
            if props.get("sessionID") in (None, self._session_id):
                self._on_permission_asked(props)

    async def _answer_permission(self, props: dict) -> None:
        rid = str(props.get("id"))
        if rid in self._answered_permissions:
            return
        self._answered_permissions.add(rid)
        request = PermissionRequest(
            tool_name=str(props.get("permission") or ""),
            input=props.get("metadata") or {},
            raw=props,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny", message="optio harness: permission handler failed",
            )
        # opencode reply vocabulary: allow → "once" (never "always": the optio
        # gate decides per request); deny → "reject". updated_input has no
        # opencode equivalent and is ignored.
        body: dict = (
            {"reply": "once"} if decision.behavior == "allow"
            else {"reply": "reject", "message": decision.message or "Denied by the operator."}
        )
        try:
            async with self._http.post(
                self._url(f"/permission/{rid}/reply"),
                params=self._params(), json=body,
            ) as resp:
                if resp.status >= 400:
                    _LOG.warning(
                        "conversation: permission reply %s → HTTP %s "
                        "(likely already answered elsewhere)", rid, resp.status,
                    )
        except (aiohttp.ClientError, ConnectionError) as exc:
            _LOG.warning("conversation: permission reply failed: %s", exc)
        self._event_queue.put_nowait({
            "type": "x-optio-permission-answered",
            "request_id": rid,
            "behavior": decision.behavior,
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._http is None:
            # The reader hasn't created the aiohttp session yet (or was never
            # started). Fail loudly instead of an opaque AttributeError.
            raise ConversationClosed("conversation not started")
        self._pending = True
        body: dict = {"parts": [{"type": "text", "text": text}]}
        if self.current_model_id:
            prov, _, mod = self.current_model_id.partition("/")
            if prov and mod:
                body["model"] = {"providerID": prov, "modelID": mod}
        try:
            async with self._http.post(
                self._url(f"/session/{self._session_id}/prompt_async"),
                params=self._params(),
                json=body,
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, ConnectionError) as exc:
            self._pending = False
            raise ConversationClosed(f"send failed: {exc}") from exc

    def on_event(self, handler):
        self._event_handlers.append(handler)
        return lambda: self._event_handlers.remove(handler)

    def on_message(self, handler):
        self._message_handlers.append(handler)
        return lambda: self._message_handlers.remove(handler)

    def on_permission_request(self, handler):
        self._permission_handler = handler
        queued, self._queued_permission_requests = (
            self._queued_permission_requests, [],
        )
        for props in queued:
            asyncio.ensure_future(self._answer_permission(props))

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending

    async def interrupt(self) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if not self._pending:
            return
        async with self._http.post(
            self._url(f"/session/{self._session_id}/abort"),
            params=self._params(), json={},
        ) as resp:
            resp.raise_for_status()

    async def set_active_model(self, model: str) -> None:
        """Record the model (``"providerID/modelID"``) attached to subsequent
        sends. Used by the server-side model probe; the operator UI drives its
        own selection client-side (see set_control)."""
        self.current_model_id = model

    async def set_control(self, control_id: str, value: "str | bool") -> None:
        # opencode's only session control is the model, which is resolved and
        # applied entirely UI-local (attached per-prompt); there is no server
        # round-trip, so this satisfies the Conversation protocol as a no-op.
        return None

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._close_reason = reason
        self._event_queue.put_nowait({"type": "x-optio-closed", "reason": reason})
        # Stop the dispatcher, then drain whatever it left in the queue so
        # subscribers are guaranteed to see the final x-optio-closed event.
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        while not self._event_queue.empty():
            obj = self._event_queue.get_nowait()
            for handler in list(self._event_handlers):
                await self._call_handler(handler, obj, "on_event")
