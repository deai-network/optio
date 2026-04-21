# optio-opencode — Brainstorming Notes

**Date:** 2026-04-21
**Companion to:** `2026-04-21-optio-opencode-seed-spec.md`

Points raised during the brainstorming session that produced the seed spec but were intentionally not incorporated. Kept here for reference when the dedicated optio-opencode brainstorming session builds on the seed.

---

## Audit of opencode's web interface (as of 2026-04-21)

From an audit of `~/deai/opencode` at the time of this session. Treat as a point-in-time snapshot; verify before acting.

### Server (`opencode web`)

- Entry point: `packages/opencode/src/cli/cmd/web.ts:31-81` (`WebCommand` → `Server.listen`).
- Server implementation: `packages/opencode/src/server/server.ts:94-134`. Hono.js.
- Routes (non-exhaustive): `/` (static UI), `/event` (SSE, 10s heartbeat, `packages/opencode/src/server/routes/instance/event.ts:13,51-58`), `/session/*`, `/project/*`, `/permission/*`, `/question/*`, `/provider/*`, `/pty/*` (WebSocket upgrade), `/config/*`, `/mcp/*`, `/sync/*`, `/file/*`, `/tui/*`, `/global/*`, `/experimental/*`.
- Static assets: bundled at build time into `opencode-web-ui.gen.ts` (`packages/opencode/src/server/routes/ui.ts:10-11`). Proxies to `https://app.opencode.ai` if embedded UI missing.
- Auth: HTTP Basic via `OPENCODE_SERVER_PASSWORD` (middleware.ts:39-49). Alternative: `?auth_token=` query (middleware.ts:47). Username defaults to "opencode", customizable via `OPENCODE_SERVER_USERNAME`. No password → server is unsecured.
- CORS: `CorsMiddleware` (middleware.ts:68-82). Allows `http://localhost:*`, `http://127.0.0.1:*`, Tauri origins, `*.opencode.ai`. Custom origins via `Server.listen({cors: [...]})` (server.ts:101).
- State: request state in-memory. Session state persisted to SQLite via Drizzle ORM (`packages/opencode/src/session/*`).
- `OPENCODE_DISABLE_EMBEDDED_WEB_UI=true` lets the server skip serving the SPA (assets can be served elsewhere).

### Client (`packages/app`)

- Framework: SolidJS. Astro + Vite.
- Entry: `packages/app/src/entry.tsx:1-145`. Mounts to `#root`.
- **No public mountable component export** from `packages/app/src/index.ts`. Embedding as a React component is not viable without forking.
- Server URL: `location.origin` by default (entry.tsx:104); overridable via build-time `VITE_OPENCODE_SERVER_HOST` / `VITE_OPENCODE_SERVER_PORT`, or at runtime via `localStorage["opencode.settings.dat:defaultServerUrl"]` (entry.tsx:12, 52-53).
- Transport: fetch (via generated SDK), SSE (`globalSDK.event.listen()`), WebSocket (for `/pty`).
- Session header: `x-opencode-workspace`.
- Full-viewport CSS (`h-dvh w-screen`); not designed to adapt to a constrained container. Uses Notifications API, clipboard APIs, `window.open` (behaviors to expect when iframed).

### SDK (`packages/sdk/js`)

- `OpencodeClient`, `createOpencodeClient(config?)` in `src/v2/client.ts`.
- Generated from OpenAPI spec via `@hey-api/openapi-ts` (source in `src/v2/gen/`).
- Low-level REST only; no high-level session / conversation abstraction. A Python backend could call opencode's HTTP API directly without touching this SDK.

### Lifecycle

- `opencode web --port=<N>` runs indefinitely via `await new Promise(() => {})` (web.ts:78).
- URL printed to stdout (web.ts:48, 74); no structured "ready" marker.
- `Server.stop()` is awaitable. Explicit signal handling not visible in the code; Hono's Node adapter may handle SIGTERM.
- Sessions written to SQLite; location likely under XDG config dir (`xdg-basedir` in deps).

### Decision captured

Embedding as a React component is off the table. Only iframe embedding is viable.

---

## Architectural paths considered and rejected

