# Optio Widget Extensions — Design Specification

**Base revision:** `aa1c234796be6096c66031f094be57f8b6223a9e` on branch `main` (as of 2026-04-21T01:47:06Z)

**Date:** 2026-04-21
**Status:** Draft

---

## Overview

This spec defines the optio primitives needed to support processes whose UI is a live-running web application embedded in the optio dashboard. The primary driver is optio-opencode (launching `opencode web` either locally or on a remote host and embedding its web client), but the primitives are generic: any process that exposes an HTTP + SSE + WS web app can use them.

## Architecture Context

Three-layer architecture (for reader orientation):

- **optio** — generic process orchestration. Owns the primitives defined in this spec.
- **optio-opencode** — reusable Python helper that orchestrates `opencode web` as either a local subprocess or via SSH on a remote host; consumes optio's primitives. Sketched in a companion seed spec; fully designed in a separate brainstorming session.
- **windage and other end consumers** — compose optio-opencode with domain-specific behavior (e.g., system prompts, output harvesting). Out of scope here.

## Scope

**In scope for this spec:**

1. Widget registry — a process declares a UI widget name; optio-ui renders the registered component.
2. Widget upstream proxy — optio-api proxies HTTP + SSE + WS on `/api/widget/<database>/<prefix>/<processId>/*` to a worker-registered upstream URL, injecting inner auth server-side.
3. Widget data — a process publishes an opaque JSON blob on its process document; optio-ui delivers it to the widget live.
4. Generic iframe widget — shipped in optio-ui as a registered widget. Mounts an iframe to the proxied URL with configurable pre-mount setup.
5. Cross-cutting: auth, URL-routing strategy for embedded apps, lifecycle, failure handling.
6. Reference task: a local `marimo edit` task in optio-demo that exercises all primitives end-to-end.

**Out of scope for this spec:**

- Express, nextjs-pages, and nextjs-app adapter support for the widget proxy. MVP ships Fastify only.
- optio-opencode's internal design (separate seed + brainstorming).
- Health-probing, reconnection, or retry for upstreams. These live with the worker / consumer.
- App-to-app `postMessage` between iframe and parent dashboard. Add when a consumer needs it.
- Per-widget fine-grained authorization beyond optio-api's existing viewer/operator distinction.

---

## Primitive 1: Widget Registry

### Python side (optio-core)

New optional field on `TaskInstance`:

```python
TaskInstance(..., ui_widget: str | None = None)
```

Stored on the process document as `uiWidget`. Added to the process schema in `packages/optio-contracts` so it surfaces in API responses and SSE tree events.

### Client side (optio-ui)

A module-level registry with a public API:

```typescript
import { registerWidget, type WidgetProps } from '@optio/ui'

registerWidget('opencode-iframe', OpencodeIframeWidget)
```

`WidgetProps` shape:

```typescript
interface WidgetProps {
  process: Process            // full process document (includes widgetData)
  apiBaseUrl: string          // e.g., "https://app.example.com"
  widgetProxyUrl: string      // derived: `${apiBaseUrl}/api/widget/${encodeURIComponent(database)}/${encodeURIComponent(prefix)}/${process._id}/` (trailing slash load-bearing — see Primitive 2 subpath notes)
  prefix: string
  database: string
}
```

### Rendering host

A new component `ProcessDetailView` in optio-ui (no such component exists today — it is a prerequisite of this spec). It is used in place, not as a URL route: the current optio-dashboard has no router and selects processes via React state. `ProcessDetailView` replaces the inline `<ProcessTreeView /> + <ProcessLogPanel />` render in the right pane of the dashboard's layout.

Exact prop shape (self-fetching vs. data-in-props) is an implementation detail deferred to the plan phase; leaning toward self-fetching via `processId`.

Decision logic:

- If `process.uiWidget` is set and a widget is registered under that name → render the registered component with `WidgetProps`.
- If `process.uiWidget` is set but no widget is registered → `console.warn`, fall back to default rendering.
- If `process.uiWidget` is not set → default rendering (status + progress + log tail).

Default rendering is minimal in this spec; enhancing it is a separate concern.

Deep-linkable per-process URLs (introducing a router) are explicitly out of scope; left as future work.

### Lifecycle

- `uiWidget` is set at task-definition time and does not change during process execution.
- Rendering host reacts to process document changes; mid-execution widget swaps are possible but not a supported pattern.

---

## Primitive 2: Widget Upstream Proxy

