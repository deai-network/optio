# Claude Code Human-Input Channel (Phase 1: in-session input listener)

This spec was written against the following baseline:

**Base revision:** `892a186105ebefb7b3cfaa0a1f127a66659d8997` on branch `main` (as of 2026-06-08T12:28:37Z)

## Problem

`optio-claudecode` exposes the running claude session through ttyd in an iframe. The
human reads output and types into the **same tmux pane** that the harness also writes to
via `send_text_to_claude` ("fake-typing": `set-buffer` → `paste-buffer` → sleep →
`send-keys Enter`, `host_actions.py:871`). There is no coordination between the two
writers — no lock, no line-clear, no draft snapshot.

Consequence: while the human is composing a multi-line message in claude's input box
(which can take a minute), a system message (deliverable ack, resume notice, etc.) pastes
into the **same** box and appends to the half-typed draft. The trailing `Enter` then
submits the mangled concatenation. The `"System: "` prefix (optio_agents
`context.py:14`) only disambiguates when messages land *separately*; under a collision it
does not help.

The root issue: **two uncoordinated writers share one TTY line-editor, and the human's
draft lingers in that shared editor for the entire composition window.**

## Goal

Eliminate the garbling by moving the human's draft **off** the shared tmux pane and
routing every injection through a **single serialized chokepoint** owned by the session.
The human composes in a client-side input box; on submit, the complete message is
delivered to the session and fake-typed as one atomic, serialized burst — never
interleaved with a system message.

This is **Phase 1**. **Phase 2** (separate spec) will add a proxy-level key filter that
*enforces* "no free-text in the terminal." Phase 1 relies on the operator self-limiting
terminal use to momentary control keys (Esc, Enter, arrows, 1–9, y/n), which do not
accumulate a lingering draft.

## Approach (chosen: D1 — in-session HTTP listener)

The session opens its own small HTTP listener (one route, `POST /input`), reached by the
browser **through the existing API widget proxy exactly as ttyd's port is**. Because the
listener runs **inside the session's own asyncio task (engine-side)**, its request handler
natively holds `host`, the `tmux_*` context, and the serialization lock — there is no
"context trapped in the task closure" problem, no engine↔session registry, no RPC codegen,
and no Mongo polling. The HTTP response provides a **synchronous delivery ack** the input
box can show.

### Why not the alternatives

- **A — Mongo-polled inbox:** introduces a second comms paradigm parallel to the existing
  control plane, no synchronous delivery ack, poll latency, and fiddly exactly-once drain.
- **C — clamator RPC (`sendAgentInput`):** idiomatic with `launch`/`cancel`/`dismiss`, but
  the live session's injector is trapped in the task closure; reaching it from the RPC
  handler requires a new per-process input-handler registry in the executor + contract
  codegen. More surface for the same outcome.
- **D2 — host-side listener (true sibling process to ttyd on the task host):** the purest
  mirror of ttyd, but to keep serialization it must become the *single* injector, forcing
  the system-message path to funnel through it and deploying a real listener process on the
  (possibly remote) host. Heavier; deferred/avoided.

D1 keeps "reach a session-opened port via the proxy" (the trusted ttyd data flow) while
keeping injection engine-side where `send_text_to_claude` already runs, so one in-process
lock serializes human + system writes with no extra deployment.

### Key divergence from the ttyd pattern (simplification)

ttyd runs **on the task host** (remote over SSH), so its port needs
`host.establish_tunnel` to be reachable from the api container (`session.py:306`). The D1
listener runs **engine-side**, already on the engine network where the api reaches ttyd's
tunneled `worker_port`. So the control listener **skips `establish_tunnel`**: it binds on
`bind_addr` (`OPTIO_WIDGET_TUNNEL_BIND`) and registers `http://{upstream_host}:{port}`
(`upstream_host` = `OPTIO_WIDGET_TUNNEL_HOST`) directly. Identical in local and SSH modes —
SSH-ness only affects `send_text_to_claude`, which is already engine-side.