### Network topology

- **Option 1 (rejected): direct browser-to-remote.** Remote opencode exposes its port to the browser (public / LAN / VPN). Rejected because the deployment cannot always guarantee browser-reachable remotes.
- **Option 2 (rejected): SSH tunnel from worker, exposing a port on the worker machine to the browser.** Simpler proxy story but adds CORS audits per opencode release, TLS per port, firewall complexity per session, token-in-URL leakage, ugly multi-origin URLs.
- **Option 3 (adopted): everything proxied through optio-api.** Single origin, single auth domain, no dynamic firewall config. Cost: optio-api proxies HTTP + SSE + WS.
- **Option 4 (deferred): support multiple topologies.** Not built in MVP; could be added if a deployment needs it.

### Connection-info channel

- **Option 1 (rejected): a dedicated typed `widgetConnection` field on the process doc.** Bakes "connection" concept into optio; mixes concerns.
- **Option 2 (rejected): ride the old spec's `extended-state` primitive with a single connection-info entry.** Pulls append-only mechanism into scope unnecessarily.
- **Option 3 (rejected): SSE-only widget-connection event with no persistence.** Needs reconnect-recovery logic.
- **Option 4 (rejected): pull-on-demand endpoint for widget connection.** Requires polling.
- **Adopted:** collapse into two orthogonal primitives — opaque `widgetData` (client-visible metadata) plus `widgetUpstream` (server-side network routing). See final spec.

### Collapsing widget data and widget upstream

Considered: could a single opaque `widgetData` primitive carry upstream info too (optio-api magic-interprets a key like `upstream`)?

Rejected because:

- Mixes application-layer data with network infrastructure.
- Creates implicit behavior keyed on string content.
- Breaks the opaque-blob contract.
- Grants the process ambient authority to register arbitrary upstreams by writing keys (SSRF risk).
- Forces the auth token to be client-visible to work (widget has to read it and put it in requests).

Keeping them separate lets the inner auth token live entirely server-side.

### Auth plane

- **Initial framing (corrected):** spec referenced "Better Auth" as optio-api's auth mechanism. This was wrong. optio-api exposes an `AuthCallback<TRequest>` in `packages/optio-api/src/auth.ts`; the host app supplies whatever auth it wants. Better Auth is what `optio-demo` happens to use.
- **Adopted:** widget proxy uses the same `AuthCallback` for outer auth. Inner auth is injected server-side per `InnerAuth` variant.

### Subpath URL routing

- **Option A (available): per-process subdomain** (e.g., `widget-<processId>.app.example.com`). Requires wildcard DNS + wildcard TLS cert. Clean but more infra.
- **Option B (adopted as default): localStorage override set by the parent before iframe mount.** Iframe is same-origin; parent's `localStorage.setItem('opencode.settings.dat:defaultServerUrl', …)` is visible to the iframe. No wildcard needed. Iframe widget's `localStorageOverrides` prop generalizes this for any embedded app with a similar runtime-URL convention.

### Adapter coverage

- **Initially proposed:** all four adapters (Fastify, Express, nextjs-pages, nextjs-app) via a shared core proxy module. Next.js app router is awkward for WS upgrade.
- **Adopted for MVP:** Fastify only. Express / Next.js deferred until a consumer needs them. Core proxy module stays framework-agnostic so later adapters plug in without restructuring.

### Proxy registry persistence

- **Option: in-memory + MongoDB mirror with a reconcile protocol.** More infra.
- **Adopted: lazy-load + in-memory cache, rebuilt on demand from MongoDB.** optio-api restart trivially recoverable — no reconciliation needed.

### Redis stream for worker → api notifications

- **Considered:** new `widget_upstream_set` / `widget_data_set` Redis command types on the existing stream, so optio-api gets near-real-time push.
- **Rejected (for MVP):** unnecessary. MongoDB write + lazy-load on first request is sufficient. Existing tree poller covers the update/invalidation case.

---

## Old (2026-04-10) spec — what was dropped and why

The `2026-04-10-windage-extension-points-spec.md` draft proposed five extensions. Three were dropped wholesale for this iteration:

- **§1 Extended process state** (`ctx.append_extended_state(entry)` + append-only `extendedState` array).
- **§2 Extended state retrieval** (`GET /api/processes/:prefix/:id/extended-state` with `from`/`to`/`last`).
- **§5 SSE extended-state deltas** (new `extended-state` event on the tree stream).

Reason: these were designed around a bespoke chat UI that the original windage-core + windage-ui would render. Since windage is now pivoting to embed opencode instead of rolling its own chat UI, the append-only entry stream has no known consumer. Opencode maintains its own SQLite-backed history; there is nothing for optio to store.

Additionally dropped:

- **§3 Process messaging** (`POST .../message`, `ctx.get_messages()`, `ctx.wait_for_message()`). Same reason: opencode handles message routing between its client and server internally; optio is not in the data path.

What was kept and reshaped:

- **§4 Custom UI widget registry.** Kept and generalized. `WidgetProps` shape trimmed (no more `sseConnection: EventSource`).

What is new:

- **Widget upstream proxy** (Primitive 2 in the final spec).
- **Widget data** as an opaque client-visible blob (Primitive 3).
- **Generic iframe widget** in optio-ui (Primitive 4).

---

## Scope of consumers — why three layers

Considered: should there be an optio-opencode layer at all, or is windage sufficient as the only consumer for now?

- **Argument for collapsing into windage** (YAGNI): only one user today; extract a reusable layer when a second consumer shows up.
- **Argument for splitting now (adopted):** boundary is easy to draw (opencode-specifics all sit in one Python helper); "embed opencode in my product" is plausibly a multi-consumer pattern; extraction cost is low.

Three layers accepted: optio / optio-opencode / windage.

---

## SSH library choice

Considered for Python-side SSH:

- **`asyncssh` (adopted):** pure-Python, native asyncio, built-in port forwarding (local + remote), SFTP client, subprocess exec. Best fit for optio-core's async codebase.
- **`paramiko` (rejected):** synchronous; requires threading to bridge into async.
- **`fabric` (rejected):** higher-level task runner over paramiko; too opinionated for library use.
- **Shell out to OS `ssh` (rejected):** works but error handling is string-parsing; worse observability.

asyncssh multiplexes a single SSH connection across:

- Command exec (install / launch / monitor / teardown) via `.create_process()` / `.run()`.
- Local port forward (the tunnel carrying browser → opencode traffic) via `.forward_local_port()`.
- SFTP (file transfer for system prompts, auth tokens, deliverables retrieval) via `.start_sftp_client()`.

---

## Shipping deliverables from opencode — candidates considered

For the question of how consumers harvest structured output from an opencode session:

1. Poll opencode's REST API (`GET /session/:id/messages`).
2. Subscribe to opencode's `/event` SSE stream from the worker.
3. Read opencode's SQLite database directly (SFTP-copy, or CLI query).
4. Use opencode's own CLI (`opencode session show <id>`) over SSH exec.
5. Push via a custom tool the LLM calls (requires opencode's tool/webhook extensibility).
6. SCP or SFTP to copy files back from the remote host over the existing SSH connection.

Listed in the seed as question 5 for the dedicated brainstorm.

---

## Interface style for optio-opencode — candidates considered

1. **Task factory.** `create_opencode_task(...) -> TaskInstance`.
2. **Base class / mixin.** Consumers subclass, override hooks.
3. **Runner + callbacks.** Generic runner + a config object + a small set of callbacks.

Left unresolved pending the dedicated brainstorm; choice depends on how windage consumes.

---

## Things not discussed but possibly worth revisiting

- How opencode's session auth (`OPENCODE_SERVER_PASSWORD`) should be generated and rotated between task instances.
- Whether multiple concurrent opencode tasks on the same worker / same remote need isolation beyond port separation (separate install dirs? separate session DBs?).
- Resource limits on the remote host (CPU / memory / disk caps during long exploration sessions).
- Logging / observability of opencode-side events back into optio's logs.
- Clean teardown semantics when the user closes the dashboard tab without cancelling the process (the process keeps running; should opencode auto-terminate after some idle period?).
