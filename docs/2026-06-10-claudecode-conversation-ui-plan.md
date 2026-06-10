# ClaudeCode Conversation UI (Phase II) — Implementation Plan

> **For agentic workers:** This plan is **parallel-shaped**. Tasks 1–12 are file-disjoint and run concurrently (one agent per task). Agents make file changes ONLY — **no git, no test runs, no pnpm/pip installs** inside tasks. All verification and all commits happen in the final sequential phase (V1–V3), driven by the main loop. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface a conversation-mode Claude Code task in the dashboard: opt-in engine-side listener streaming raw events (replay + live) through the existing widget proxy, a new `optio-claudecode-ui` React widget (chat + interrupt + permission approve/deny), dashboard registration, and a demo task.

**Architecture:** optio-claudecode gains `conversation_ui` flag + a per-task aiohttp listener (SSE out via replay buffer + live tail; send/interrupt/permission POSTs in) registered as `widgetUpstream`. Zero optio-api/optio-contracts/optio-ui/optio-core changes. New TS package interprets raw claude events client-side.

**Spec:** `docs/2026-06-10-claudecode-conversation-ui-design.md` (binding). Phase I spec for the Conversation surface: `docs/2026-06-10-claudecode-conversation-gate-design.md`.

**Tech stack:** Python 3.11 asyncio + aiohttp (already a claudecode dep via input_listener), pytest; TypeScript/React 19 + antd, tsc + vitest (mirroring optio-ui).

---

## Execution protocol

1. Working branch `csillag/claude-code-conversion-ui` already exists and is checked out (base: `feature/claudecode-conversation-gate` @ `2d280d8`). No branch creation.
2. **Wave 1** — Tasks 1–12 in parallel; every file owned by exactly one task; tree may not build mid-wave.
3. **Wave 2** — V1–V3 sequential in the main loop: SSE-through-proxy probe, installs + full test pass + fixes, commits.

## Shared contracts (binding for all tasks)

```python
# optio_claudecode.types — new field on ClaudeCodeTaskConfig
conversation_ui: bool = False        # True requires mode="conversation"

# optio_claudecode.conversation_listener (new module)
class ConversationListener:
    def __init__(self, conversation, *, password: str) -> None
    async def start(self, bind_iface: str) -> int      # returns bound port
    async def stop(self) -> None
    # constants
BUFFER_MAXLEN = 1000
UNBUFFERED_TYPES = {"stream_event"}

# optio_claudecode.host_actions — extended signature (backward-compatible)
def build_conversation_argv(claude_path, *, claude_flags, permission_gate,
                            include_partial_messages: bool = False,
                            replay_user_messages: bool = False) -> list[str]
```

```typescript
// optio-claudecode-ui package, name "optio-claudecode-ui", version 0.1.0
export function ClaudeCodeConversationWidget(props: WidgetProps): JSX.Element
export function registerClaudeCodeConversationWidget(): void
// src/events.ts — pure reducer (testable without DOM)
export type ChatItem =
  | { kind: 'user'; text: string; seq: number }
  | { kind: 'assistant'; text: string; pending: boolean; seq: number }
  | { kind: 'activity'; text: string; seq: number }
  | { kind: 'permission'; requestId: string; toolName: string; input: unknown;
      answered: 'allow' | 'deny' | null; seq: number }
  | { kind: 'closed'; reason: string; seq: number };
export interface ChatState { items: ChatItem[]; busy: boolean; closed: boolean }
export const initialChatState: ChatState
export function reduceEvent(state: ChatState, ev: any, seq: number): ChatState
```

Wire facts (from Phase I, verified live): raw stream-json events `system`/`user`/`assistant`/`result`/`control_request`/`x-optio-*`; partials arrive as `{"type":"stream_event","event":{...content_block_delta...}}` when `--include-partial-messages` is set. Listener SSE frames: `id: <seq>\ndata: <raw event JSON>\n\n`. Synthetic listener event: `{"type":"x-optio-permission-answered","request_id":...,"behavior":...}`.

Widget endpoints via proxy: `${widgetProxyUrl}events|send|interrupt|permission` (widgetProxyUrl ends with `/`).

---

### Task 1: `conversation_ui` config flag

**Files:** Modify `packages/optio-claudecode/src/optio_claudecode/types.py`

- [ ] **Step 1:** Add field after `permission_gate` (keep comment style):

