"""GrokConversation — engine-side driver for one headless Grok session over
the Agent Client Protocol (ACP): JSON-RPC 2.0 over the stdin/stdout of
``grok agent … stdio``.

The session body launches ``grok agent [--model M] [--always-approve]
--no-leader stdio`` via ``host.launch_subprocess(stdin=True)``, attaches the
handle here, starts ``run_reader()``, runs ``bootstrap()`` (the ACP
handshake), publishes this object via ``ctx.publish_result``, and waits until
the subprocess ends.

Event payloads are transparent: every parsed stdout JSON-RPC object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. Structurally mirrors optio-claudecode's
ClaudeCodeConversation, but frames ACP instead of claude stream-json.

============================================================================
ACP WIRE FACTS — pinned by a LIVE PROBE of the real `grok agent stdio`
(grok 0.2.81, model grok-composer-2.5-fast). See docs Stage 6 Task 0.
============================================================================

Client -> agent REQUESTS (have `id`, expect a `result`):
  * ``initialize`` {protocolVersion:1, clientCapabilities:{fs:{readTextFile,
    writeTextFile}, terminal}} -> {protocolVersion, agentCapabilities,
    authMethods, _meta:{modelState, availableCommands, …}}.
  * ``session/new`` {cwd, mcpServers:[]} -> {sessionId, models, _meta}.
  * ``session/prompt`` {sessionId, prompt:[{type:"text", text}]} ->
    **THIS RESPONSE IS THE TURN-END SIGNAL**: {stopReason:"end_turn" |
    "cancelled" | …, _meta:{promptId, totalTokens, …}}. A denied/aborted turn
    returns stopReason:"cancelled" (with _meta.cancellationCategory =
    "PermissionRejected" / "MidTurnAbort").

Agent -> client NOTIFICATIONS (no `id`): ``session/update`` with
``params.update.sessionUpdate`` ∈:
  * ``agent_message_chunk`` — {update:{sessionUpdate, content:{type:"text",
    text}}}. Concatenate per turn -> the final answer (on_message).
  * ``agent_thought_chunk`` — same shape; reasoning, NOT folded into answer.
  * ``tool_call``          — {update:{sessionUpdate, toolCallId, title,
    rawInput, _meta.updateParams:{kind,status}}}.
  * ``tool_call_update``   — {update:{sessionUpdate, toolCallId, kind, title,
    content:[…], rawInput, status}}.
  * ``plan`` / ``available_commands_update`` / ``user_message_chunk`` and the
    ``_x.ai/*`` notifications — passed through untouched to on_event.

Agent -> client REQUESTS (have `id` AND `method`, WE must respond):
  * ``session/request_permission`` {sessionId, toolCall:{toolCallId, kind,
    title, rawInput}, options:[{optionId, name, kind}]}. Option `kind` ∈
    {allow_once, allow_always, reject_once, reject_always}. ANSWER with
    ``result``:
       allow  -> {outcome:{outcome:"selected", optionId:<an allow_* option>}}
       deny   -> {outcome:{outcome:"selected", optionId:<a reject_* option>}}
                 or {outcome:{outcome:"cancelled"}} if no reject option.
    (Only appears when the client does NOT advertise the relevant capability;
    we advertise neither terminal nor fs write, so grok runs its own tools and
    asks here — that is the permission gate seam.)
  * ``terminal/create`` / ``fs/*`` — only if we advertise those capabilities
    (we do not); answered with a JSON-RPC method-not-found error defensively.

Client -> agent CANCEL: ``session/cancel`` {sessionId} is a NOTIFICATION
(no `id`, no ack). It makes the in-flight ``session/prompt`` return
stopReason:"cancelled" — that response is the interrupt's completion signal.
============================================================================
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

# ACP option `kind` prefixes for allow / reject decisions.
_ALLOW_KINDS = ("allow_once", "allow_always", "allow")
_REJECT_KINDS = ("reject_once", "reject_always", "reject")


class GrokConversation:
    """Implements optio_agents.conversation.Conversation for Grok (ACP)."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_label: str = "grok",
        permission_gate: bool = False,
        mcp_servers: list | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_label = agent_label
        # When False, session/request_permission is answered with a defensive
        # deny (design §3.5) instead of being queued for a handler.
        self._permission_gate = permission_gate
        self._mcp_servers = mcp_servers or []
        self._handle = None
        self._session_id: str | None = None
        self._pending = 0                    # user turns awaiting their result
        self._closed = asyncio.Event()
        self._close_reason: str | None = None
        # Cooperative-shutdown request towards the owning task body.
        self.close_requested = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._event_handlers: list = []
        self._message_handlers: list = []
        self._permission_handler = None
        self._queued_permission_requests: list[dict] = []
        # JSON-RPC id bookkeeping.
        self._next_id = 0
        self._req_futures: dict[int, asyncio.Future] = {}   # handshake requests
        self._prompt_ids: set[int] = set()                  # session/prompt turns
        # Accumulates agent_message_chunk text for the current turn.
        self._answer_parts: list[str] = []
        self._dispatcher_task: asyncio.Task | None = None

    # -- wiring ------------------------------------------------------------

    def attach(self, handle) -> None:
        """Attach the live ProcessHandle (must have been launched with
        stdin=True)."""
        if handle.stdin is None:
            raise ValueError(
                "GrokConversation.attach: handle has no stdin writer; launch "
                "the subprocess with stdin=True"
            )
        self._handle = handle

    async def bootstrap(self) -> None:
        """Run the ACP handshake: ``initialize`` then ``session/new``.

        Requires ``run_reader()`` to already be running (it routes the
        responses back to the futures created here). We advertise NEITHER the
        terminal NOR fs-write client capability, so grok executes its own tools
        and surfaces approval via ``session/request_permission`` (the gate
        seam) instead of delegating tool execution to us.
        """
        await self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        })
        resp = await self._request("session/new", {
            "cwd": self._cwd,
            "mcpServers": self._mcp_servers,
        })
        result = (resp or {}).get("result") or {}
        self._session_id = result.get("sessionId")
        if not self._session_id:
            raise RuntimeError(
                f"grok ACP session/new returned no sessionId: {result!r}"
            )

    async def run_reader(self) -> None:
        """Drain stdout until EOF; route JSON-RPC messages. Owned by the
        session body; ends when the subprocess ends."""
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
                    _LOG.warning("grok conversation: unparseable line: %.200s", line)
                    self._event_queue.put_nowait(
                        {"type": "x-optio-unparseable", "line": line},
                    )
                    continue
                self._route(obj)
        finally:
            await self._finish("process ended")

    def _route(self, obj: dict) -> None:
        rid = obj.get("id")
        method = obj.get("method")
        if method is None and rid is not None and ("result" in obj or "error" in obj):
            # Response to one of OUR requests.
            if rid in self._req_futures:
                fut = self._req_futures.pop(rid)
                if not fut.done():
                    fut.set_result(obj)
            elif rid in self._prompt_ids:
                # session/prompt response == turn end.
                self._prompt_ids.discard(rid)
                self._pending = max(0, self._pending - 1)
                text = "".join(self._answer_parts)
                self._answer_parts = []
                self._fire_message(text)
        elif method is not None and rid is not None:
            # Agent -> client REQUEST that we must answer.
            if method == "session/request_permission":
                self._on_permission(obj)
            else:
                # Unadvertised capability (terminal/create, fs/*): decline so
                # grok falls back to running the tool itself.
                asyncio.ensure_future(self._write_json({
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601,
                              "message": f"optio grok client does not implement {method}"},
                }))
        elif method == "session/update":
            self._on_session_update(obj)
        # else: other agent notifications (_x.ai/*, plan, …) — pass through only.
        self._event_queue.put_nowait(obj)

    def _on_session_update(self, obj: dict) -> None:
        update = (obj.get("params") or {}).get("update") or {}
        if update.get("sessionUpdate") == "agent_message_chunk":
            text = ((update.get("content") or {}).get("text")) or ""
            if text:
                self._answer_parts.append(text)

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
            _LOG.exception("grok conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate ----------------------------------------------------

    def _on_permission(self, obj: dict) -> None:
        if not self._permission_gate:
            _LOG.warning(
                "grok conversation: session/request_permission received with "
                "permission_gate off; denying defensively",
            )
            asyncio.ensure_future(self._answer_permission_decision(
                obj, PermissionDecision(
                    behavior="deny",
                    message="optio harness: permission gate not enabled",
                ),
            ))
            return
        if self._permission_handler is None:
            # Queue until a handler is registered; the turn blocks agent-side,
            # which closes the publish/registration race.
            self._queued_permission_requests.append(obj)
            return
        asyncio.ensure_future(self._answer_permission(obj))

    async def _answer_permission(self, obj: dict) -> None:
        params = obj.get("params") or {}
        tool_call = params.get("toolCall") or {}
        request = PermissionRequest(
            tool_name=tool_call.get("title") or tool_call.get("kind") or "",
            input=tool_call.get("rawInput") or {},
            raw=obj,
        )
        try:
            decision = await self._permission_handler(request)
        except Exception:  # noqa: BLE001
            _LOG.exception("grok conversation: permission handler raised; denying")
            decision = PermissionDecision(
                behavior="deny",
                message="optio harness: permission handler failed",
            )
        await self._answer_permission_decision(obj, decision)

    async def _answer_permission_decision(
        self, obj: dict, decision: PermissionDecision,
    ) -> None:
        params = obj.get("params") or {}
        options = params.get("options") or []
        wanted = _ALLOW_KINDS if decision.behavior == "allow" else _REJECT_KINDS
        option_id = None
        for opt in options:
            if (opt.get("kind") or "").lower() in wanted:
                option_id = opt.get("optionId")
                break
        if option_id is not None:
            outcome = {"outcome": "selected", "optionId": option_id}
        else:
            # No matching option (e.g. deny with no reject_* option): cancelling
            # the request is ACP's abort path.
            outcome = {"outcome": "cancelled"}
        await self._write_json({
            "jsonrpc": "2.0", "id": obj.get("id"),
            "result": {"outcome": outcome},
        })

    # -- Conversation protocol surface --------------------------------------

    async def send(self, text: str) -> None:
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._session_id is None:
            raise RuntimeError("GrokConversation.send before bootstrap() completed")
        self._next_id += 1
        rid = self._next_id
        self._prompt_ids.add(rid)
        self._pending += 1
        try:
            await self._write_json({
                "jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                "params": {
                    "sessionId": self._session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
            })
        except Exception:
            self._prompt_ids.discard(rid)
            self._pending = max(0, self._pending - 1)
            await self._finish("stdin write failed")
            raise

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
        if self._pending == 0 or self._session_id is None:
            return
        # session/cancel is a notification (no id); the in-flight prompt
        # response carrying stopReason:"cancelled" is the completion signal.
        await self._write_json({
            "jsonrpc": "2.0", "method": "session/cancel",
            "params": {"sessionId": self._session_id},
        })

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- internals -----------------------------------------------------------

    async def _request(self, method: str, params: dict) -> dict:
        """Send a client->agent request and await its response (handshake only)."""
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._req_futures[rid] = fut
        await self._write_json({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params,
        })
        return await fut

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
        self._closed.set()
        self._close_reason = reason
        # Fail any in-flight handshake requests.
        for fut in self._req_futures.values():
            if not fut.done():
                fut.set_exception(ConversationClosed(reason))
        self._req_futures.clear()
        self._event_queue.put_nowait({"type": "x-optio-closed", "reason": reason})
        # Stop the dispatcher, then drain whatever it left so subscribers are
        # guaranteed to see the final x-optio-closed event.
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
