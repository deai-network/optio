# ClaudeCode Conversation UI (Phase II) — Design

This spec was written against the following baseline:

**Base revision:** `2d280d89d3337b17a608c070eb1bbbdd0fdf6c9e` on branch `feature/claudecode-conversation-gate` (as of 2026-06-10T06:55:00Z)

## Summary

Phase II of the conversation gate (`docs/2026-06-10-claudecode-conversation-gate-design.md`,
whose §11 feasibility check this spec realizes): surface a conversation-mode Claude Code
task in the dashboard. A per-task, strictly opt-in HTTP listener inside the engine
exposes the live `Conversation` as a transparent event stream plus send/interrupt/
permission endpoints; the existing widget proxy carries everything; a new
`optio-claudecode-ui` package ships the React chat widget; optio-dashboard registers it;
a new optio-demo task makes it manually testable end to end.

Ground rule inherited from optio: tasks are long-running background processes that may
or may not be monitored. Attaching/detaching a monitor never influences task state —
what a monitor sees is a projection.

## Decisions (settled during brainstorming)

1. **Replay semantics**: session-persistent. An engine-side replay buffer lets a viewer
   attach mid-session and see history; nothing is persisted to Mongo; after the task
   ends the conversation view is gone. (Claude Code's own transcript inside the
   snapshot remains the durable record; a post-mortem transcript viewer is a separate
   future feature.)
2. **Replay buffer vs live wire**: the buffer excludes high-frequency partial-text
   events; the live wire includes them, so an attached viewer sees answers flow as
   they are generated instead of a minutes-long spinner.
3. **No engine-side projection.** The engine channels raw Claude Code stream-json
   events through untouched; all interpretation happens client-side, in one place.
   The buffer's exclusion of partials is a mechanical type filter, not interpretation.
4. **Strictly opt-in.** The published `Conversation` object (Phase I) stays the default
   and only gate. The listener is a deliberate parallel path enabled per task by a new
   config flag, valid only in conversation mode.
5. **Controls scope**: chat + interrupt button + interactive permission approve/deny
   (the `permission_gate` flow surfaced in the browser).
6. **Send path**: per-task engine-side listener POSTs, reached through the existing
   widget proxy (the iframe-input precedent). No clamator RPC.
7. **API hop**: none. The widget proxy (`/api/widget/<db>/<prefix>/<processId>/*`)
   forwards all endpoints; GET = viewer role, POST = operator role, per existing
   optio-api auth rules. optio-api and optio-contracts are untouched.
8. **UI placement**: new package `optio-claudecode-ui`. optio-ui stays free of
   claude-specific knowledge and bloat; consumer apps that want this UI register the
   widget explicitly via optio-ui's public `registerWidget` extension point.

## 1. optio-claudecode: config + task surface

New field on `ClaudeCodeTaskConfig`:

```python
conversation_ui: bool = False
```

Validation (`__post_init__`, additive): `conversation_ui=True` requires
`mode="conversation"`.

When `conversation_ui=True`:

- The conversation argv additionally gets `--include-partial-messages` (live partial
  text) and `--replay-user-messages` (user turns echoed back on stdout — without it
  the stream, and therefore the buffer and the UI, would carry only the assistant
  side). Both flags exist in the current CLI (verified in Phase I V1 help output).
  Gated on the flag — in-process-only consumers don't pay for events nobody reads.
- After `ctx.publish_result(conversation)`, the task body starts the conversation
  listener (§2), registers it via `ctx.set_widget_upstream(url, inner_auth)` with a
  per-task random basic-auth credential, and calls `ctx.set_widget_data({})` (the UI's
  widget-live gate requires widgetData to be present).
- `create_claudecode_task` sets `ui_widget="claudecode-conversation"`.

When `False` (default): behavior is byte-identical to Phase I (`ui_widget=None` in
conversation mode, no listener, no extra argv flags).

Echo note: `--replay-user-messages` does not affect `on_message` (which fires only on
`result` events); echoed user turns appear as `type:"user"` events on the transparent
`on_event` stream only — exactly where the buffer and UI want them.

