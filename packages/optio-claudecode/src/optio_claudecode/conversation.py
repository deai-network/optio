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
import uuid as uuid_lib

from optio_agents.conversation import (
    ConversationClosed,
    PermissionDecision,
    PermissionRequest,
)

from optio_claudecode.info import AGENT_INFO

_LOG = logging.getLogger(__name__)


def _user_message_line(text: str, message_uuid: str) -> bytes:
    # No "priority": the schema defaults an omitted one to "next", which is
    # exactly what optio always wants (cli-queue-lifecycle.md §1). The uuid
    # is the ONLY thing that turns command_lifecycle events on for this
    # message (schema: "commands enqueued without a uuid ... emit no
    # lifecycle events").
    return (json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "uuid": message_uuid,
    }) + "\n").encode("utf-8")


class ClaudeCodeConversation:
    """Implements optio_agents.conversation.Conversation for Claude Code."""

    def __init__(self, *, agent_label: str = AGENT_INFO.slug, permission_gate: bool = False) -> None:
        self._agent_label = agent_label
        # When False, can_use_tool control_requests are answered with a
        # defensive deny (design §3.5) instead of being queued for a handler.
        self._permission_gate = permission_gate
        self._handle = None
        # Sends awaiting a ``result`` (a merged turn — a message sent while a
        # turn runs joins it, so two sends can produce one result — leaves
        # this above 0 after that result). session_state_changed idle/running
        # resync it against _sends_since_result rather than zeroing it
        # outright: see the comment on that branch in _route.
        self._pending = 0
        # Sends written since the last ``result``. Reset to 0 on ``result``,
        # incremented on every send(). Used only to correct _pending against
        # a stale idle (below).
        self._sends_since_result = 0
        # Whether a system/session_state_changed event has been seen since
        # this conversation started. The _pending resync above depends on
        # those events (CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1, set by
        # conversation_launch_env); without them is_pending() can drift after
        # a merged turn. Tracked so _route can warn once, not on every result.
        self._seen_state_event = False
        self._warned_no_state_events = False
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
        # Fix 19: set by begin_session_end() when a cooperative cancel ends
        # the session while a turn runs. From then on nothing new may reach
        # the CLI (send() raises, `closed` reads True): only the graceful
        # interrupt, whose turn the kill would otherwise cut.
        self._ending = False

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
        # Fix 25 (final-review-2 I3 / ledger line 399): a relaunch (model or
        # effort change) kills the old process and calls attach() with the
        # new one. The old process's send()s never got their result (it was
        # killed, not finished), so without this is_pending() would stay
        # True forever — the UI stuck busy and every later send queuing
        # behind a queue that never drains. Both counters describe the DEAD
        # process's in-flight sends, which have no bearing on the fresh one
        # attached here (Steering.reset_transport(), called separately,
        # re-writes any message that still needs to reach it).
        self._pending = 0
        self._sends_since_result = 0

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
            if not self._seen_state_event and not self._warned_no_state_events:
                self._warned_no_state_events = True
                _LOG.warning(
                    "conversation: no system/session_state_changed event seen "
                    "before this result; is_pending() may drift after a "
                    "merged turn. Set CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1 "
                    "in the launch env (conversation_launch_env) to fix this.",
                )
            self._pending = max(0, self._pending - 1)
            self._sends_since_result = 0
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
        elif t == "system" and obj.get("subtype") == "session_state_changed":
            # The CLI brackets every turn with running/idle. A message sent
            # while a turn runs joins that turn (one result for two sends), so
            # the send/result count alone would stay "pending" forever; idle
            # is meant to mean nothing awaits a result any more.
            #
            # But the CLI's idle for a turn that just ended can arrive a few
            # ms after that turn's own result (recorded: result then idle
            # ~4 ms later), and steering's interrupt_and_send reacts to the
            # result by writing the NEXT turn's prompt within microseconds —
            # raising _pending back to 1 before the stale idle is even read.
            # Zeroing _pending unconditionally on idle would then erase that
            # brand-new turn's pending state for good (OWNER RULING, review
            # of Task 3, fix round 1). Instead, idle resyncs _pending to
            # _sends_since_result: for a genuinely-finished turn that's 0 (a
            # merged turn's extra send was folded into the count that
            # `result` already reset), but a send already written for the
            # next turn survives. running then restores _pending to at least
            # 1 if a send raced ahead of it too.
            self._seen_state_event = True
            state = obj.get("state")
            if state == "idle":
                self._pending = self._sends_since_result
            elif state == "running":
                self._pending = max(self._pending, 1)
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

    async def send(self, text: str, *, uuid: str | None = None) -> None:
        """``uuid`` is this message's stdin identity (Fix 13a): Steering
        passes its own steering id, so bubble id, response id and CLI uuid
        are one value. Callers outside Steering (session.py's agent
        feedback, the first prompt) omit it and get a fresh uuid4 here —
        every message optio writes still needs one, or the CLI never emits
        command_lifecycle for it at all (cli-queue-lifecycle.md §1)."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._ending:
            raise ConversationClosed("session ending")
        message_uuid = uuid if uuid is not None else str(uuid_lib.uuid4())
        self._pending += 1
        self._sends_since_result += 1
        try:
            await self._write_bytes(_user_message_line(text, message_uuid))
        except Exception:
            self._pending -= 1
            self._sends_since_result -= 1
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

    def emit_event(self, event: dict) -> None:
        """Fan out a synthetic ``x-optio-*`` event to on_event subscribers, in
        order with the native stream. The steering scaffold's hook: the
        conversation listener buffers it like any event, so a reload (and a
        resume) replays it."""
        self._event_queue.put_nowait(event)

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
        """True while a turn is outstanding: more sends than results seen
        since the conversation started, corrected against the CLI's own
        session_state_changed idle/running so a stale idle for an
        already-ended turn can't erase a turn that started a few ms later
        (see the comment on the session_state_changed branch in _route)."""
        return self._pending > 0

    async def interrupt(self, *, cancel_queued: bool = False) -> None:
        """Abort the running turn; a no-op when idle. ``cancel_queued``
        (Fix 19, session end) asks the CLI to drop what still waits in its
        queue instead of running it next (cli-queue-lifecycle.md,
        interrupt_receipt_v1): each dropped message gets a command_lifecycle
        ``cancelled`` and never a ``started``."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._pending == 0:
            return
        self._next_request_id += 1
        rid = f"optio-{self._next_request_id}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._control_acks[rid] = fut
        request: dict = {"subtype": "interrupt"}
        if cancel_queued:
            request["cancel_queued"] = True
        await self._write_json({
            "type": "control_request",
            "request_id": rid,
            "request": request,
        })
        await fut

    async def cancel_async_message(self, message_uuid: str) -> bool:
        """Ask the CLI to drop a still-queued message before it starts. The
        control_response's ``cancelled`` bool says whether anything was
        actually removed: false means the message already started or
        completed, or was never known, and it will be (or was) delivered
        normally (cli-queue-lifecycle.md §1). A transport capability that
        Steering no longer uses: since Fix 17 the CLI queue never holds more
        than one optio message, so "Send now" needs no cancel."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        self._next_request_id += 1
        rid = f"optio-{self._next_request_id}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._control_acks[rid] = fut
        await self._write_json({
            "type": "control_request",
            "request_id": rid,
            "request": {"subtype": "cancel_async_message", "message_uuid": message_uuid},
        })
        obj = await fut
        response = (obj.get("response") or {}).get("response") or {}
        return bool(response.get("cancelled"))

    def begin_session_end(self) -> None:
        """Fix 19 (owner ruling 2026-09-15, session-end design part 4): the
        session is ending while a turn runs. From now on ``send()`` raises
        ConversationClosed and ``closed`` reads True, so neither Steering's
        one-at-a-time writer nor ``POST /send`` can hand the CLI a message
        that would start a turn the kill cuts. interrupt() still works."""
        self._ending = True

    async def interrupt_for_session_end(self) -> None:
        """Fix 19: interrupt the running turn with ``cancel_queued`` and
        return once its ``result`` has been dispatched, so the CLI has
        recorded the partial answer in its transcript and every event it
        sent on the way is buffered. A no-op when idle. The caller bounds
        it (session.py ``_end_turn_gracefully``)."""
        if self._pending == 0:
            return
        turn_end = asyncio.Event()

        def _watch(event: dict) -> None:
            if event.get("type") == "result":
                turn_end.set()

        unsubscribe = self.on_event(_watch)
        try:
            await self.interrupt(cancel_queued=True)
            await turn_end.wait()
        finally:
            unsubscribe()

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        """True once the session has ended, or is ending (Fix 19:
        begin_session_end)."""
        return self._closed.is_set() or self._ending

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