## Deployment invariants (verified)

- The fastify-widget-proxy (`registerWidgetProxy`, optio-api `adapters/fastify.ts`) is
  always part of optio-api in dev, optio-demo, and Excavator. No external reverse proxy
  changes the `/api/*` path: Excavator fronts it with Caddy (`handle /api/*` →
  `reverse_proxy api:3000`, **no path rewrite**); dev uses a Vite `/api`→`:3000` proxy.
- UI is same-origin relative (`OptioProvider baseUrl=""`). All widget URLs are derived from
  `WidgetProps` (`apiBaseUrl` + `prefix` + `database` + `processId`). **The input box must
  build its POST URL the same way** — no absolute URLs, no env vars — so it is reachable by
  construction wherever the terminal iframe is, under Caddy, Vite, or the plain dashboard.
- In Excavator prod, `engine` and `api` are **separate containers** behind Caddy; the
  engine registers `widgetUpstream`/`controlUpstream` as engine-network URLs
  (`http://engine:PORT`) the api container reaches over the docker network. D1's listener
  binds on `bind_addr` (`0.0.0.0` in container, `127.0.0.1` local) accordingly.

## Components

### optio-claudecode (`session.py`, `host_actions.py`)

- **In-session listener.** After `launch_ttyd_with_claude`, start an **aiohttp**
  application with a single route `POST /input` (body `{text: string}`). Because the
  listener is always engine-side (never on the remote task host), it binds on `bind_addr`
  (`OPTIO_WIDGET_TUNNEL_BIND`: `0.0.0.0` in-container so the api container can reach it,
  `127.0.0.1` local) in **both** local and SSH modes. Allocate a free port; no
  `establish_tunnel`.
- **Register/clear upstream.** `await ctx.set_control_upstream("http://{upstream_host}:{port}")`
  after start; `await ctx.clear_control_upstream()` in the `finally` block, alongside the
  ttyd/tmux teardown (`teardown_session_tree`). Stop the aiohttp site/runner there too.
- **Serialization.** Introduce one `asyncio.Lock` in the session closure. The listener
  handler does `async with injection_lock: await host_actions.send_text_to_claude(host,
  tmux_path, tmux_socket, tmux_session, text)`. The existing `_agent_sender` (system path,
  `session.py:341`) is wrapped in the **same** lock. Human text is injected **raw** (no
  `"System: "` prefix).
- **Handler result.** Return `{ok: true}` on success; `{ok: false, reason}` with
  `reason ∈ {"send-failed"}` on tmux failure (`send_text_to_claude` raises). Map to HTTP
  200 / 502 respectively.
- **Dependency.** Add `aiohttp` to optio-claudecode `pyproject.toml`.
- **Widget selection.** Switch the task's `ui_widget` from `"iframe"` to `"iframe-input"`.

### optio-core (`store.py`, `context.py`)

- New per-process Mongo field **`controlUpstream`** with the same `{url, innerAuth}` shape
  as `widgetUpstream`. Store writers `update_control_upstream` / `clear_control_upstream`.
- `ProcessContext.set_control_upstream(url, inner_auth=None)` /
  `clear_control_upstream()`, mirroring `set_widget_upstream` (`context.py:232`).
- No inner auth (parity with ttyd, which uses none): the listener is loopback/engine-net
  only, reached solely via the authenticated proxy, and the proxy's `checkAuth` gates a
  `POST` as a write method → write role required.

### optio-api (`adapters/fastify.ts`, `widget-proxy-core.ts`)

- New **HTTP-only** route `POST /api/widget-control/<database>/<prefix>/<processId>`.
  Resolve `controlUpstream` from Mongo (reuse the `resolveWidgetUpstream` /
  `widget-upstream-registry` caching pattern), forward the body to the upstream's `/input`.
  No WebSocket — keeps this off the known-flaky WS proxy path.