### Python side (optio-core)

New `ProcessContext` methods:

```python
async def set_widget_upstream(
    self,
    url: str,                            # e.g., "http://127.0.0.1:45678"
    inner_auth: InnerAuth | None = None,
) -> None

async def clear_widget_upstream(self) -> None
```

`InnerAuth` union covers how optio-api authenticates to the upstream when forwarding:

```python
@dataclass
class BasicAuth:
    username: str
    password: str

@dataclass
class QueryAuth:
    name: str            # e.g., "auth_token"
    value: str

@dataclass
class HeaderAuth:
    name: str            # e.g., "X-Opencode-Token"
    value: str

InnerAuth = BasicAuth | QueryAuth | HeaderAuth
```

### Data model

New optional field on the process document:

```
widgetUpstream: {
  url: string
  innerAuth?: { kind: "basic" | "query" | "header", ... }
} | null
```

### Transport (worker → optio-api)

No new Redis stream. The worker writes `widgetUpstream` to the process document via the store layer (same code path as status/progress writes). Optio-api reads it via:

- **First-request lazy load:** on the first proxied request for a given processId, look up `widgetUpstream` in MongoDB. Cache in an in-memory registry keyed by processId.
- **Invalidation via the tree poller:** on process-doc change notifications, update or evict the cached entry.
- **Restart recovery:** optio-api restart empties the cache; the next request repopulates. No reconciliation protocol needed.

### Proxy routes

```
(ALL)  /api/widget/:database/:prefix/:processId/*path         → HTTP + SSE (SSE is a long-lived GET)
(WS)   ws:/api/widget/:database/:prefix/:processId/*path      → WebSocket upgrade
```

The `database` and `prefix` segments are URL-encoded path components, not query parameters. They must live in the path because the iframe's content generates relative URLs whose browser resolution preserves the path but drops the query string — any query-scoped routing info would be lost on the first relative navigation or asset load inside the iframe.

Per-request flow:

1. Parse `database`, `prefix`, `processId` from the path. 404 on malformed.
2. Authenticate the outer auth via optio-api's configured `AuthCallback`. 401 if null.
3. Authorize: viewer role sufficient for GET/SSE and WS; operator required for POST/PUT/DELETE. Uses `checkAuth(..., isWrite)` as elsewhere.
4. Resolve the Mongo `db` via the same `resolveDb` helper the REST routes use (`{ db } = resolveDb(dbOpts, { database, prefix })`). 404 on failure.
5. Look up `widgetUpstream` in `{prefix}_processes` on that db (lazy-load + cache, keyed by `<database>/<prefix>/<processId>`).
6. If absent → 404.
7. Rewrite: strip `/api/widget/:database/:prefix/:processId` prefix; inject inner auth per the configured `InnerAuth` variant.
8. Forward via the proxy plugin (see Library + adapter integration). Stream response back unmodified.

### CORS

The iframe is same-origin with optio-api; no CORS config needed on `/api/widget/*`.

### Embedding defenses (X-Frame-Options, CSP frame-ancestors)

The widget proxy strips anti-embedding response headers before returning upstream responses to the browser:

- Deletes `X-Frame-Options` entirely.
- Removes only the `frame-ancestors` directive from `Content-Security-Policy`; other CSP directives pass through. If `frame-ancestors` was the sole directive, the CSP header is dropped.

Rationale: the proxy's purpose is precisely to make an upstream embeddable in an iframe under optio-api's outer auth. Upstreams like marimo, jupyter, or internal dashboards default to `X-Frame-Options: deny` / `frame-ancestors 'none'` as a generic clickjacking defense against any third-party embedding. In the widget proxy's context, that defense is already provided by optio-api's `AuthCallback`: the proxy is unreachable without a valid session, and the iframe is same-origin with the dashboard. Preserving the upstream's anti-embedding headers would break the proxy without adding security.

### Library + adapter integration