```python
    # Opt-in dashboard conversation UI: the task starts a per-task listener
    # (SSE event stream + send/interrupt/permission POSTs) and registers it
    # as widgetUpstream. The published Conversation object remains the
    # default gate; this is a deliberate parallel path. Conversation mode only.
    conversation_ui: bool = False
```

- [ ] **Step 2:** Append to `__post_init__`:

```python
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "ClaudeCodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
```

### Task 2: ConversationListener module

**Files:** Create `packages/optio-claudecode/src/optio_claudecode/conversation_listener.py`

- [ ] **Step 1:** Write the module. Mirror `input_listener.py`'s style (aiohttp, OS-assigned port, runner cleanup). Full implementation:

```python
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
import json
import logging
from collections import deque

from aiohttp import web

from optio_agents.conversation import ConversationClosed, PermissionDecision

_LOG = logging.getLogger(__name__)

BUFFER_MAXLEN = 1000
UNBUFFERED_TYPES = {"stream_event"}
PING_INTERVAL_S = 15.0


class ConversationListener:
    def __init__(self, conversation, *, password: str) -> None:
        self._conversation = conversation
        self._password = password
        self._seq = 0
        self._buffer: deque[tuple[int, dict]] = deque(maxlen=BUFFER_MAXLEN)
        self._subscribers: set[asyncio.Queue] = set()
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._runner: web.AppRunner | None = None
        self._unsubscribe = conversation.on_event(self._on_event)
        conversation.on_permission_request(self._on_permission_request)

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
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
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
        import base64
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
                    seq, event = await asyncio.wait_for(
                        queue.get(), timeout=PING_INTERVAL_S,
                    )
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
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
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, bind_iface, 0)
        await site.start()
        server = site._server  # aiohttp exposes the asyncio.Server here
        return server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._unsubscribe()
        for fut in self._pending_permissions.values():
            if not fut.done():
                fut.set_result(PermissionDecision(
                    behavior="deny", message="optio harness: session ending",
                ))
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
```

### Task 3: argv builder flags

**Files:** Modify `packages/optio-claudecode/src/optio_claudecode/host_actions.py`

- [ ] **Step 1:** Extend `build_conversation_argv` (backward-compatible defaults):

```python
def build_conversation_argv(
    claude_path: str, *, claude_flags: list[str], permission_gate: bool,
    include_partial_messages: bool = False,
    replay_user_messages: bool = False,
) -> list[str]:
```

After the base list construction, before the `permission_gate` block:

```python
    if include_partial_messages:
        out.append("--include-partial-messages")
    if replay_user_messages:
        out.append("--replay-user-messages")
```

Docstring addition: both flags are requested by `conversation_ui=True` — partials feed the live view; user-message replay puts the operator's turns on the stream so the replay buffer and UI carry both sides.

### Task 4: session wiring

**Files:** Modify `packages/optio-claudecode/src/optio_claudecode/session.py`

