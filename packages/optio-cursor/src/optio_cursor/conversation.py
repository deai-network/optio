"""CursorConversation — engine-side driver for one headless Cursor session
over the Agent Client Protocol (ACP): JSON-RPC 2.0 over the stdin/stdout of
``cursor-agent acp``.

The session body launches ``cursor-agent [--model M] [--force] acp`` via
``host.launch_subprocess(stdin=True)``, attaches the handle here, starts
``run_reader()``, runs ``bootstrap()`` (the ACP handshake), publishes this
object via ``ctx.publish_result``, and waits until the subprocess ends.

Event payloads are transparent: every parsed stdout JSON-RPC object is fanned
out to ``on_event`` subscribers as a dict, unmodified. Synthetic events use
the ``x-optio-`` type prefix. Adapted from optio-grok's GrokConversation —
both agents speak the same public ACP protocol.

============================================================================
ACP WIRE FACTS for `cursor-agent acp` (JSON-RPC 2.0 over stdio).
Provenance per shape:
  [cursor-verified]  — pinned by a live UNAUTHENTICATED handshake probe of
                       the real `cursor-agent acp` on this host.
  [grok-pinned, cursor runtime-unverified] — copied from optio-grok's
                       LIVE-pinned ACP shapes (grok 0.2.81; see
                       optio_grok/conversation.py). Cursor implements the
                       same public ACP protocol; a logged-in prompt-cycle
                       probe was NOT possible (host `cursor-agent status` =
                       "Not logged in"). Runtime confirmation deferred to
                       the demo stage — tracked in design doc §7 item 3.
============================================================================

Methods present in the cursor binary [cursor-verified]:
  session/new, session/load, session/prompt, session/cancel, session/update,
  session/set_model, session/request_permission, authenticate.

Client -> agent REQUESTS (have `id`, expect a `result`):
  * ``initialize`` {protocolVersion:1, clientCapabilities:{…}} ->
    [cursor-verified] {protocolVersion:1, agentCapabilities:{loadSession:
    true, promptCapabilities:{image:true}, sessionCapabilities:{list:{}}},
    authMethods:[{id:"cursor_login"}]}.
  * ``session/new`` {cwd, mcpServers:[]} -> {sessionId, models, _meta}.
    [grok-pinned, cursor runtime-unverified]
  * ``session/prompt`` {sessionId, prompt:[{type:"text", text}]} ->
    **THIS RESPONSE IS THE TURN-END SIGNAL**: {stopReason:"end_turn" |
    "cancelled" | …}. A denied/aborted turn returns stopReason:"cancelled".
    [grok-pinned, cursor runtime-unverified]
  * ``session/list`` {} -> {sessions:[{sessionId, cwd?, <ts fields>?}, …]}.
    Enumerates cursor's restored on-disk sessions on RESUME (capability
    ``sessionCapabilities.list`` [cursor-verified]). Response shape + ordering
    are runtime-unverified (no authed login on this host — see replay_history);
    parsed tolerantly.
  * ``session/load`` {sessionId, cwd, mcpServers} -> {} AFTER cursor REPLAYS the
    loaded conversation as a burst of ``session/update`` notifications (the
    ``loadSession`` capability [cursor-verified]). Drives the resume-history
    backfill (replay_history). [ACP-spec-derived, cursor runtime-unverified]

Agent -> client NOTIFICATIONS (no `id`): ``session/update`` with
``params.update.sessionUpdate`` ∈:   [grok-pinned, cursor runtime-unverified]
  * ``agent_message_chunk`` — {update:{sessionUpdate, content:{type:"text",
    text}}}. Concatenate per turn -> the final answer (on_message).
  * ``agent_thought_chunk`` — same shape; reasoning, NOT folded into answer.
  * ``tool_call``          — {update:{sessionUpdate, toolCallId, title,
    rawInput, …}}.
  * ``tool_call_update``   — {update:{sessionUpdate, toolCallId, kind, title,
    content:[…], rawInput, status}}.
  * ``plan`` / ``available_commands_update`` / ``user_message_chunk`` and any
    vendor-prefixed notifications — passed through untouched to on_event.

Agent -> client REQUESTS (have `id` AND `method`, WE must respond):
  * ``session/request_permission`` {sessionId, toolCall:{toolCallId, kind,
    title, rawInput}, options:[{optionId, name, kind}]}. Option `kind` ∈
    {allow_once, allow_always, reject_once, reject_always}. ANSWER with
    ``result``:
       allow  -> {outcome:{outcome:"selected", optionId:<an allow_* option>}}
       deny   -> {outcome:{outcome:"selected", optionId:<a reject_* option>}}
                 or {outcome:{outcome:"cancelled"}} if no reject option.
    (Only appears when the client does NOT advertise the relevant capability;
    we advertise neither terminal nor fs write, so cursor runs its own tools
    and asks here — that is the permission gate seam.)
    [grok-pinned, cursor runtime-unverified]
  * ``terminal/create`` / ``fs/*`` — only if we advertise those capabilities
    (we do not); answered with a JSON-RPC method-not-found error defensively.
    [grok-pinned, cursor runtime-unverified]

Client -> agent CANCEL: ``session/cancel`` {sessionId} is a NOTIFICATION
(no `id`, no ack). It makes the in-flight ``session/prompt`` return
stopReason:"cancelled" — that response is the interrupt's completion signal.
[grok-pinned, cursor runtime-unverified]

Cursor-specific divergences from grok (for Task 1/2):
  * Subprocess is ``cursor-agent [--model M] [--force] acp`` — no
    ``--no-leader``/``stdio`` args; ``--force`` is the auto-approve analogue
    of grok's ``--always-approve`` (acceptance by the acp subcommand is
    runtime-unverified — fall back to answering session/request_permission
    allow-all client-side if rejected).
  * authMethods id is ``cursor_login`` (grok differs). [cursor-verified]
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


class CursorConversation:
    """Implements optio_agents.conversation.Conversation for Cursor (ACP)."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_label: str = "cursor",
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
        # ACP model block from session/new (see models.py). Captured at
        # bootstrap so the session can populate the picker without a separate
        # (auth-gated) `cursor-agent models` subprocess.
        self.session_models: dict | None = None
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
                "CursorConversation.attach: handle has no stdin writer; launch "
                "the subprocess with stdin=True"
            )
        self._handle = handle

    async def bootstrap(self) -> None:
        """Run the ACP handshake: ``initialize`` then ``session/new``.

        Requires ``run_reader()`` to already be running (it routes the
        responses back to the futures created here). We advertise NEITHER the
        terminal NOR fs-write client capability, so cursor executes its own
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
        result = (resp or {}).get("result") or {}
        self._session_id = result.get("sessionId")
        if not self._session_id:
            raise RuntimeError(
                f"cursor ACP session/new returned no sessionId: {result!r}"
            )
        models = result.get("models")
        if isinstance(models, dict):
            self.session_models = models
            self.current_model_id = models.get("currentModelId")

    async def reset_session(self) -> str | None:
        """Start a FRESH ACP session (drops the current session's chat context)
        without re-initializing. Used after the startup model probe so its
        throwaway "capital of Hungary" turns never leak into the operator's
        conversation. Returns the ABANDONED session id (so the caller can purge
        its on-disk records, which cursor persists under $HOME and would
        otherwise be snapshot-captured and rediscovered on resume). Best-effort:
        on failure the existing session is kept and None is returned."""
        old = self._session_id
        try:
            resp = await self._request("session/new", {
                "cwd": self._cwd,
                "mcpServers": self._mcp_servers,
            })
        except Exception:  # noqa: BLE001 — a reset failure just keeps the session
            _LOG.exception("cursor conversation: reset_session failed")
            return None
        result = (resp or {}).get("result") or {}
        sid = result.get("sessionId")
        if sid:
            self._session_id = sid
            models = result.get("models")
            if isinstance(models, dict):
                self.session_models = models
                self.current_model_id = models.get("currentModelId")
        return old if (old and old != self._session_id) else None

    async def replay_history(self, resume_session_id: str | None = None) -> str | None:
        """Backfill the event stream with the RESTORED conversation's prior turns.

        On resume the workdir tar restored cursor-agent's on-disk ACP session(s)
        under $HOME, but this run's ``bootstrap`` minted a FRESH ``session/new``,
        so the live ``on_event`` fan-out — and hence any listener's replay buffer
        — starts empty: a viewer attaching after a resume would see only NEW
        turns, never the prior conversation. ``session/load(id)`` makes cursor
        **replay** that conversation via ``session/update`` notifications, then
        return; those flow through the SAME ``_route`` → ``on_event`` path live
        turns use, landing in the listener's replay buffer automatically — so a
        late viewer reconstructs the full prior history exactly like live turns.

        WHICH id to load:
          * ``resume_session_id`` given (the snapshot PERSISTED cursor's ACP
            session id — the deterministic path): ``session/load`` it DIRECTLY,
            skipping the ``session/list`` heuristic below. This is the primary
            path (parity with every other resume engine, which all persist a
            session id): the heuristic can mispick an EMPTY session as they
            accumulate, and the persisted id is authoritative.
          * ``resume_session_id`` is ``None`` (old, pre-seam snapshots that
            recorded no id, or iframe captures): FALL BACK to the discovery
            heuristic — cursor advertises ``sessionCapabilities.list``
            [cursor-verified], so ``session/list`` enumerates the restored
            sessions and ``_select_prior_session_id`` picks the prior one
            (skipping the fresh session bootstrap just minted).

        On success the loaded session is ADOPTED (``self._session_id``) so the
        next prompt continues the prior thread, and any ``agent_message_chunk``
        text the replay accumulated is DROPPED — a replay is history, not a live
        turn, so it must not leak into the first real turn's ``on_message`` (a
        replay never fires ``on_message`` at all: ``session/load`` is a request,
        not a ``session/prompt``, so no turn-end handling runs).

        MANDATORY graceful fallback: if the persisted-id load errors, or the
        ``session/list`` path returns nothing loadable, or any call errors, keep
        the fresh ``session/new`` session and return ``None`` — resume must never
        break; it just shows no history in that case. A persisted-id load failure
        does NOT fall back to the list heuristic (the persisted id is
        authoritative). Returns the loaded session id, or ``None`` when the fresh
        session is kept.

        ORDERING is load-bearing (the caller's responsibility): this runs
        strictly AFTER ConversationListener subscribed to ``on_event`` (its
        constructor) — else the replayed events dispatch to nobody and the buffer
        misses the history — and BEFORE the resume-notice send (which continues
        the now-loaded thread). See session.py's ``if resuming:`` call site.

        NOTE [cursor runtime-unverified]: the ``session/list`` response shape and
        the id-selection heuristic could NOT be pinned without a live
        authenticated cursor login (the host is "Not logged in"); it is now only
        the pre-seam fallback. We parse tolerantly and pick most-recent.
        """
        if self._closed.is_set():
            return None
        if resume_session_id:
            # Deterministic fast path: the snapshot persisted the prior ACP
            # session id, so load it DIRECTLY — no session/list heuristic.
            return await self._load_and_adopt(resume_session_id)
        # Fallback (old snapshots recorded no id): discover the prior session via
        # session/list + the most-recent heuristic, then load it.
        try:
            listed = await self._request("session/list", {})
        except Exception:  # noqa: BLE001 — a resume backfill must never break resume
            _LOG.exception("cursor conversation: session/list failed; starting fresh")
            return None
        if not listed or listed.get("error"):
            _LOG.info(
                "cursor conversation: session/list unavailable (%s); starting fresh",
                (listed or {}).get("error"),
            )
            return None
        prior_id = self._select_prior_session_id(listed.get("result") or {})
        if not prior_id:
            return None
        return await self._load_and_adopt(prior_id)

    async def _load_and_adopt(self, prior_id: str) -> str | None:
        """``session/load(prior_id)`` — make cursor replay the prior conversation,
        then ADOPT the loaded session (``self._session_id``) so the next prompt
        continues that thread. Drops the replay's accumulated agent_message_chunk
        text (history, not a live turn — it must not prepend to the first real
        turn's answer). Returns the loaded id, or ``None`` on any error (the
        fresh ``session/new`` session is kept — resume never breaks)."""
        try:
            loaded = await self._request("session/load", {
                "sessionId": prior_id,
                "cwd": self._cwd,
                "mcpServers": self._mcp_servers,
            })
        except Exception:  # noqa: BLE001 — a resume backfill must never break resume
            _LOG.exception(
                "cursor conversation: session/load(%s) failed; starting fresh",
                prior_id,
            )
            return None
        if not loaded or loaded.get("error"):
            _LOG.info(
                "cursor conversation: session/load(%s) errored (%s); starting fresh",
                prior_id, (loaded or {}).get("error"),
            )
            return None
        self._session_id = prior_id
        self._answer_parts = []
        return prior_id

    def _select_prior_session_id(self, result: dict) -> str | None:
        """Pick the prior conversation's session id from a ``session/list``
        result, or ``None`` when there is nothing to load.

        Tolerant of the (runtime-unverified) response shape: sessions live under
        ``result["sessions"]`` (ACP's ListSessionsResponse), each an object
        carrying its id as ``sessionId`` (or ``id``) and optionally its ``cwd``.
        The fresh session ``bootstrap`` just minted (``self._session_id``) is
        skipped — loading it would replay nothing. Among the rest, prefer
        sessions rooted at THIS workspace when the entries carry a ``cwd``, then
        take the most-recent: by a timestamp field when all candidates carry one,
        else the LAST entry — a DOCUMENTED assumption that ``session/list``
        returns sessions oldest→newest, pending the live auth spike."""
        sessions = result.get("sessions")
        if not isinstance(sessions, list):
            return None
        entries = []
        for s in sessions:
            if not isinstance(s, dict):
                continue
            sid = s.get("sessionId") or s.get("id")
            if not sid or sid == self._session_id:
                continue      # skip non-ids and the fresh bootstrap session
            entries.append(s)
        if not entries:
            return None
        cwd = (self._cwd or "").rstrip("/")
        cwd_matches = [s for s in entries if (s.get("cwd") or "").rstrip("/") == cwd]
        pool = cwd_matches or entries

        def _ts(s):
            for key in ("updatedAt", "modifiedAt", "lastActiveAt", "createdAt", "timestamp"):
                if s.get(key) is not None:
                    return s[key]
            return None

        stamps = [_ts(s) for s in pool]
        best = pool[-1]
        if all(t is not None for t in stamps):
            try:
                # key on the stamp only (never compares the dicts on ties).
                best = max(zip(stamps, pool), key=lambda p: p[0])[1]
            except TypeError:
                best = pool[-1]   # non-comparable stamps → chronological fallback
        return best.get("sessionId") or best.get("id")

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
                    _LOG.warning("cursor conversation: unparseable line: %.200s", line)
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
                # cursor falls back to running the tool itself.
                asyncio.ensure_future(self._write_json({
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601,
                              "message": f"optio cursor client does not implement {method}"},
                }))
        elif method == "session/update":
            self._on_session_update(obj)
        # else: other agent notifications (plan, vendor-prefixed, …) — pass
        # through only.
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
            try:
                for handler in list(self._event_handlers):
                    await self._call_handler(handler, obj, "on_event")
            finally:
                self._event_queue.task_done()

    async def drain(self) -> None:
        """Block until every queued event has been dispatched to on_event.

        The resume replay backfill and the injected resume-notice reach
        subscribers ASYNCHRONOUSLY via ``_dispatch_loop``. The session awaits
        this after emitting them and before ``end_replay()`` so the listener's
        replay window reliably captures them ALL in the durable tier (no race
        where a late-dispatched replay event lands in the live ring instead)."""
        await self._event_queue.join()

    async def _call_handler(self, handler, arg, label: str) -> None:
        try:
            result = handler(arg)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — subscriber bugs never kill the driver
            _LOG.exception("cursor conversation: %s handler raised", label)

    def _fire_message(self, text: str) -> None:
        for handler in list(self._message_handlers):
            asyncio.ensure_future(self._call_handler(handler, text, "on_message"))

    # -- permission gate ----------------------------------------------------

    def _on_permission(self, obj: dict) -> None:
        if not self._permission_gate:
            _LOG.warning(
                "cursor conversation: session/request_permission received with "
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
            _LOG.exception("cursor conversation: permission handler raised; denying")
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
            raise RuntimeError("CursorConversation.send before bootstrap() completed")
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
        subscriber (the ConversationListener) and the reducer.

        Used at the replay→live boundary on resume: the resume notice is sent as
        a LIVE turn, but cursor echoes user turns as ``user_message_chunk`` only
        during a ``session/load`` replay, never live (wire-confirmed) — so without
        an injected event the last replayed answer stays pending, the resume
        answer merges into it, and the notice never renders. Emitting the
        ``user_message_chunk`` the reducer's boundary branch consumes finalizes
        the pending bubble, bumps the turn and renders the notice as an activity
        row."""
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
        """Push a session-control value change to the native transport
        (generalizes model selection). cursor exposes only the ``model``
        control; its change switches model mid-conversation INLINE via a
        ``session/set_model`` ACP request (no process restart) — grok's
        live-pinned mechanism, [grok-pinned, cursor runtime-unverified]; the
        method is present in the cursor binary. See models.py for the probe
        record + the restart-based fallback. Unknown control ids are no-ops."""
        if control_id != "model":
            return
        if self._closed.is_set():
            raise ConversationClosed(self._close_reason or "conversation closed")
        if self._session_id is None:
            raise RuntimeError(
                "CursorConversation.set_control before bootstrap() completed"
            )
        # Reuse the awaited session/set_model helper (also used by the startup
        # model probe); it updates current_model_id after the round-trip.
        await self.set_active_model(value)

    async def _set_model(self, model: str) -> None:
        try:
            await self._request("session/set_model", {
                "sessionId": self._session_id, "modelId": model,
            })
        except ConversationClosed:
            pass  # a swap racing the close is a no-op
        except Exception:  # noqa: BLE001 — never let a set_model bug kill the driver
            _LOG.exception("cursor conversation: session/set_model failed")

    async def set_active_model(self, model: str) -> None:
        """Await a ``session/set_model`` round-trip so the NEXT prompt uses
        ``model``. Used by the startup model probe (model_probe.probe_models)
        and by ``set_control("model", ...)`` for the interactive UI path."""
        await self._set_model(model)
        self.current_model_id = model

    async def close(self) -> None:
        self.close_requested.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def session_id(self) -> str | None:
        """The live ACP session id. After ``bootstrap()`` this is the
        ``session/new`` id; after a successful ``replay_history`` it is the
        ADOPTED prior id. Persisted in the resume snapshot so the next resume can
        ``session/load`` it directly (skipping the ``session/list`` heuristic)."""
        return self._session_id

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