- Auth: existing global `checkAuth` hook (write method → write role).
- Missing/absent `controlUpstream` (session gone) → 502/404.

### optio-ui (`widgets/`, `registry.ts`)

- New widget type **`iframe-input`** (registered in `widgets/registry.ts`), composing the
  existing iframe (top, display; reuse `IframeWidget`, no duplication) and a prose input
  (bottom): a `<textarea>` where **Enter submits, Shift+Enter inserts a newline**, plus a
  send button.
- On submit: `POST` to `/api/widget-control/<database>/<prefix>/<processId>` with
  `{text}`, URL derived from `WidgetProps` (relative). Disable while in flight. On success
  (`{ok:true}`): clear the box, show a transient "delivered." On `{ok:false}` / 502 /
  network error: keep the text, show "session not running" / "failed — retry."

## Data flow

```
input box ─POST /api/widget-control/<db>/<prefix>/<pid>─▶ api proxy ─▶ controlUpstream
   ▲ ack (delivered / not-running / failed)                          (engine:control_port)
   └─────────────────────────────────────────────────────────────────────────┘
                                                     in-session aiohttp /input handler
   system path: optio-agents send_to_agent ─▶ _agent_sender ─┐
                                                              ├─ injection_lock ─▶ send_text_to_claude ─▶ tmux
   human path:  aiohttp /input handler ──────────────────────┘
```

The terminal's momentary control keys (Esc/Enter/arrows/1–9/y/n) still reach tmux directly
through ttyd; accepted for Phase 1 (Phase 2 enforces the restriction at the proxy).

## Error handling

- **Listener handler:** holds the lock for the whole burst, so a human and a system message
  can never interleave. tmux failure → `{ok:false, reason:"send-failed"}` (502).
- **Proxy:** `controlUpstream` unresolved → 502/404; the widget surfaces "session not
  running" and retains the typed text.
- **Teardown:** `clear_control_upstream` runs in the `finally` block so late POSTs fail
  cleanly rather than hitting a dead port.
- **Listener lifecycle:** the aiohttp runner is started after ttyd and stopped in the same
  `finally` path; failure to start aborts the session like a ttyd-launch failure.

## Testing (TDD)

- **optio-claudecode:**
  - listener `/input` handler injects via `send_text_to_claude` under the shared lock;
  - **serialization test** — interleaved system (`_agent_sender`) and human (`/input`)
    sends produce **non-overlapping, ordered** `send_text_to_claude` calls (assert the lock
    serializes them; no interleave);
  - lifecycle: listener starts after ttyd, registers `controlUpstream`; teardown stops it
    and clears the upstream;
  - handler ack: success → `{ok:true}`; tmux failure → `{ok:false, reason:"send-failed"}`.
- **optio-core:** `set_control_upstream` / `clear_control_upstream` write/clear the Mongo
  field (mongodb-memory-server).
- **optio-api:** `POST /api/widget-control/...` forwards the body to the resolved upstream;
  auth enforced (write role); missing `controlUpstream` → error.
- **optio-ui:** `iframe-input` renders the iframe + input; submit POSTs the correct
  **relative** URL and `{text}`; Enter submits vs Shift+Enter newline; ack states render;
  error path retains the text.

## Scope / non-goals

- **Phase 2 (separate spec):** proxy key-filter enforcer (whitelist Esc/Enter/arrows/1–9/y/n
  on ttyd's INPUT frames, drop free-text) so terminal free-text is impossible, not merely
  discouraged.
- **Out of scope:** message history/echo panel (the terminal already shows delivered
  messages), typing indicators, multi-user presence, edit/recall, and opencode adoption of
  the same input channel (same pattern, a later effort).

## Affected packages (release order: deps first)

`optio-core` → `optio-api` → `optio-claudecode` → `optio-ui`. (optio-agents is untouched:
the system path still flows through `send_to_agent` → `_agent_sender`, which Phase 1 wraps
in the shared lock on the claudecode side.)