- [ ] **Step 1:** Imports: `from optio_claudecode.conversation_listener import ConversationListener` and `from optio_core.models import BasicAuth` (check: opencode's session imports BasicAuth from optio_core.models — same here).

- [ ] **Step 2:** In `run_claudecode_session`, alongside the existing conversation state vars, add `conv_listener: ConversationListener | None = None`.

- [ ] **Step 3:** In `_conversation_body`, pass the new argv knobs:

```python
        argv = host_actions.build_conversation_argv(
            claude_path, claude_flags=claude_flags,
            permission_gate=config.permission_gate,
            include_partial_messages=config.conversation_ui,
            replay_user_messages=config.conversation_ui,
        )
```

- [ ] **Step 4:** Immediately after `ctx.publish_result(conversation)`, add:

```python
        nonlocal conv_listener
        if config.conversation_ui:
            listener_password = secrets.token_urlsafe(32)
            bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
            upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
            conv_listener = ConversationListener(
                conversation, password=listener_password,
            )
            listener_port = await conv_listener.start(bind_addr)
            await ctx.set_widget_upstream(
                f"http://{upstream_host}:{listener_port}",
                inner_auth=BasicAuth(username="optio", password=listener_password),
            )
            await ctx.set_widget_data({})
            ctx.report_progress(None, "Conversation UI is live")
```

(`secrets` is already imported in this module — verify; if not, add. The listener runs in the engine process like the input listener; no SSH tunnel involved regardless of host type.)

- [ ] **Step 5:** Teardown — in the session `finally`, next to the existing input-runner cleanup pattern:

```python
        if conv_listener is not None:
            try:
                await conv_listener.stop()
            except Exception:
                _LOG.exception("conversation listener cleanup failed")
```

- [ ] **Step 6:** `create_claudecode_task`: widget per mode/flag:

```python
        ui_widget=(
            "iframe-input" if config.mode == "iframe"
            else ("claudecode-conversation" if config.conversation_ui else None)
        ),
```

- [ ] **Step 7 (handler-slot rule):** in `_conversation_body`, the listener construction registers `on_permission_request` (done inside `ConversationListener.__init__`). Add a comment at the construction site: `# conversation_ui occupies the single permission-handler slot (see design §2).`

### Task 5: listener unit tests

**Files:** Create `packages/optio-claudecode/tests/test_conversation_listener.py`

Use a fake conversation (no engine, no Mongo): an object with `on_event(h)->unsub`, `on_permission_request(h)`, async `send/interrupt` recording calls (raising `ConversationClosed` when told), and a `fire(event)` helper invoking the registered handler. Drive HTTP with `aiohttp.ClientSession` against `127.0.0.1:<port>` from `listener.start("127.0.0.1")`.

- [ ] **Step 1:** Write tests (auth header helper: `base64("optio:" + pw)`):

```python
"""ConversationListener unit tests against a fake Conversation."""

import asyncio
import base64
import json

import aiohttp
import pytest

from optio_agents.conversation import ConversationClosed, PermissionDecision
from optio_claudecode.conversation_listener import ConversationListener


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None
        self.sent = []
        self.interrupts = 0
        self.closed = False

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    async def send(self, text):
        if self.closed:
            raise ConversationClosed("closed")
        self.sent.append(text)

    async def interrupt(self):
        if self.closed:
            raise ConversationClosed("closed")
        self.interrupts += 1

    def fire(self, event):
        for h in list(self.handlers):
            h(event)


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
async def listener():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    yield conv, lst, f"http://127.0.0.1:{port}"
    await lst.stop()


async def _read_events(resp, n, timeout=5):
    """Parse n SSE data frames from an open aiohttp response."""
    out = []
    buf = b""
    async def _go():
        nonlocal buf
        while len(out) < n:
            chunk = await resp.content.read(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if data:
                    out.append(json.loads(b"".join(data).strip()))
    await asyncio.wait_for(_go(), timeout)
    return out


async def test_replay_then_live_and_partial_exclusion(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})
    conv.fire({"type": "stream_event", "n": "partial"})  # live-only
    conv.fire({"type": "result", "n": 2})
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/events", headers=_auth("pw")) as resp:
            assert resp.status == 200
            replay = await _read_events(resp, 2)
            assert [e["type"] for e in replay] == ["user", "result"]  # no partial
            conv.fire({"type": "stream_event", "n": "live-partial"})
            live = await _read_events(resp, 1)
            assert live[0]["type"] == "stream_event"  # partials DO flow live


async def test_last_event_id_resume(listener):
    conv, lst, url = listener
    conv.fire({"type": "user", "n": 1})    # seq 1
    conv.fire({"type": "result", "n": 2})  # seq 2
    async with aiohttp.ClientSession() as s:
        headers = {**_auth("pw"), "Last-Event-ID": "1"}
        async with s.get(f"{url}/events", headers=headers) as resp:
            events = await _read_events(resp, 1)
            assert events[0]["n"] == 2  # seq 1 skipped


async def test_send_interrupt_forwarding_and_closed(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/send", json={"text": "hi"}, headers=_auth("pw"))
        assert r.status == 200 and conv.sent == ["hi"]
        r = await s.post(f"{url}/interrupt", json={}, headers=_auth("pw"))
        assert r.status == 200 and conv.interrupts == 1
        conv.closed = True
        r = await s.post(f"{url}/send", json={"text": "x"}, headers=_auth("pw"))
        assert r.status == 409


async def test_permission_roundtrip_and_second_answer_404(listener):
    conv, lst, url = listener

    class Req:
        raw = {"request_id": "perm-1"}
        tool_name = "Bash"
        input = {}

    task = asyncio.create_task(conv.perm_handler(Req()))
    await asyncio.sleep(0.05)
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "allow"},
                         headers=_auth("pw"))
        assert r.status == 200
        decision = await asyncio.wait_for(task, 2)
        assert decision.behavior == "allow"
        r = await s.post(f"{url}/permission",
                         json={"request_id": "perm-1", "behavior": "deny"},
                         headers=_auth("pw"))
        assert r.status == 404
    # answered broadcast landed in the buffer
    assert any(e.get("type") == "x-optio-permission-answered"
               for _, e in lst._buffer)


async def test_auth_rejected(listener):
    conv, lst, url = listener
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{url}/events", headers=_auth("WRONG"))
        assert r.status == 401
        r = await s.post(f"{url}/send", json={"text": "x"})
        assert r.status == 401
```

### Task 6: session-level + validation tests

**Files:** Create `packages/optio-claudecode/tests/test_conversation_ui_session.py`

- [ ] **Step 1:** Validation matrix (pure unit): `conversation_ui=True` + `mode="iframe"` raises; `conversation_ui=True` + conversation mode OK; default False.

- [ ] **Step 2:** argv assertions (pure unit): `build_conversation_argv(..., include_partial_messages=True, replay_user_messages=True)` contains both flags; defaults omit them.

- [ ] **Step 3:** Session integration (same bootstrap as `tests/test_conversation_session.py` — real engine, claude shim): launch a task with `mode="conversation"`, `conversation_ui=True`, `permission_mode="bypassPermissions"`; via `launch_and_await_result` obtain the conversation; then poll the process doc until `widgetUpstream` is set; assert: `widgetUpstream.url` non-empty + `innerAuth` present, `widgetData == {}`, `uiWidget == "claudecode-conversation"` on the TaskInstance/process; GET the listener's `/events` directly (URL from the doc, auth from… the doc's innerAuth is stored in Mongo — read it in the test) and assert the SSE replay contains the `system/init` event; `close()` → task done; after terminal, the listener port refuses connections (listener stopped). Follow the conftest fixtures used by `test_conversation_session.py` verbatim — do not invent a new bootstrap. Write the full test code following those conventions.