## 2. Conversation listener (new module, sibling of `input_listener.py`)

Runs in the engine process on the session's asyncio loop (it holds the live
`Conversation`; the projection principle is preserved — it only observes and forwards).
aiohttp app, bound per the same `OPTIO_WIDGET_TUNNEL_BIND` interface logic as the
existing input listener, OS-assigned port.

Endpoints (all behind the basic-auth inner credential injected by the widget proxy):

| Endpoint | Behavior |
|---|---|
| `GET /events` | SSE. On connect: replay buffer contents, then live tail. Each event's SSE `id:` is its monotonic `seq`; `Last-Event-ID` honored, so reconnects resume without duplicates. Live tail includes partial-text events; replay does not. |
| `POST /send` | `{text}` → `conversation.send(text)`. 409 when conversation closed. |
| `POST /interrupt` | `{}` → `conversation.interrupt()`. No-op when idle. |
| `POST /permission` | `{request_id, behavior: "allow"\|"deny", updated_input?, message?}` → resolves the pending permission future (below). 404 for unknown/already-answered request_id. |

Internals:

- Subscribes `conversation.on_event`; stamps every raw event with a monotonic `seq`.
- **Replay buffer**: `collections.deque(maxlen=1000)` of raw events. Mechanical type
  filter: events whose `type` is `stream_event` (the partial-message deltas) are
  forwarded live but never buffered. Everything else — `system`, `user`, `assistant`,
  `result`, `control_request`, `x-optio-*` — is buffered.