- **MVP ships only the Fastify adapter**, built on `@fastify/http-proxy`. The widget-proxy registration is an internal step of `registerOptioApi(fastifyInstance, opts)` — consumers only need the one init call, and the proxy inherits `opts.authenticate` and `opts.db` / `opts.mongoClient` from the same adapter options. It installs a `preHandler` that runs URL-routing extraction + auth + authorization + upstream lookup + 404 short-circuit, attaches the resolved upstream to the request, and lets the plugin handle the forward. Dynamic upstream via `replyOptions.getUpstream`; header-style inner auth via `rewriteRequestHeaders`; query-style inner auth via URL mutation in the preHandler or the plugin's query-string option, whichever is cleaner in the pinned version.
- **WS upgrade auth is the one known unknown.** `@fastify/http-proxy`'s WS hook (`wsHooks.preConnect` or equivalent in the pinned version) gives the hook callback a request shape that is not necessarily a full `FastifyRequest`. Plan-phase deliverable: confirm the WS hook shape in the pinned version and decide whether WS auth can reuse the existing `AuthCallback<FastifyRequest>` directly or needs a small adapter / separate WS-typed callback.
- Express, nextjs-pages, nextjs-app adapters: explicitly deferred. When adapted later, each adapter will supply its own framework-native proxy integration while the core auth + upstream-lookup logic stays shared.

### Authentication

The proxy routes reuse optio-api's existing `AuthCallback` mechanism; they do not introduce a parallel auth plane:

```typescript
// packages/optio-api/src/auth.ts — already exists
export type AuthCallback<TRequest> =
  (req: TRequest) => Promise<OptioRole | null> | OptioRole | null;
```

- **Outer auth.** Every `/api/widget/:database/:prefix/:processId/*` request and every WS upgrade passes through the host-configured `AuthCallback`. The callback inspects the request using whatever the host's auth system is (cookie, bearer, mTLS, API key — optio-api is agnostic). Enforcement: viewer for reads, operator for writes.
- **Inner auth.** Configured per-process by the worker via `set_widget_upstream(url, inner_auth=...)`. Optio-api injects the configured Basic Auth / query param / header when forwarding. The browser never sees the inner credential.
- **CSRF.** Inherits whatever the host app configured for other optio-api routes. If the host uses cookie auth, it is responsible for CSRF defenses (SameSite cookies, origin checks, etc.); the widget proxy inherits that protection.

### WS upgrade and the generic AuthCallback

`AuthCallback<TRequest>` is generic over the framework's request type. The WS upgrade event gives each framework a somewhat different request object. Each adapter's widget-proxy registration is responsible for constructing the correct `TRequest` shape for the upgrade and calling `checkAuth` before completing the upgrade.

For the Fastify adapter specifically, the shape of the request object exposed to `@fastify/http-proxy`'s WS hook (the plan-phase unknown noted under Library + adapter integration) determines whether we can pass it straight to `AuthCallback<FastifyRequest>` or need a small adapter that constructs a `FastifyRequest`-compatible view over the raw upgrade request.

### Subpath URL routing for the embedded client

Most embedded web clients assume their server is at `location.origin`. When the iframe is served under `/api/widget/<database>/<prefix>/<processId>/`, this is wrong: the client would send requests to the root path, bypassing the proxy.

The iframe widget (Primitive 4) handles this by writing origin-scoped `localStorage` keys *before* the iframe loads. Since the iframe is same-origin with the parent dashboard, keys set by the parent are visible to the iframe's code.

The iframe widget accepts a `localStorageOverrides` entry in `widgetData` so any embedded app with a runtime-URL convention can be supported. Opencode's convention (`opencode.settings.dat:defaultServerUrl`) is one such; marimo's (if it has one, or if `--base-url` CLI is preferred) is another.

### Deployment constraint

The iframe must be served from an origin where the host app's configured auth will function on embedded-app requests. In a cookie-based setup this means same-origin or shared-cookie-domain. In a bearer-token setup, the token must reach the iframe's requests — which in practice usually still means cookie-backed state, since arbitrary embedded apps (like opencode) cannot be modified to attach headers.

---

## Primitive 3: Widget Data

### Python side (optio-core)

New `ProcessContext` methods:

```python
async def set_widget_data(self, data: Any) -> None
async def clear_widget_data(self) -> None
```

- `data` must be JSON-serializable. Optio-core does not interpret the content.
- `set_widget_data` overwrites the whole blob. No append, no patch — a process wanting append semantics maintains its own list in the blob and rewrites it.
- `clear_widget_data` sets it to `null`.

### Data model

New optional field on the process document:

```
widgetData: <any JSON value> | null
```

### Propagation to the UI

- Available on the process document returned by existing read endpoints (`GET /api/processes/:id`). No new endpoint.
- Propagates to connected clients by riding the existing tree-stream `update` event. Two changes to `createTreePoller` are required:
  1. Add `widgetData` to the snapshot fingerprint so a widgetData-only change still triggers an event. (The current fingerprint covers only `status` and `progress`; a widgetData-only change would otherwise be invisible.)
  2. Add `widgetData` to the per-process payload shipped in the `update` event.
