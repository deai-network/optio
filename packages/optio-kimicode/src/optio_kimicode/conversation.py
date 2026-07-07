"""KimiCodeConversation — engine-side driver for one headless Kimi Code
session over the Agent Client Protocol (ACP): JSON-RPC 2.0 over the
stdin/stdout of ``kimi acp``.

The session body launches ``kimi acp`` via ``host.launch_subprocess(
stdin=True)``, attaches the handle here, starts ``run_reader()``, runs
``bootstrap()`` (the ACP handshake), publishes this object via
``ctx.publish_result``, and waits until the subprocess ends.

Event payloads are transparent: every parsed stdout JSON-RPC object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. Ported from optio-cursor's / optio-grok's ACP
drivers — all three agents speak the same public ACP protocol.

============================================================================
ACP WIRE FACTS for ``kimi acp`` (JSON-RPC 2.0 over stdio).
Provenance: pinned by reading the Kimi Code source that implements the
server — ``@moonshot-ai/acp-adapter`` (``.kimi-src/kimi-code/packages/
acp-adapter/src/{server,session,approval,config-options}.ts``) driven from
``apps/kimi-code/src/cli/sub/acp.ts``. Kimi implements the same public ACP
protocol as grok/cursor; the deltas below are the only kimi-specific shapes.
============================================================================

Client -> agent REQUESTS (have `id`, expect a `result`):
  * ``initialize`` {protocolVersion:1, clientCapabilities:{fs:{readTextFile,
    writeTextFile}, terminal}} -> {protocolVersion, agentCapabilities:{
    loadSession, promptCapabilities, sessionCapabilities:{list,resume}},
    authMethods:[<terminal-auth method>], agentInfo}. (server.ts:initialize)
  * ``session/new`` {cwd, mcpServers:[]} -> {sessionId, configOptions}.
    **KIMI DELTA**: session/new returns a unified ``configOptions:
    SessionConfigOption[]`` surface (PLAN D11) — NOT grok/cursor's ``models``
    block. The model picker is the config option with ``id:"model"`` whose
    ``currentValue`` is the current model id and whose ``options:[{value,
    name}]`` list the choices. (server.ts:newSession, config-options.ts)
  * ``session/prompt`` {sessionId, prompt:[{type:"text", text}]} ->
    **THIS RESPONSE IS THE TURN-END SIGNAL**: {stopReason:"end_turn" |
    "cancelled" | …}. A denied/aborted turn returns stopReason:"cancelled".
    (session.ts:prompt returns {stopReason})
  * ``session/set_model`` {sessionId, modelId} -> ok. The experimental
    ACP model switch (``unstable_setSessionModel``); present in kimi.
    (server.ts:unstable_setSessionModel, SetSessionModelRequest={sessionId,
    modelId})

Agent -> client NOTIFICATIONS (no `id`): ``session/update`` with
``params.update.sessionUpdate`` ∈:   (events-map.ts, session.ts)
  * ``agent_message_chunk`` — {update:{sessionUpdate, content:{type:"text",
    text}}}. Concatenate per turn -> the final answer (on_message).
  * ``agent_thought_chunk`` — same shape; reasoning, NOT folded into answer.
  * ``tool_call``          — {update:{sessionUpdate, toolCallId, title,
    rawInput, …}}.
  * ``tool_call_update``   — {update:{sessionUpdate, toolCallId, kind, title,
    content:[…], rawInput, status}}.
  * ``plan`` / ``available_commands_update`` / ``config_option_update`` /
    ``user_message_chunk`` — passed through untouched to on_event.

Agent -> client REQUESTS (have `id` AND `method`, WE must respond):
  * ``session/request_permission`` {sessionId, toolCall:{toolCallId, kind,
    title, rawInput}, options:[{optionId, name, kind}]}. Option `kind` ∈
    {allow_once, allow_always, reject_once}. ANSWER with ``result``:
       allow  -> {outcome:{outcome:"selected", optionId:<an allow_* option>}}
       deny   -> {outcome:{outcome:"selected", optionId:<a reject_* option>}}
                 or {outcome:{outcome:"cancelled"}} if no reject option.
    (session.ts:handleApproval → conn.requestPermission; approval.ts pins the
    three canonical options + the {selected|cancelled} outcome mapping.)
    Only appears when the client does NOT advertise fs-write / terminal (we
    advertise neither), so kimi executes its own tools and asks here — the
    permission gate seam. (server.ts:maybeBuildAcpKaos gates on
    clientCapabilities.fs.)
  * ``terminal/create`` / ``fs/*`` — only if we advertise those capabilities
    (we do not); answered with a JSON-RPC method-not-found error defensively.

Client -> agent CANCEL: ``session/cancel`` {sessionId} is a NOTIFICATION
(no `id`, no ack). It makes the in-flight ``session/prompt`` return
stopReason:"cancelled" — that response is the interrupt's completion signal.
(server.ts:cancel — logs unknown sessionIds, returns void; session.ts:cancel)
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


class KimiCodeConversation:
    """Implements optio_agents.conversation.Conversation for Kimi Code (ACP)."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_label: str = "kimi",
        permission_gate: bool = False,
        mcp_servers: list | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_label = agent_label
        # When False, session/request_permission is answered with a defensive
        # deny instead of being queued for a handler.
        self._permission_gate = permission_gate
        self._mcp_servers = mcp_servers or []
        self._handle = None
        self._session_id: str | None = None
        # Model picker state captured at bootstrap so the session can populate
        # the picker without a separate (auth-gated) subprocess. KIMI DELTA:
        # kimi surfaces the picker as ``configOptions`` (not grok/cursor's
        # ``models`` block) — session_config_options holds the raw block and
        # current_model_id is pulled from the ``model`` config option's
        # currentValue. session_models stays None on kimi (attribute-compat
        # with the grok/cursor drivers).
        self.session_models: dict | None = None
        self.session_config_options: list | None = None
        self.current_model_id: str | None = None
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
                "KimiCodeConversation.attach: handle has no stdin writer; "
                "launch the subprocess with stdin=True"
            )
        self._handle = handle

    async def bootstrap(self) -> None:
        """Run the ACP handshake: ``initialize`` then ``session/new``.

        Requires ``run_reader()`` to already be running (it routes the
        responses back to the futures created here). We advertise NEITHER the
        terminal NOR fs-write client capability, so kimi executes its own
        tools and surfaces approval via ``session/request_permission`` (the
        gate seam) instead of delegating tool execution to us.
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
        # session/new can come back as a JSON-RPC ERROR rather than a result
        # (e.g. an invalid/rejected credential, so kimi refuses to create a
        # session). Surface kimi's ACTUAL error message — otherwise the masking
        # below (`.get("result") or {}` → {}) discards the real reason and the
        # operator is left with a useless "no sessionId: {}".
        error = (resp or {}).get("error")
        if error is not None:
            message = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(
                f"kimi ACP session/new failed: {message or error!r}"
            )
        result = (resp or {}).get("result") or {}
        self._session_id = result.get("sessionId")
        if not self._session_id:
            raise RuntimeError(
                f"kimi ACP session/new returned no sessionId: {result!r}"
            )
        # KIMI DELTA: capture the ``configOptions`` picker surface and pull the
        # current model id from the ``model`` config option's currentValue
        # (config-options.ts: {type:'select', id:'model', currentValue,
        # options:[{value,name}]}). See models.py for how the picker is built.
        config_options = result.get("configOptions")
        if isinstance(config_options, list):
            self.session_config_options = config_options
            for opt in config_options:
                if isinstance(opt, dict) and opt.get("id") == "model":
                    self.current_model_id = opt.get("currentValue")
                    break

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
                    _LOG.warning("kimi conversation: unparseable line: %.200s", line)
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
                # kimi falls back to running the tool itself.
                asyncio.ensure_future(self._write_json({
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601,
                              "message": f"optio kimi client does not implement {method}"},
                }))
        elif method == "session/update":
            self._on_session_update(obj)
        # else: other agent notifications (plan, config_option_update, …) —
        # pass through only.
        self._event_queue.put_nowait(obj)

    def _on_session_update(self, obj: dict) -> None:
        update = (obj.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = ((update.get("content") or {}).get("text")) or ""
            if text:
                self._answer_parts.append(text)
        elif kind == "config_option_update":
            # A live picker change (model/thinking/mode) — surface it to the
            # widget so the controls bar tracks the authoritative state.
            self._emit_control_update(update.get("configOptions"))

    def _emit_control_update(self, config_options) -> None:
        """Fan out a synthetic ``x-optio-control-update`` from a live
        ``config_option_update`` snapshot so the widget reducer refreshes
        ``state.controls``.

        kimi's ``config_option_update`` notification carries the FULL refreshed
        ``configOptions`` array (verified: kimi-code fork
        ``acp-adapter/src/events-map.ts:configOptionUpdateNotification``), so we
        re-project the whole snapshot through the same projection the widgetData
        seed uses (single source of truth: ``models.parse_all_controls``) and
        fold it as a complete controls snapshot — not a single-value patch."""
        if not isinstance(config_options, list):
            return
        # Lazy import: optio_kimicode.__init__ imports session -> conversation,
        # so importing models at module scope here risks a partial-init cycle.
        from optio_kimicode.models import parse_all_controls

        controls = parse_all_controls(config_options)
        for c in controls:
            if c.id == "model" and isinstance(c.value, str) and c.value:
                self.current_model_id = c.value
        self._event_queue.put_nowait({
            "type": "x-optio-control-update",
            "controls": [c.to_dict() for c in controls],
        })

    # -- event fan-out -----------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while True:
            obj = await self._event_queue.get()
            try:
                for handler in list(self._event_handlers):
                    await self._call_handler(handler, obj, "on_event")
            finally:
                self._event_queue.task_done()

    async def drain(self) -> None:
        """Block until every queued event has been dispatched to on_event.

        The replay backfill and the injected resume-notice reach subscribers
        ASYNCHRONOUSLY via ``_dispatch_loop``. The session awaits this after
        emitting them and before ``end_replay()`` so the listener's replay
        window reliably captures them ALL in the durable tier (no race where a
        late-dispatched replay event lands in the live ring instead)."""
        await self._event_queue.join()

    async def _call_handler(self, handler, arg, label: str) -> None:
        try:
            result = handler(arg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
            _LOG.exception("kimi conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate ----------------------------------------------------

    def _on_permission(self, obj: dict) -> None:
        if not self._permission_gate:
            _LOG.warning(
                "kimi conversation: session/request_permission received with "
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
            _LOG.exception("kimi conversation: permission handler raised; denying")
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
            raise RuntimeError("KimiCodeConversation.send before bootstrap() completed")
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

    def emit_event(self, obj: dict) -> None:
        """Inject a SYNTHETIC event into the on_event fan-out — the same queue +
        dispatch path routed wire events take, so it reaches every on_event
        subscriber (the ConversationListener) and lands in its replay buffer.

        Used at the replay→live boundary on resume: the resume notice is sent as
        a LIVE turn, but kimi echoes user turns as ``user_message_chunk`` only
        during a ``session/load`` replay, never live — so without an injected
        event the last replayed answer stays pending, the resume answer merges
        into it, and the notice never renders. Emitting the ``user_message_chunk``
        the reducer already consumes finalizes the pending bubble, bumps the turn
        and renders the notice as an activity row."""
        self._event_queue.put_nowait(obj)

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

    async def set_control(self, control_id: str, value) -> None:
        """Push a session-control value change to kimi over ACP (generalizes the
        former model selector — the model is just ``control_id == "model"``).

        Routing:
          * ``model`` -> ``session/set_model`` (the experimental
            ``unstable_setSessionModel {sessionId, modelId}``; LIVE-VERIFIED).
          * ``reasoning_effort`` -> ``session/set_config_option`` with
            ``configId:"thinking"``. The engine-neutral control id is
            ``reasoning_effort`` (the graded thinking slider), but the ACP
            configId the fork dispatches on is ``thinking`` — so the id is
            remapped here (the graded level string is passed through as-is).
          * everything else (``mode``, …) -> the generic
            ``session/set_config_option`` (configId == control_id).

        **VERIFIED against the kimi-code fork** (``packages/acp-adapter/src/
        server.ts:setSessionConfigOption`` + ``@agentclientprotocol/sdk`` 0.23.0
        ``SetSessionConfigOptionRequest`` in ``schema/types.gen.d.ts``): the
        request params are ``{sessionId, configId, value}`` — the option key is
        ``configId`` (NOT ``optionId``, which the plan flagged as an unknown to
        confirm), and ``value`` is the raw string the server dispatches on
        (``value === 'on'`` for ``thinking``; ``configId`` routes ``model`` /
        ``mode`` / ``thinking``, any other id -> JSON-RPC invalid_params).

        Awaited by the listener's ``/control`` route; raises ConversationClosed
        after teardown so the listener can answer 409."""
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._session_id is None:
            raise RuntimeError(
                "KimiCodeConversation.set_control before bootstrap() completed"
            )
        if control_id == "model":
            self.current_model_id = value  # optimistic
            await self._request("session/set_model", {
                "sessionId": self._session_id, "modelId": value,
            })
            return
        # The graded reasoning-effort slider is projected with control id
        # ``reasoning_effort``, but the fork's ACP configId is ``thinking``; bridge
        # the id here so the level string dispatches on the right config option.
        config_id = "thinking" if control_id == "reasoning_effort" else control_id
        await self._request("session/set_config_option", {
            "sessionId": self._session_id, "configId": config_id, "value": value,
        })

    async def close(self) -> None:
        self.close_requested.set()

    async def replay_history(self, session_id: str) -> bool:
        """Backfill the event stream with the RESTORED session's prior turns on
        resume, via ACP ``session/load``.

        The wrapper's ``bootstrap`` opened a FRESH ``session/new`` for this task,
        so kimi never re-emitted the prior conversation — a viewer attaching after
        a resume would see only NEW turns. ``session/load(sessionId)`` makes the
        agent rehydrate the on-disk session and replay its whole history as a
        synchronous batch of ``session/update`` notifications (user_message_chunk /
        agent_message_chunk / tool_call*), then settle its response. Those
        notifications flow through the SAME ``_route`` -> event-queue -> on_event
        fan-out live turns use, so they land in the listener's replay buffer with
        no extra wiring; a late viewer then reconstructs the full prior history.
        (server.ts:loadSession replays BEFORE returning; session.ts:replayHistory
        emits the per-message updates — verified against the kimi-code fork.)

        ORDERING is load-bearing: the event fan-out has no late-subscriber buffer
        of its own (``_dispatch_loop`` drops events fired before a handler
        subscribes), so this MUST be called AFTER the ConversationListener has
        subscribed to ``on_event`` (session.py calls it inside the
        ``if config.conversation_ui:`` block, after the listener is constructed).
        Awaiting the ``session/load`` response guarantees every replayed
        notification was already routed onto the (FIFO) event queue.

        Pure buffer backfill, NOT a completed turn: the replayed
        ``agent_message_chunk`` text is folded into ``_answer_parts`` by
        ``_on_session_update`` (the shared notification handler), but there is no
        matching ``session/prompt`` id, so no ``on_message`` fires — we reset
        ``_answer_parts`` afterwards so the replayed history never prefixes the
        first NEW turn's answer.

        WORKING SESSION on success: a successful ``session/load`` ADOPTS the
        loaded session as ``self._session_id`` (replacing the fresh
        ``session/new`` id bootstrap opened), so the resume-notice and every
        subsequent ``send()``/``session/prompt`` run in the RESTORED session and
        kimi rehydrates its history as the model's context — semantic continuity,
        not just visual replay.

        GRACEFUL FALLBACK: if ``session/load`` errors (the recovered id is unknown
        to the ``kimi acp`` server, a capability mismatch, or the agent rejects
        it) the agent emits no notifications and returns a JSON-RPC error — we log
        and return ``False`` WITHOUT raising, WITHOUT adopting. The fresh
        ``session/new`` session stays the working session, so resume shows no
        prior history but the conversation remains fully usable. Returns ``True``
        only when the load succeeded and history was replayed."""
        if self._closed.is_set():
            return False
        try:
            resp = await self._request("session/load", {
                "sessionId": session_id,
                "cwd": self._cwd,
                "mcpServers": self._mcp_servers,
            })
        except ConversationClosed as exc:
            _LOG.info(
                "kimicode resume: session/load unavailable (%s); starting fresh session",
                exc,
            )
            return False
        error = (resp or {}).get("error")
        if error:
            _LOG.info(
                "kimicode resume: session/load unavailable (%s); starting fresh session",
                error.get("message") if isinstance(error, dict) else error,
            )
            return False
        # Discard the answer-part accumulation the replayed agent_message_chunk
        # notifications left behind — this was history, not a live turn, so it must
        # not leak into the next real turn's coalesced answer.
        self._answer_parts = []
        # Adopt the loaded session as the WORKING session (semantic continuity):
        # bootstrap set self._session_id to the fresh session/new id, but every
        # subsequent send()/session/prompt must run in the RESTORED session so
        # kimi rehydrates its on-disk history as the model's context — otherwise
        # the resume-notice and all user turns prompt an EMPTY fresh session and
        # the agent has no memory of the prior conversation (resume amnesia).
        # Grok/cursor adopt likewise on load success; only a FAILED load (above)
        # keeps the fresh session/new id.
        self._session_id = session_id
        _LOG.info("kimicode resume: session/load replayed prior history")
        return True

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
            try:
                for handler in list(self._event_handlers):
                    await self._call_handler(handler, obj, "on_event")
            finally:
                self._event_queue.task_done()