- **Permission wiring**: the listener registers the conversation's single
  `on_permission_request` handler. The handler stores the pending request (keyed by
  the control_request's `request_id`) and parks on a future; `POST /permission`
  resolves it with the corresponding `PermissionDecision`. The pending request is
  visible to viewers as the raw `control_request` event (replayed too, so a viewer
  attaching mid-question sees it); the answer is broadcast as a synthetic
  `{"type": "x-optio-permission-answered", "request_id": ..., "behavior": ...}`
  event (buffered) so any second viewer sees the card resolve. Bookkeeping, not
  interpretation.
- **Handler-slot rule (documented)**: `conversation_ui=True` occupies the single
  `on_permission_request` slot. Consumers that want programmatic permission gating
  must not enable `conversation_ui` (or must accept that the UI is the gate).
- Lifecycle: started right after `publish_result`; shut down in the session teardown
  bracket (runner cleanup like the existing input listener). `widgetUpstream` is
  already cleared at terminal state by the optio-core executor.
- SSE keep-alive: periodic comment line (`: ping`) every ~15 s so proxies don't time
  the stream out.

## 3. API hop: none

The widget proxy already forwards arbitrary subpaths, methods, and streaming
responses (it is WS-capable) to `widgetUpstream`, injecting the inner basic-auth
credential. Auth falls out of existing rules: `GET /events` needs viewer, the three
POSTs need operator.

**Implementation-time verification (V-phase, before UI work relies on it):** probe
that `@fastify/http-proxy` passes SSE through unbuffered (it proxies WS explicitly;
SSE is plain streaming HTTP). If it buffers, the fallback is a WebSocket `/events`
endpoint instead of SSE — proxy WS support is explicit, and the UI change is local
to the widget's transport hook.

## 4. optio-claudecode-ui (new TypeScript package)

- Exports `ClaudeCodeConversationWidget` (an optio-ui `WidgetComponent`) and a
  convenience `registerClaudeCodeConversationWidget()` that calls optio-ui's
  `registerWidget("claudecode-conversation", ClaudeCodeConversationWidget)`.
- Peer-deps: `optio-ui`, `react`, `antd` (same versions/policy as optio-ui). Build:
  `tsc`, matching optio-ui.
- All Claude-specific event interpretation lives here, client-side:
  - chat bubbles from `user` / `assistant` / `result` events;
  - dimmed activity rows from `tool_use` content blocks ("running Bash: …");
  - partial text from `stream_event` deltas flowing into the in-flight assistant
    bubble (replaced by the final text at `result`);
  - permission card on `control_request` (`subtype: can_use_tool`): tool name +
    input preview + Approve / Deny buttons → `POST /permission`; card resolves on
    `x-optio-permission-answered`;
  - busy indicator derived from the sent/result bracket; Interrupt button →
    `POST /interrupt`;
  - input box per IframeInputWidget conventions: never disabled, Enter submits,
    Shift+Enter newline, focus retained;
  - `x-optio-closed` renders a terminal "conversation ended" divider and disables
    sends.
- Transport: `EventSource` on `${widgetProxyUrl}events` (browser re-sends
  `Last-Event-ID` automatically on reconnect); `fetch` POSTs to
  `${widgetProxyUrl}send` / `interrupt` / `permission`. The widget proxy handles
  auth; the component never sees credentials.
- Styling: inline styles + antd, matching optio-ui conventions.

## 5. optio-dashboard

Registers the widget (import `optio-claudecode-ui`, call the register helper at app
startup). One-line workspace dependency addition. This is the manual test vehicle.

## 6. optio-demo

New demo task definition with:

```python
ClaudeCodeTaskConfig(
    consumer_instructions="",          # defaulted conversation prompt
    mode="conversation",
    conversation_ui=True,
    permission_gate=True,              # exercises the approve/deny UI
    credentials_json=...,              # per optio-demo's existing claudecode demo pattern
)
```

(Exact credential plumbing follows whatever the existing optio-demo claudecode task
does — resolved at plan time.)

## 7. Error handling

| Failure | Behavior |
|---|---|
| SSE client disconnects | Listener drops the subscriber; task unaffected (projection principle). |
| `POST /send` after conversation closed | 409 `{ok: false, reason: "closed"}`; widget shows "conversation ended". |
| `POST /permission` unknown/answered request_id | 404; widget refreshes card state from the event stream. |
| Listener startup failure | Task fails (conversation_ui was explicitly requested; silently degrading to no-UI would violate least surprise). |
| Buffer overflow (>1000 events) | Oldest entries drop (deque); a freshly attached viewer sees a truncated head — acceptable for a monitor. |
| Multiple viewers | All receive the stream; sends/interrupts/permission answers from any operator viewer are valid; permission race resolved first-write-wins, later answers get 404. |

## 8. Testing

- **optio-claudecode (pytest)**: listener unit tests against a fake `Conversation`
  (replay-then-live ordering, partial-event exclusion from buffer, `Last-Event-ID`
  resume, send/interrupt forwarding, permission roundtrip incl. second-answer 404,
  closed-conversation 409, basic-auth rejection). Session-level test with
  `conversation_ui=True` asserting argv flags, upstream registration, `ui_widget`,
  and listener teardown. Validation-matrix additions for the new flag.
- **optio-claudecode-ui**: build + typecheck; component-level tests only if optio-ui
  already has a JS test harness (checked at plan time — none was observed).
- **V-phase probe**: SSE-through-proxy buffering check (§3).
- **End-to-end**: manual, by the user, via optio-dashboard + the optio-demo task —
  the stated goal of this phase.

## 9. Out of scope

- Mongo persistence / post-mortem transcript viewer (future; durable record already
  exists in the snapshot's Claude transcript).
- opencode conversation UI (would be a sibling package interpreting opencode events).
- Per-task auth scoping in optio-api (host-app concern, unchanged).
- "Both" mode (iframe + conversation), unchanged from Phase I.
- Phase I's deferred items.

## 10. Package/file map

| Package | Change |
|---|---|
| optio-claudecode | `conversation_ui` flag + validation; argv flags; new `conversation_listener.py`; session wiring (start/register/teardown); AGENTS.md/README |
| optio-claudecode-ui | new package: widget component, register helper, package scaffolding |
| optio-dashboard | dependency + widget registration |
| optio-demo | new conversation-UI demo task |
| optio-api / optio-contracts / optio-ui / optio-core / optio-agents | **no changes** |