- List stream (`createListPoller`) is **not** modified: the sidebar does not use `widgetData`, and `widgetData` can be arbitrarily large. Keeping the list stream slim.
- `widgetUpstream` is **never** included in any client-facing payload (list stream, tree stream, or REST response). It contains credentials via `innerAuth` and is server-side only. Tests assert this invariant.
- `WidgetProps.process.widgetData` in the widget component is always the latest observed value.

### Lifecycle

- Cleared on dismiss, alongside logs/progress.
- Preserved across terminal states (`done`/`failed`/`cancelled`) — the widget can read final state on a completed process.

---

## Primitive 4: Generic Iframe Widget

Ships in optio-ui as a registered widget under the name `"iframe"`. Any process whose UI is a live-running web app served through the upstream proxy can declare `ui_widget="iframe"` and use this widget without shipping React code.

### Behavior

1. Reads `WidgetProps.process.widgetData` for iframe configuration.
2. Before the iframe mounts, writes any `widgetData.localStorageOverrides` keys into the parent origin's `localStorage` (same origin = the iframe sees them).
3. Mounts an `<iframe>` whose `src` is `WidgetProps.widgetProxyUrl` (or `widgetData.iframeSrc` if overridden).
4. Cleans up localStorage keys it set when the widget unmounts.

### Rendering states, keyed on the process doc

- **No `widgetData` yet** → loading placeholder. Convention: worker sets `widgetData` only after upstream is registered and the upstream app is confirmed ready. This doubles as the "ready to mount" signal, avoiding race between iframe load and proxy registration.
- **`widgetData` present + process running** → mount the iframe.
- **Process terminal (`done`/`failed`/`cancelled`)** → keep the iframe mounted and overlay a dismissible "session ended" banner. Upstream is about to be cleared; any live connections drop and surface naturally in the embedded app's error UI.
- **Process dismissed** → unmount the iframe.

### widgetData conventions consumed by this widget

```typescript
interface IframeWidgetData {
  localStorageOverrides?: Record<string, string>
  iframeSrc?: string           // override; default is widgetProxyUrl
  sandbox?: string             // override iframe `sandbox` attribute
  allow?: string               // override iframe `allow` attribute (feature policy)
  title?: string               // iframe title for a11y
}
```

Unknown keys are ignored; the worker may store additional keys for its own use.

### Sandbox / permissions defaults

- Iframe rendered **without** a `sandbox` attribute by default. Same-origin use requires `allow-same-origin`; the origin boundary is already enforced by the proxy.
- `allow` defaults to an empty string. Processes needing clipboard, camera, microphone, etc., set `widgetData.allow`.
- Per-widget, per-deployment discretion. Processes embedding untrusted apps should enable sandboxing; processes embedding a trusted internal tool need not.

### Why in optio-ui rather than optio-opencode

Mounting an iframe to a proxied URL with configurable pre-mount setup is genuinely generic — not opencode-specific. Jupyter, TensorBoard, a running Rails server, any internal dashboard could use it. Keeping it in optio-ui avoids every consumer duplicating the same component. Consumers with special needs can still register their own widget under a different name.

---

## Lifecycle and Failure Handling

### Happy-path lifecycle

```
Process starts
  ↓
  Worker prepares its upstream (SSH tunnel, local subprocess, whatever)
  ↓
  Worker: ctx.set_widget_upstream(url, inner_auth=...)
  ↓
  Worker: ctx.set_widget_data({...})     ← signals iframe widget to mount
  ↓
  [user interacts via proxied iframe]
  ↓
Process ends (done/failed/cancelled)
  ↓
  Worker: ctx.clear_widget_upstream()   [or optio-core does it in teardown]
  ↓
  Registry entry removed; in-flight browser connections drop
  ↓
  widgetData preserved on the doc (for post-mortem view)
```

### Optio's lifecycle guarantees

- `widgetUpstream` is cleared whenever the process enters a terminal state, whether or not the worker called `clear_widget_upstream` explicitly. Optio-core clears it as part of the standard teardown path.
- On dismiss, both `widgetUpstream` and `widgetData` are cleared alongside logs.
- On relaunch, previous `widgetUpstream` and `widgetData` are cleared before the worker runs.

### Failure modes and stance

