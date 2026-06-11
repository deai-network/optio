# Opencode Conversation Mode

This spec was written against the following baseline:

**Base revision:** `65753bdf838f0674cc652ed45362417205728140` on branch `feature/opencode-seed-save-back` (as of 2026-06-11T17:23:00Z)

> Baseline note: `feature/opencode-seed-save-back` is slated to merge to `main`
> before this feature lands; this branch (`csillag/opencode-frontend`) is based
> on its tip and will ultimately merge back to `main`.

## Summary

optio-claudecode can act as a **generic conversation gateway**: a task launched
with `mode="conversation"` hands the caller a live `Conversation` object
(send / events / permission gating / interrupt / close), and can optionally
serve the same conversation through a web widget. This spec brings the same
capability to optio-opencode, at full parity, built on opencode's headless
server.

Key architectural difference from the claudecode implementation: opencode
already ships a full HTTP+SSE server (`opencode serve` and `opencode web` run
the **same server**; `web` merely opens a browser). So instead of building an
optio-side listener web server, both the Python gateway and the conversation
widget are **clients of the opencode server the task already spawns**. No new
server component, no event translation at the gateway level.

## Goals

- `OpencodeTaskConfig(mode="conversation")` → caller-driven session returning a
  live `Conversation` (the shared protocol from optio-agents) via
  `ctx.publish_result()` / `launch_and_await_result()`.
- Optional conversation web UI (`conversation_ui=True`) rendered by a new
  engine-neutral **optio-conversation-ui** widget package that handles both
  the claudecode listener protocol and the opencode native protocol.
- Full parity with claudecode's conversation mode: `host_protocol` toggle,
  permission gating, tool-verbosity hint, resume/snapshot integration, SSH /
  remote support, hooks and seed machinery unchanged.

## Non-goals

- Support for opencode's "question" tool flow (multi-choice questions to the
  user). Conversation mode disables the tool (`"tools": {"question": false}`
  merged into the generated opencode config). Question support is deferred to
  a future feature; its natural design mirrors the permission machinery.
- Normalizing the *event stream* across engines. Events pass through
  engine-native; normalization happens in consumer layers (today: the widget's
  reducers). A future shared normalization layer can be built on `on_event`.
- Narrowing the widget proxy's path exposure (the widget can reach the whole
  opencode API through the proxy, same as iframe mode today). A path allowlist
  on the proxy is a possible future hardening, out of scope here.

## Architecture

### Layering: what is normalized, what passes through

The shared `Conversation` protocol (optio-agents) normalizes the **control
plane** and passes the **data plane** through raw:

- **Inbound, caller → engine (normalized verbs):** `send(text)`,
  `interrupt()`, `close()`, `PermissionDecision`. Each engine's implementation
  translates these to its native transport (claudecode: stream-json on stdin;
  opencode: HTTP POSTs). Only the common denominator is expressible — e.g.
  opencode's file attachments are not reachable through `send(text)`; if ever
  needed, they become an extension on the concrete `OpencodeConversation`
  class, not a widening of the shared protocol.
- **Outbound, engine → caller (raw):** `on_event` delivers unmodified native
  payloads (claudecode: stream-json dicts; opencode: `/event` SSE payloads).
  Subscribers must know which engine they listen to.