### Task 7: optio-claudecode-ui scaffolding

**Files:** Create `packages/optio-claudecode-ui/package.json`, `tsconfig.json`, `vitest.config.ts`, `README.md`, `src/index.ts`

- [ ] **Step 1:** `package.json` (mirror optio-ui's source-shipping style):

```json
{
  "name": "optio-claudecode-ui",
  "version": "0.1.0",
  "license": "Apache-2.0",
  "description": "React conversation widget for optio-claudecode conversation-mode tasks.",
  "repository": { "type": "git", "url": "git+https://github.com/deai-network/optio.git", "directory": "packages/optio-claudecode-ui" },
  "author": "Kristof Csillag <kristof.csillag@deai-labs.com>",
  "type": "module",
  "main": "src/index.ts",
  "types": "src/index.ts",
  "exports": { ".": "./src/index.ts" },
  "files": ["src", "!src/__tests__", "README.md"],
  "scripts": { "build": "tsc", "test": "vitest run" },
  "dependencies": {
    "optio-ui": "workspace:*"
  },
  "peerDependencies": { "react": ">=18", "react-dom": ">=18", "antd": ">=5" },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "antd": "^5.29.3",
    "jsdom": "^29.0.1",
    "react": "^19.2.4",
    "react-dom": "^19.2.4",
    "typescript": "^5.7.0",
    "vitest": "^3.2.4"
  }
}
```

- [ ] **Step 2:** `tsconfig.json` — copy optio-ui's verbatim (extends base, jsx react-jsx, noEmit). `vitest.config.ts` — copy optio-ui's if present (check `packages/optio-ui/vitest.config.*`; replicate environment jsdom setup); if optio-ui has none, minimal `{ test: { environment: 'jsdom' } }` config.

- [ ] **Step 3:** `src/index.ts`:

```typescript
export { ClaudeCodeConversationWidget, registerClaudeCodeConversationWidget } from './ClaudeCodeConversationWidget.js';
export { reduceEvent, initialChatState } from './events.js';
export type { ChatItem, ChatState } from './events.js';
```

- [ ] **Step 4:** `README.md` — what it is, registration snippet (`registerClaudeCodeConversationWidget()` at app startup), pointer to the Phase II design doc, note that the python task needs `mode="conversation"` + `conversation_ui=True`.

### Task 8: event reducer

**Files:** Create `packages/optio-claudecode-ui/src/events.ts`

- [ ] **Step 1:** Implement the reducer per the shared contract. Interpretation rules (all claude-specific thinking lives here):

```typescript
// Raw stream-json event -> ChatState transition.
// - {type:"user"}: text blocks of message.content -> user bubble. Skip
//   harness messages (text starting "System: ") -> activity row instead.
// - {type:"assistant"}: for each content block: text -> upsert the pending
//   assistant bubble's text; tool_use -> activity row `running ${name}: ...`
//   (one-line input preview, JSON.stringify sliced to 120 chars).
// - {type:"stream_event"}: event.event?.delta?.text (content_block_delta)
//   appends to the pending assistant bubble (creating it if absent).
// - {type:"result"}: finalize pending bubble with .result text (if string),
//   busy=false.
// - {type:"control_request", request:{subtype:"can_use_tool"}}: permission
//   card (answered: null), busy stays true.
// - {type:"x-optio-permission-answered"}: mark matching card answered.
// - {type:"x-optio-closed"}: closed item + closed=true.
// - sending a user message sets busy=true (handled by the widget on POST,
//   and reinforced when the echoed user event arrives).
// - everything else: ignored.
```

Write the full implementation (~120 lines, pure functions, no React imports).

### Task 9: the widget component

**Files:** Create `packages/optio-claudecode-ui/src/ClaudeCodeConversationWidget.tsx`

- [ ] **Step 1:** Implement. Structure (follow IframeInputWidget conventions for the input row — `packages/optio-ui/src/widgets/IframeInputWidget.tsx` is the reference):

```typescript
import { useEffect, useReducer, useRef, useState } from 'react';
import type { WidgetProps } from 'optio-ui';
import { registerWidget } from 'optio-ui';
import { initialChatState, reduceEvent } from './events.js';

export function ClaudeCodeConversationWidget(props: WidgetProps) {
  // EventSource on `${props.widgetProxyUrl}events` (browser handles
  // Last-Event-ID on auto-reconnect). Each message: JSON.parse(ev.data),
  // dispatch({ ev: parsed, seq: Number(ev.lastEventId) }).
  // POST helpers via fetch to `${props.widgetProxyUrl}send|interrupt|permission`.
  // Render: scrollable column of items (auto-scroll to bottom on append
  // unless the user scrolled up), busy spinner row while busy, permission
  // cards with Approve/Deny buttons (antd Button), Interrupt button enabled
  // while busy, textarea input row exactly per IframeInputWidget (never
  // disabled, Enter submits, Shift+Enter newline, refocus after send,
  // data-testid attributes: conversation-input-box / conversation-send).
  // closed state: divider "conversation ended", input disabled.
}

export function registerClaudeCodeConversationWidget(): void {
  registerWidget('claudecode-conversation', ClaudeCodeConversationWidget);
}
```

Write the full component (~200 lines). Styling: inline styles consistent with optio-ui (user bubbles right-aligned light-blue, assistant left-aligned white with 1px border, activity rows dimmed monospace, permission card bordered amber).

### Task 10: UI tests

**Files:** Create `packages/optio-claudecode-ui/src/__tests__/events.test.ts`, `packages/optio-claudecode-ui/src/__tests__/widget.test.tsx`

- [ ] **Step 1:** `events.test.ts` — pure reducer tests: user event → user bubble; System:-prefixed user → activity; assistant text + result → finalized bubble, busy false; stream_event deltas accumulate into pending bubble then replaced by result text; tool_use → activity row; control_request → permission card; x-optio-permission-answered → card answered; x-optio-closed → closed. Write full code (table-driven, ~80 lines).

- [ ] **Step 2:** `widget.test.tsx` — component smoke test with a mocked EventSource (assign `globalThis.EventSource` to a class capturing the instance; fire synthetic messages): renders user+assistant bubbles after events, send button POSTs (mock `fetch`, assert URL `${widgetProxyUrl}send` and body), permission card Approve click POSTs `/permission` with the request_id. Use @testing-library/react. Write full code.

### Task 11: dashboard + demo wiring

**Files:** Modify `packages/optio-dashboard/package.json`, `packages/optio-dashboard/src/app/App.tsx`, `packages/optio-demo/src/optio_demo/tasks/claudecode.py`

- [ ] **Step 1:** dashboard `package.json` dependencies: add `"optio-claudecode-ui": "workspace:*"`.

- [ ] **Step 2:** `App.tsx`: add import + one registration call at module scope (next to the existing optio-ui imports, line ~13):

```typescript
import { registerClaudeCodeConversationWidget } from 'optio-claudecode-ui';
registerClaudeCodeConversationWidget();
```

- [ ] **Step 3:** optio-demo `claudecode.py`: in the per-seed loop (after the existing seed-pinned demo task append, `claudecode.py:196-221`), append a conversation-UI variant:

```python
        tasks.append(
            create_claudecode_task(
                process_id=f"claudecode-conversation-seed-{seed_id}",
                name=f"Claude Code conversation — {name}",
                description=(
                    "Conversation-mode Claude Code session from a captured "
                    f"seed ({name}): chat with the agent in the dashboard, "
                    "approve tool permissions interactively."
                ),
                config=ClaudeCodeTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    permission_gate=True,       # exercises the approve/deny UI
                    host_protocol=False,        # pure conversation gate
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )
```

### Task 12: docs

**Files:** Modify `packages/optio-claudecode/AGENTS.md`, `packages/optio-claudecode/README.md`

- [ ] **Step 1:** AGENTS.md, Conversation mode section: document `conversation_ui` (opt-in listener, endpoints table, replay-buffer semantics, handler-slot rule, the two extra argv flags, `ui_widget="claudecode-conversation"`), pointer to the Phase II design doc and to `optio-claudecode-ui`.

- [ ] **Step 2:** README.md: one short paragraph + config snippet in the Conversation mode section.

---

## Wave 2 — sequential (main loop)

### V1: SSE-through-proxy probe

- [ ] **Step 1:** Throwaway script (not committed): tiny Node script starting (a) an SSE origin server emitting one event per second, (b) fastify + the optio-api widget proxy registration pointing at it (reuse `packages/optio-api` exports; a stub Mongo lookup via the registry's injectable pieces — see `widget-proxy-core.ts`; if stubbing is awkward, run the real thing against a Mongo doc). `curl -N` through the proxy; verify events arrive incrementally (≤2s cadence), not buffered.
- [ ] **Step 2:** If buffered: file the finding, switch listener `GET /events` + widget transport to WebSocket (proxy WS support explicit) — this is the spec's named fallback; adjust Tasks 2/9 code accordingly before the test pass.

### V2: installs + full test pass

- [ ] **Step 1:** `pnpm install` (new workspace package), confirm `pnpm-workspace.yaml` glob covers `packages/*` (it does — verify).
- [ ] **Step 2:** `cd packages/optio-claudecode && <venv-python> -m pytest tests/ -q` — green (incl. new listener + ui-session tests; Mongo via Docker).
- [ ] **Step 3:** `cd packages/optio-claudecode-ui && pnpm build && pnpm test` — typecheck + vitest green.
- [ ] **Step 4:** `cd packages/optio-dashboard && node_modules/.bin/tsc -p . --noEmit || pnpm build` — dashboard typechecks with the new dep (use the package's own build script).
- [ ] **Step 5:** `cd packages/optio-ui && pnpm test` — unchanged-green (no edits expected; guard).
- [ ] **Step 6:** Fix Wave-1 breakage inline per Shared contracts.

### V3: commits (main loop only)

- [ ] **Step 1:** `git add packages/optio-claudecode && git commit -m "feat(claudecode): opt-in conversation UI listener (SSE replay + live, permission gate)"`
- [ ] **Step 2:** `git add packages/optio-claudecode-ui && git commit -m "feat(claudecode-ui): conversation widget package"`
- [ ] **Step 3:** `git add packages/optio-dashboard packages/optio-demo docs/ && git commit -m "feat(dashboard,demo): register conversation widget + demo task"`
- [ ] **Step 4:** Report to user for manual end-to-end testing (dashboard + demo task). **No merge, no branch finishing — user tests first.**

---

## Self-review notes (applied)

- Spec coverage: §1→Tasks 1,3,4; §2→2,5; §3→V1; §4→7-10; §5/§6→11; §7→2,5; §8→5,6,10,V2; docs→12. No gaps.
- File-disjoint: types/listener/host_actions/session each single-owner; dashboard+demo combined in Task 11 (three files, one owner); new package files all in Tasks 7-10 with no overlap (index.ts in 7, events.ts in 8, widget in 9, tests in 10).
- Contract consistency: `ConversationListener(conversation, password=)`/`start(bind_iface)->port`/`stop()` used identically in Tasks 2/4/5; reducer API identical in Tasks 8/9/10.