| Failure | Optio behavior | Required from the process |
|---|---|---|
| Worker crashes before `set_widget_upstream` | `widgetUpstream` never set; iframe widget stays in loading state until process transitions to `failed`, then shows banner. | Nothing. |
| Worker sets upstream but upstream is unreachable | Proxy requests return upstream's error (connection refused → 502). Registry is unchanged. | Process should monitor upstream and either retry or transition to `failed`. Optio does not probe upstreams. |
| optio-api restart mid-session | In-memory cache drops; next proxied request re-reads `widgetUpstream` from MongoDB. Active SSE/WS connections break; browser reconnects. | Nothing. |
| Browser reloads the iframe | New request hits proxy, reads registry, forwards. Upstream's own session semantics decide whether state persists. | Nothing. |
| Network blip between worker and upstream | Optio has no visibility into the worker's upstream. | Process decides: reconnect, or fail. Implementation in consumer. |
| Worker process crashes hard (no teardown) | Existing instance-liveness heartbeat detects the dead process and transitions it to `failed`; `widgetUpstream` cleared as part of that transition. | Nothing. |
| `widgetData` missing after `widgetUpstream` is set | Iframe widget stays in loading state indefinitely. | Process must always set `widgetData` after upstream, as the "go live" signal. Documented convention. |

### What optio explicitly does not do

- Probe upstream health.
- Reconnect a worker's upstream after a drop.
- Retry failed proxied requests.
- Surface upstream errors as anything other than pass-through HTTP status codes.

All of that lives with the worker / consumer.

---

## Testing Approach

### Unit tests (optio-core)

- `set_widget_upstream`, `clear_widget_upstream`, `set_widget_data`, `clear_widget_data` round-trip through the store layer.
- Teardown correctly clears `widgetUpstream` on all three terminal states and on dismiss.
- Relaunch clears prior `widgetUpstream` and `widgetData` before the worker starts.

### Integration tests (optio-api, Fastify adapter)

- A small in-process Node HTTP server as the upstream, supporting REST + SSE + WS echo.
- Scenarios: unauthenticated → 401; viewer → GET/SSE OK, POST forbidden; operator → POST OK; unknown processId → 404; upstream down → 502 pass-through; inner-auth injection verified per variant.
- WS: upgrade allowed only after auth passes; upgrade carries inner auth; close on upstream disappearance.
- optio-api restart mid-session: verify new requests rebuild the registry from MongoDB.

### Component tests (optio-ui)

- `ProcessDetailView` dispatches to the registered widget for a given `uiWidget`; falls back to default on missing registration.
- Iframe widget: mounts iframe only after `widgetData` present; writes `localStorageOverrides` before mount and cleans up on unmount; honors `sandbox` / `allow` / `iframeSrc` overrides.
- Registry: `registerWidget` replaces on re-registration (so tests can re-register without teardown ceremony).

### End-to-end smoke

- One happy-path e2e: dummy task whose worker starts a tiny local HTTP+SSE server, registers upstream, writes widgetData, sleeps; assert the dashboard can hit the proxy, see SSE, and see teardown on process end.

---

## Reference Task: Local Marimo

### Purpose

A user-verifiable, end-to-end validation of all four primitives, buildable without optio-opencode. Ships in optio-demo. A human can launch it from optio-dashboard and confirm the whole stack works — widget registry routes rendering, upstream proxy forwards HTTP + WS, widget data signals go-live, iframe widget mounts correctly.

### Why marimo

Pure Python reactive-notebook tool with its own web server (HTTP + WS for reactivity). Installs via pip, runs on `127.0.0.1` with no auth, and its UI is a real tool — useful enough that "does the widget work" is obviously answerable by looking at it.

### Shape of the task

Lives in `optio-demo`:

```python
@task(name="marimo-notebook", ui_widget="iframe")
async def run_marimo(ctx: ProcessContext, notebook_path: str) -> None:
    # 1. Pick a free local port
    # 2. Start `marimo edit --port=<port> --host=127.0.0.1 <notebook_path>` as subprocess
    # 3. Wait for marimo to be listening
    # 4. await ctx.set_widget_upstream(f"http://127.0.0.1:{port}")
    # 5. await ctx.set_widget_data({...})  # localStorage overrides if needed
    # 6. Monitor the subprocess; propagate exit/failure to process state
    # 7. On cancellation: terminate subprocess; teardown clears upstream
```

No SSH, no remote host, no inner auth — the simplest possible consumer of the primitives.

### Primitives exercised