- **Two narrow semantic seams:** `on_message` (assistant-text extraction, one
  small per-engine function) and `on_permission_request` (native request
  wrapped into the shared `PermissionRequest`, which carries `raw`; the
  caller's `PermissionDecision` is mapped back to the native reply).

This matches claudecode's existing behavior exactly; opencode mirrors it.

### Approach: engine server as the single conversation backend

The opencode server the task already launches (password-authed, tunneled,
with a pre-created session) is the one backend for all three consumers:

```
                 ┌──────────────────────────── engine process ───────────────┐
                 │                                                            │
  caller ──────► │ OpencodeConversation (HTTP+SSE client) ──┐                 │
  (Conversation) │                                          ▼                 │
                 │                          opencode server subprocess        │
                 │                          (spawned by launch_opencode,      │
                 │                           OPENCODE_SERVER_PASSWORD)        │
                 │                                          ▲                 │
                 └──────────────────────────────────────────┼─────────────────┘
                                                            │ (tunnel if SSH)
  browser ── outer auth ──► optio-api widget proxy ── inner BasicAuth ──┘
  (conversation widget, native opencode API)
```

- **Python gateway:** `OpencodeConversation` implements the shared protocol as
  an HTTP+SSE client of the spawned server.
- **Widget:** the opencode server is registered directly as the widget
  upstream (`ctx.set_widget_upstream`) — exactly as iframe mode does today —
  and the conversation widget speaks opencode's native API through the proxy.
- **Auth:** unchanged from iframe mode. Inner auth: launch-generated secret in
  `OPENCODE_SERVER_PASSWORD`, injected by the widget proxy as
  `Authorization: Basic opencode:<password>`; the browser never sees it. Outer
  auth: whatever the consumer app enforces on optio-api routes (cookies flow
  with `EventSource`/`fetch`). The Python client uses the inner credential
  directly (localhost or SSH tunnel), never the proxy.

Rationale over the alternatives considered: copying claudecode's
`ConversationListener` into optio-opencode duplicates ~250 lines of listener
logic; extracting the listener to optio-agents adds an extra hop and a replay
buffer that duplicate persistence and streaming the opencode server already
provides. The engine already ships the conversation server; we use it.

### Relevant opencode server surface (shipped CLI)

All on the same server `opencode web` runs today
(`packages/opencode/src/server/` in the opencode repo):

| Operation | Endpoint |
|---|---|
| Live events (SSE) | `GET /event` |
| Send prompt (non-blocking) | `POST /session/{id}/prompt_async` |
| Interrupt | `POST /session/{id}/abort` |
| Answer permission | `POST /session/{id}/permissions/{permissionID}` |
| List pending permissions | permission group list endpoint |
| Message history | `GET /session/{id}/message` |

`auto_start` already uses `prompt_async`; the session is already pre-created
at launch and shared by all viewers.

## Python surface

### `OpencodeTaskConfig` additions

Defaults preserve current behavior exactly:

```python
mode: Literal["iframe", "conversation"] = "iframe"
host_protocol: bool = True       # optio.log keyword channel on/off
conversation_ui: bool = False    # register the conversation widget
tool_verbosity: ToolVerbosity = "description-only"  # widget rendering hint
```

Validation rules:

- `mode="iframe"` requires `host_protocol=True` (the keyword channel is iframe
  mode's only completion signal — same rule as claudecode).
- `conversation_ui=True` requires `mode="conversation"`.
- `auto_start` remains valid in both modes (kick off the AGENTS.md task, then
  converse).

**Deliberately no `permission_gate` flag.** Claudecode needs one because
headless claude only routes permission asks when launched with a special flag.
Opencode asks server-side whenever its own permission ruleset (in
`opencode_config`) says "ask" — there is nothing to enable at launch.
Capability parity holds: the caller gates permissions by registering
`on_permission_request`. Documentation must call out the stall hazard: if the
opencode permission config can ask and nobody can answer (no handler, no UI),
the session blocks on that request (`interrupt()`/`close()` remain the escape
hatch).

### `OpencodeConversation`

New module in optio-opencode implementing the shared `Conversation` protocol:

- Constructed at launch with `(worker_port, password, session_id)` — values
  the session body already produces.
- A reader task subscribes to `GET /event` (SSE, aiohttp) and dispatches every
  payload **raw** to `on_event` subscribers. The stream is server-wide; one
  server per task, no filtering.
- `send(text)` → `POST /session/{id}/prompt_async`;
  `interrupt()` → `POST /session/{id}/abort`;
  `close()` → cooperative shutdown (unblocks the session body → normal
  teardown including snapshot capture).
- `on_message`: extracts assistant text for the task's session from native
  events.
- `on_permission_request`: watches permission-request events, wraps them into
  the shared `PermissionRequest` (with `raw`), posts the resulting
  `PermissionDecision` to `POST /session/{id}/permissions/{permissionID}`.
  Single-handler-slot rule kept (last registration wins). Note: the widget
  answers via its own path (proxy → server), so a programmatic handler and the
  UI can coexist; the opencode server arbitrates — first answer wins, the
  loser gets a 4xx.
- Synthetic `x-optio-closed` event emitted to subscribers on teardown
  (claudecode parity).
- **No replay buffer on the object.** Opencode persists the session natively;
  claudecode's buffer lives in its listener, which this design doesn't have.

### Completion semantics

Identical to claudecode: a conversation-mode session ends when the caller
calls `close()`, or — when `host_protocol=True` — when the agent emits
DONE/ERROR on the keyword channel; whichever comes first.

## Session lifecycle integration

`run_opencode_session` keeps one code path with a mode fork at the driver
level, mirroring claudecode's structure:

- **Prepare/install/launch unchanged:** workdir, seeds (including save-back),
  claustrum, `launch_opencode`, tunnel, pre-created session — identical in
  both modes. The conversation transport rides the same `worker_port` +
  `password` that `auto_start` uses today.
- **Protocol driver:** `run_log_protocol_session(..., keywords=config.host_protocol)`
  — the keywords-off mode already exists in optio-agents. With
  `host_protocol=False` there is no tail dispatcher; completion is
  caller-driven only.
- **Prompt composition:** `compose_agents_md` gains the same adjustments
  claudecode's prompt got — omit the keyword-protocol documentation when
  `host_protocol=False`, soften the task framing in conversation mode (no
  "execute the task, then emit DONE"). The generated `opencode.json` gets
  `"tools": {"question": false}` merged in conversation mode.
- **Body:** instead of only `await proc.wait()`, the conversation body builds
  `OpencodeConversation`, calls `ctx.publish_result(conversation)` (so
  `launch_and_await_result` returns it), then waits on *either*
  close-requested *or* process exit. Premature server exit →
  `_SessionFailed`, as today. `close()` → graceful teardown: stop the SSE
  reader, terminate the subprocess, run the normal `finally` (snapshot
  capture, `after_execute`, seed save-back — untouched).
- **Widget registration (only when `conversation_ui=True`):** TaskInstance
  gets `ui_widget="conversation"`; `set_widget_upstream` unchanged from
  iframe mode; `set_widget_data` carries the conversation widget's contract
  (see UI section) instead of the iframe SPA fields.
- **Resume:** no new persistence. Opencode's session db lives in the workdir
  home and is already captured/restored by snapshots; `preserved_session_id`
  re-attaches to the same session; the existing resume-notice prompt still
  fires. The widget recovers history from the native message-history
  endpoint; a resumed `launch_and_await_result(resume=True)` hands the caller
  a fresh `Conversation` for the same underlying session. (No equivalent of
  claudecode's `.optio-conversation-buffer.json` is needed.)
- **Remote/SSH:** zero mode-specific code — the Python client and the widget
  proxy both reach the server through the already-established tunnel.
- **Hooks, deliverables, seed callbacks:** timing and semantics unchanged in
  both modes.

## UI: the `optio-conversation-ui` package

**One new package, one widget type, two protocol adapters.**

- New package `optio-conversation-ui` registers a single widget type
  `"conversation"` (replacing `"claudecode-conversation"`). TaskInstances on
  both engines emit the new type; dashboard and demo are updated in-repo;
  external consumers update on their next dependency bump.
- `optio-claudecode-ui` **remains in the repo as a frozen backup**: no longer
  consumed by anything in-repo, not kept in sync, no further publishes. It is
  deleted in a follow-up change once the new package is validated.

Internal split:

- **Shared presentation core** (the bulk, lifted from the existing widget):
  the normalized `ChatItem`/`ChatState` model and reducer plumbing, rendering
  (user/assistant bubbles, activity rows, ephemeral tool rows, permission
  cards, closed divider), auto-scroll, growing input with optimistic local
  echo, interrupt button, `toolVerbosity` handling. This presentation core is
  the layer where cross-engine event normalization lives.
- **Claudecode adapter:** today's transport and reducer unchanged —
  `EventSource({widgetProxyUrl}events)` with Last-Event-ID replay,
  `POST send/interrupt/permission`, stream-json → `ChatItem`.
- **Opencode adapter:** native API through the proxy — bootstrap history from
  `GET {widgetProxyUrl}session/{id}/message`, live events from
  `EventSource({widgetProxyUrl}event)`, send via
  `POST session/{id}/prompt_async`, interrupt via `POST session/{id}/abort`,
  permission answers via `POST session/{id}/permissions/{permissionID}`. Its
  reducer maps opencode events (text deltas, tool lifecycle, permission
  asks/replies, step/idle for `busy`) → the same `ChatItem` model. Reconnect
  = re-fetch history + resubscribe (the history endpoint is the replay; no
  Last-Event-ID needed).

**Protocol selection via `widgetData`:** the task declares
`{"protocol": "claudecode" | "opencode", ...}` plus protocol-specific fields —
opencode adds `sessionID` and `directory` (the task workdir; opencode's API
routes resolve their project instance from the request's location context,
e.g. the `x-opencode-directory` header, so the adapter must send it); both
carry `toolVerbosity`. The widget picks its adapter from
`widgetData.protocol`.

**"Session ended":** both adapters key off the process document's terminal
state (as `IframeWidget` does); claudecode additionally off its
`x-optio-closed` wire event — input disabled, closed divider shown.

## Error handling and edge cases

- **SSE drop (Python client):** the reader reconnects with backoff while the
  session is alive. Opencode's `/event` has no server-side replay, so a gap
  can lose events for `on_event` subscribers — the same exposure the opencode
  SPA has. Permission requests are gap-safe: on (re)connect the client lists
  pending requests via the native list endpoint and feeds unanswered ones to
  the handler. `on_message` consumers may miss a message during a gap;
  documented limitation (claudecode's stdio pipe cannot gap — this is the one
  place this design is weaker, mitigated by the pending-permission sweep).
- **Server dies mid-conversation:** the body's `proc.wait()` returns →
  `_SessionFailed`; the conversation emits `x-optio-closed` (reason: engine
  exited); pending Python-side permission futures resolve to deny — matching
  claudecode's shutdown semantics.
- **`send`/`interrupt` after close:** raise the protocol's standard
  closed-conversation error; the widget surfaces the closed state when its
  POSTs fail through the proxy.
- **Unanswerable permission stall:** documented hazard (see Python surface);
  `interrupt()`/`close()` always work as the escape hatch (abort cancels the
  pending step server-side).
- **Unparseable SSE payloads:** wrapped as `x-optio-unparseable` and passed
  through (claudecode parity); never crash the reader.
- **Widget reconnect races:** history-fetch-then-subscribe can drop events
  between the two calls; the adapter subscribes first, buffers, then fetches
  history and reconciles by message ID.
- **Multiple viewers:** N dashboards on one session already work (shared
  server session). Permission cards race benignly — first answer wins
  server-side; the others get a 4xx and mark the card answered.

## Testing

- **Python, fake-server based** — `tests/fake_opencode.py` grows the
  conversation surface it doesn't already fake: SSE `/event`, `prompt_async`,
  `abort`, permission list/reply. Against it, mirroring claudecode's suites:
  - `test_conversation_config` — validation matrix
    (`mode`/`host_protocol`/`conversation_ui`).
  - `test_conversation_driver` — `OpencodeConversation` unit behavior: verbs
    hit the right endpoints, raw passthrough on `on_event`, `on_message`
    extraction, permission wrap/reply, pending-permission sweep on reconnect,
    `x-optio-closed`, closed-conversation errors.
  - `test_conversation_session` — mode body integration: `publish_result`
    delivery, close-driven teardown, premature exit → `_SessionFailed`,
    keywords on/off, AGENTS.md composition differences,
    `tools.question=false` merge, snapshot capture still firing.
  - `test_conversation_ui_session` — `conversation_ui=True` wiring:
    `ui_widget="conversation"`, widgetData contents, upstream registration.
  - Resume: conversation-mode case added to the existing resume tests (same
    session ID re-attached; caller gets a working `Conversation` after
    resume).
- **UI (vitest), in `optio-conversation-ui`** — existing claudecode reducer
  tests port over unchanged (proves the absorption didn't regress); new
  opencode-adapter reducer tests driven by recorded native event fixtures
  (text deltas, tool lifecycle, permission ask/reply, busy derivation);
  transport tests with mocked fetch/EventSource for the
  history-then-subscribe reconciliation.
- **claudecode regression** — its TaskInstance now emits
  `ui_widget="conversation"`; existing claudecode conversation tests are
  updated for the renamed widget type only (no behavior change).
- **E2E** — optio-demo gains a conversation-mode opencode task (alongside the
  existing iframe demo), exercised by the demo smoke tests, consistent with
  the repo's "features verified in-repo via optio-demo" convention.
