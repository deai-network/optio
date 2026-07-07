"""ClaudeCodeConversation — engine-side driver for one headless Claude Code
session over the bidirectional stream-json stdio protocol.

The session body launches ``claude -p --input-format stream-json
--output-format stream-json`` via ``host.launch_subprocess(stdin=True)``,
attaches the handle here, publishes this object via ``ctx.publish_result``,
and runs ``run_reader()`` until the subprocess ends.

Event payloads are transparent: every parsed stdout NDJSON object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. See the design doc §3.3.
"""

from __future__ import annotations

import asyncio
import json
import logging

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

_LOG = logging.getLogger(__name__)


def _user_message_line(text: str) -> bytes:
    return (json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n").encode("utf-8")


class ClaudeCodeConversation:
    """Implements optio_agents.conversation.Conversation for Claude Code."""

    def __init__(self, *, agent_label: str = "claude", permission_gate: bool = False) -> None:
        self._agent_label = agent_label
        # When False, can_use_tool control_requests are answered with a
        # defensive deny (design §3.5) instead of being queued for a handler.
        self._permission_gate = permission_gate
        self._handle = None
        self._pending = 0
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        # Model-change request towards the owning task body. When set, the
        # conversation body kills and relaunches claude with requested_model.
        self.model_change_requested: asyncio.Event = asyncio.Event()
        self.requested_model: str | None = None
        # Reasoning-effort-change request towards the owning task body. When
        # set, the body relaunches claude with the new --effort (same restart
        # path as a model change); requested_effort holds the picked level.
        self.effort_change_requested: asyncio.Event = asyncio.Event()
        self.requested_effort: str | None = None
        # Runtime model announced by the stream's system/init event (the REAL
        # running model, e.g. "claude-opus-4-8[1m]" — carries a [variant]
        # suffix and is populated even for a default-model session where
        # config.model is None). Captured in _route the moment it is seen so
        # the owning body can re-derive the reasoning_effort control's presence
        # for the actual model; the Event stays set, so the body never races
        # the reader for it.
        self.runtime_model: str | None = None
        self.runtime_model_observed: asyncio.Event = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        self._next_request_id = 0
        self._control_acks: dict[str, asyncio.Future] = {}
        self._dispatcher_task: asyncio.Task | None = None
        # Set while the session body is killing the current claude process to
        # relaunch it on a new model. A process EOF during a restart must NOT
        # close the conversation (no x-optio-closed, _closed stays clear) — the
        # task and the widget stay live across the swap. attach() clears it.
        self._restarting = False

    # -- wiring ------------------------------------------------------------

    def attach(self, handle) -> None:
        """Attach the live ProcessHandle (must have been launched with
        stdin=True)."""
        if handle.stdin is None:
            raise ValueError(
                "ClaudeCodeConversation.attach: handle has no stdin writer; "
                "launch the subprocess with stdin=True"
            )
        self._handle = handle
        # The new live process is attached; a future real EOF should close
        # normally again.
        self._restarting = False

    async def run_reader(self) -> None:
        """Drain stdout until EOF; dispatch events. Owned by the session
        body; ends when the subprocess ends."""
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
        try:
            async for raw in self._handle.stdout:
                line = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, bytes) else str(raw)
                ).strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    _LOG.warning("conversation: unparseable line: %.200s", line)
                    self._event_queue.put_nowait(
                        {"type": "x-optio-unparseable", "line": line},
                    )
                    continue
                self._route(obj)
        finally:
            await self._finish("process ended")

    def _route(self, obj: dict) -> None:
        t = obj.get("type")
        if t == "result":
            self._pending = max(0, self._pending - 1)
            text = obj.get("result")
            if isinstance(text, str):
                self._fire_message(text)
        elif t == "control_response":
            # Ack for one of OUR control_requests (e.g. interrupt).
            resp = obj.get("response") or {}
            rid = str(resp.get("request_id", ""))
            fut = self._control_acks.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_result(obj)
        elif t == "control_request":
            req = obj.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                self._on_can_use_tool(obj)
            else:
                _LOG.info(
                    "conversation: unhandled control_request subtype %r",
                    req.get("subtype"),
                )
        elif t == "system" and obj.get("subtype") == "init":
            # The stream announces the REAL running model here; capture it (raw,
            # incl. any [variant] suffix) so the body can fold it into the
            # effort control's presence. Set on every launch (incl. relaunch).
            model = obj.get("model")
            if isinstance(model, str) and model:
                self.runtime_model = model
                self.runtime_model_observed.set()
        self._event_queue.put_nowait(obj)

    # -- event fan-out -----------------------------------------------------

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

    # -- permission gate ----------------------------------------------------

    def _on_can_use_tool(self, obj: dict) -> None:
        if not self._permission_gate:
            # Gate off: no permission plumbing was requested from the CLI, so
            # a can_use_tool arriving anyway is answered with a defensive deny
            # rather than queued against a handler nobody will register.
            _LOG.warning(
                "conversation: can_use_tool received with permission_gate "
                "off; denying defensively",
            )
            asyncio.ensure_future(self._write_json({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": obj.get("request_id"),
                    "response": {
                        "behavior": "deny",
                        "message": "optio harness: permission gate not enabled",
                    },
                },
            }))
            return
        if self._permission_handler is None:
            # Queue until a handler is registered: the turn blocks CLI-side,
            # which closes the publish/registration race. Documented caller
            # contract: register promptly when permission_gate=True.
            self._queued_permission_requests.append(obj)
            return
        asyncio.ensure_future(self._answer_permission(obj))

    async def _answer_permission(self, obj: dict) -> None:
        req = obj.get("request") or {}
        request = PermissionRequest(
            tool_name=req.get("tool_name", ""),
            input=req.get("input") or {},
            raw=obj,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny",
                message="optio harness: permission handler failed",
            )
        inner: dict = {"behavior": decision.behavior}
        if decision.behavior == "allow":
            # Always echo updatedInput (the original input when the operator
            # didn't edit it): Claude Code's can_use_tool allow schema expects
            # it, and it's the hook for future edit-then-approve.
            inner["updatedInput"] = (
                decision.updated_input
                if decision.updated_input is not None else request.input
            )
        else:
            # Deny requires a human-readable message in the schema; default one
            # so a bare deny (no reason supplied) still validates.
            inner["message"] = decision.message or "Denied by the operator."
        await self._write_json({
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": obj.get("request_id"),
                "response": inner,
            },
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        self._pending += 1
        try:
            await self._write_bytes(_user_message_line(text))
        except Exception:
            self._pending -= 1
            await self._finish("stdin write failed")
            raise

    async def set_control(self, control_id: str, value) -> None:
        """Push a session-control value change to the native transport.

        Claude Code applies both a model change and a reasoning-effort change by
        restart: it stores the requested value and fires the matching change
        Event, making the session body kill and relaunch claude with the new
        ``--model`` / ``--effort`` (the restart loop handles both arms). ``model``
        and ``reasoning_effort`` are the controls claudecode exposes; unknown ids
        are ignored."""
        if control_id == "model":
            self.requested_model = value
            self.model_change_requested.set()
        elif control_id == "reasoning_effort":
            self.requested_effort = value
            self.effort_change_requested.set()

    def emit_control_update(self, controls: list[dict]) -> None:
        """Fan out a synthetic ``x-optio-control-update`` carrying a full
        controls snapshot so the widget reducer re-projects ``state.controls``.

        Used after a model/effort relaunch to make the reasoning_effort slider's
        presence and preselected level follow the (possibly new) running model —
        the same reactive path other engines use on a live control change."""
        self._event_queue.put_nowait({
            "type": "x-optio-control-update",
            "controls": controls,
        })

    def begin_restart(self) -> None:
        """Mark that the current process is about to be killed for a model
        swap, so its EOF does not close the conversation. Cleared by attach()
        when the relaunched process is wired in."""
        self._restarting = True

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
        for obj in queued:
            asyncio.ensure_future(self._answer_permission(obj))

        def _unsub() -> None:
            if self._permission_handler is handler:
                self._permission_handler = None
        return _unsub

    def is_pending(self) -> bool:
        return self._pending > 0

    async def interrupt(self) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._pending == 0:
            return
        self._next_request_id += 1
        rid = f"optio-{self._next_request_id}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._control_acks[rid] = fut
        await self._write_json({
            "type": "control_request",
            "request_id": rid,
            "request": {"subtype": "interrupt"},
        })
        await fut

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _write_json(self, obj: dict) -> None:
        await self._write_bytes((json.dumps(obj) + "\n").encode("utf-8"))

    async def _write_bytes(self, data: bytes) -> None:
        async with self._write_lock:
            stdin = self._handle.stdin
            stdin.write(data)
            drain = getattr(stdin, "drain", None)
            if drain is not None:
                await drain()

    async def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        # During an intentional model-swap restart the process EOF is not a
        # close: keep the conversation open (no _closed, no x-optio-closed) and
        # only tear down this process's dispatcher below. attach() re-arms the
        # normal close path for the relaunched process.
        if not self._restarting:
            self._closed.set()
            self._close_reason = reason
            # Fail any in-flight interrupt acks.
            for fut in self._control_acks.values():
                if not fut.done():
                    fut.set_exception(ConversationClosed(reason))
            self._control_acks.clear()
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