- **Widget registry** — `ui_widget="iframe"` resolves to the generic iframe widget.
- **Widget upstream proxy** — proxies HTTP + WS (marimo's reactive updates) to `127.0.0.1:<port>`.
- **Widget data** — carries any iframe configuration marimo needs; doubles as the "go live" signal.
- **Iframe widget** — mounts marimo's UI through the proxy.

### Subpath-URL question

Same issue as opencode: marimo's client may assume `location.origin` is its server. Two possibilities to resolve during the plan phase:

1. Marimo supports a `--base-url` flag. If yes, the worker passes `--base-url=/api/widget/<database>/<prefix>/<processId>`; no localStorage tricks needed.
2. Marimo does not. The iframe widget's `localStorageOverrides` (or equivalent mechanism for marimo's conventions) handles it.

This is the intended shakedown of the subpath strategy: if marimo works cleanly, the pattern generalizes; if it does not, something is learned about the primitives before optio-opencode builds on them.

### User-verifiable smoke test

Documented in the demo's README:

1. Start optio-dashboard + optio-demo.
2. Authenticate.
3. Launch a `marimo-notebook` task with a sample notebook shipped in the demo.
4. Open the process detail view. Iframe widget mounts; marimo UI is interactive; reactive updates flow over WS through the proxy.
5. Cancel the process. Iframe disconnects; banner shows; subprocess is terminated.
6. Dismiss. Widget unmounts; `widgetUpstream` and `widgetData` are cleared.

### Automated verification

A headless Playwright test in the demo package that performs steps 3–6 and asserts observable state (HTTP 200 from proxied requests, upstream cleared on teardown). Closest thing to an integration test without spinning up opencode. Not a replacement for the unit / integration tests above.

---

## Out of Scope (and future work)

- Express, nextjs-pages, nextjs-app adapters for the widget proxy (MVP ships Fastify only).
- optio-opencode's internal design (companion seed spec).
- Health-probing, reconnection, or retry for upstreams (process / consumer concern).
- App-to-app `postMessage` between iframe and parent dashboard.
- Per-widget fine-grained authorization beyond optio-api's existing viewer/operator distinction.
- Deep-linkable per-process URLs (would introduce a router in optio-dashboard).

## Resolved Decisions

These were the open questions at initial spec draft; resolved in a follow-up brainstorming pass.

1. **Proxy library: `@fastify/http-proxy`.** Its hook surface accommodates our needs (per-request auth via `preHandler`, dynamic upstream via `getUpstream`, header-style inner auth via `rewriteRequestHeaders`, 404 short-circuit via the preHandler, SSE pass-through by default). Query-style inner auth needs either the plugin's query-string hook or a preHandler URL mutation — decided at plan time against the pinned version. WS auth hook shape is the one remaining unknown; also confirmed at plan time. The maintenance-burden argument for a third-party plugin outweighs the minor friction of adapting to its hook surface.
2. **Iframe URL convention: `${apiBaseUrl}/api/widget/${encodeURIComponent(database)}/${encodeURIComponent(prefix)}/${processId}/`.** With `/api/` (consistent with every other API route) and trailing slash (load-bearing for relative URL resolution inside the iframe). `database` and `prefix` are path segments — not query params — because relative URLs inside the iframe lose the query string on navigation and asset loads; the routing info must travel in the path. Both are drawn from `useOptioDatabase()` / `useOptioPrefix()` (which ultimately come from instance discovery or an explicit `<OptioProvider>` prop), so no plumbing changes are required on the host side. When `database` is undefined (embedded single-db consumer without discovery), the iframe widget falls back to default rendering with a `console.warn`.
5. **Widget proxy exposed via `registerOptioApi`.** The Fastify adapter exposes one init call; the widget proxy is an internal component of `registerOptioApi` rather than a separately-exported `registerWidgetProxy`. This keeps the public surface narrow and lets the proxy inherit `dbOpts` and `authenticate` from the same options block the REST routes use.
3. **No routing.** The current dashboard selects processes via React state, not a URL route. `ProcessDetailView` is a new component that replaces the inline `<ProcessTreeView /> + <ProcessLogPanel />` render in the right pane. Deep-linkable per-process URLs left as future work.
4. **widgetData rides the tree-stream `update` event.** Tree-stream snapshot fingerprint and per-process payload are extended to include `widgetData`. List stream is unchanged (sidebar doesn't need `widgetData` and it can be arbitrarily large). `widgetUpstream` is never exposed to clients.

