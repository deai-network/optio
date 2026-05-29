# Browser-Open Surfacing

This spec was written against the following baseline:

**Base revision:** `4f2f00a3a7136bf66f728751152f29ec4dfb89d0` on branch `main` (as of 2026-05-29T09:55:46Z)

## Summary

A task running server-side sometimes needs a URL opened in the **operator's
real browser** — the canonical case is Claude Code's `/login`, which opens a
browser to an Anthropic authorization URL. Under optio the agent runs
server-side (often containerized or over SSH); there is no usable browser on the
worker, and the only client touchpoint today is the iframe widget. This feature
**widens the optio interface** with a generic server→client channel: any task
can request that the client open a URL.

The mechanism folds browser-open requests into the **process status doc** and
rides the **existing process-status SSE path** to the client (Approach B). No new
SSE stream, no new API endpoint, no optio-api DB write, no session id. It mirrors
the existing `widgetData` precedent (process-scoped state the client acts on).

Built in **two phases with a test gate between them**:

- **Phase 1 — Surfacing (generic, all tasks):** the `ctx.request_browser_open`
  method through to the client opening a tab / showing a link.
- **Phase 2 — Capture (host bridge):** optio-host opt-in shims that capture an
  agent's browser-open attempt and route it into Phase 1.

## Motivation

Live testing of the seed feature (`docs/2026-05-29-optio-claudecode-seed-design.md`,
parked) surfaced this gap: seed creation needs interactive Claude Code login,
which opens a browser the operator never sees. Claude Code's login does **not**
use a localhost callback — approval happens server-side with a paste-code
fallback — so **surfacing the URL to the operator's browser is sufficient**; no
callback-port tunneling is needed. The capability is generic (any task may want
to open a URL), so it belongs in optio-core / optio-host / optio-api / optio-ui,
not in a single agent package.

## Approach (decided)

**Approach B — fold into process status.** Considered and rejected Approach A (a
dedicated browser-open queue collection + per-session SSE stream + an ack
endpoint + a UI-generated session id plumbed through launch). B wins:

- Reuses the existing status→SSE→optio-ui path; no new stream/endpoint.
- No optio-api DB write (preserves the read-only invariant; engine owns writes).
- No session-id lifecycle/plumbing. Scope = "clients observing the process,"
  which is exactly the login audience.
- Mirrors the `widgetData`/`widgetUpstream` precedent.

The cost of B is client-side fragmentation (multiple feeds/observers); the design
handles that explicitly (see Phase 1, optio-ui).

## Phase 1 — Surfacing (generic)

### Data model

The process doc gains an append-only array field `browserOpenRequests`:

```
browserOpenRequests: [ { requestId: <uuid4 hex str>, url: <str> }, ... ]
```

Appended via `$push` (mirrors `append_log`). Tiny in practice (typically one
entry per login). Clearing entries on terminal state is **out of scope** (v1).

### optio-core

`ProcessContext.request_browser_open(url: str)` (in
`packages/optio-core/src/optio_core/context.py`, alongside the
`set_widget_*` methods) calls a new store helper:

```python
# packages/optio-core/src/optio_core/store.py
async def append_browser_open_request(db, prefix, process_oid, url: str) -> str:
    request = {"requestId": uuid4().hex, "url": url}
    await _collection(db, prefix).update_one(
        {"_id": process_oid}, {"$push": {"browserOpenRequests": request}},
    )
    return request["requestId"]
```

`request_browser_open` returns the generated `requestId` (useful for callers /
tests). Follows the exact shape of `update_widget_data` (`$set`) but uses `$push`.

### optio-contracts

Add to `ProcessSchema` (`packages/optio-contracts/src/schemas/process.ts`):

```typescript
browserOpenRequests: z.array(
  z.object({ requestId: z.string(), url: z.string() }),
).optional()
```

`url` is `z.string()` (not `.url()`) — keep permissive (localhost, custom
schemes).

### optio-api

The SSE pollers use explicit field allow-lists, **not** full-doc passthrough. Add
`browserOpenRequests: p.browserOpenRequests ?? []` to the `update`-event mapping
of **all three** pollers in `packages/optio-api/src/stream-poller.ts`:

- `createListPoller` (≈L42–57)
- `createTreePoller` (≈L113–131)
- `createMultiTreePoller` (≈L247–266)

Also add `browserOpenRequests` to each poller's **comparison snapshot** (the
change-detection projection) so a new request triggers an `update` emission.
optio-api stays read-only; propagation is the existing ~1s poll (fine for a login
URL). Unlike `widgetUpstream`, this field is client-bound and must **not** be
stripped.

### optio-ui (uniform handling — the key correctness requirement)

There is **no single observation path**: three independent feeds drive
independent client chokepoints with no shared store —

| Feed | Chokepoint | Reaches |
|---|---|---|
| `/processes/stream` | `useProcessListStream` (module singleton) | sidebar `ProcessItem` |
| `/tree/multi/stream` | `MultiProcessStreamProvider` (context) | `ProcessDetailView` subtree |
| `/{id}/tree/stream` | `useProcessStream` per-pid fallback | `ProcessDetailView` standalone |

Handling browser-open in any one component would silently drop requests on the
other views. Therefore:

- A **single shared module-level handler** in optio-ui — `handleBrowserOpenRequests(requests)` — holds a module-level `Set<requestId>` for dedup. For each unseen `requestId`: attempt `window.open(url)` **and** raise an app-level notification (antd `notification`) carrying a clickable "Open login page ↗" link. The notification is the fallback when `window.open` is popup-blocked (no user gesture in an SSE callback), and is a surface **visible regardless of which view is mounted**.
- **Every feed chokepoint** (`useProcessListStream`, `MultiProcessStreamProvider`, and the `useProcessStream` per-pid fallback) calls `handleBrowserOpenRequests(...)` with the `browserOpenRequests` it sees on each `update`.
- The handler is **imperative/global** (antd `notification.open` + `window.open`) — no mounted component required — so it works no matter which (or whether a) detail view is rendered. Exported from `optio-ui/src/index.ts`; `optio-dashboard` inherits it via the `workspace:*` dependency. Real apps using optio-ui get it for free.

Dedup is in-memory: a page reload may re-surface a still-pending request
(harmless — a pending login is still pending).

### optio-demo — Phase 1 test task

A trivial pure-Python task "Open optio repo":

```python
await ctx.request_browser_open("https://github.com/deai-network/optio")
# report a STATUS / complete
```

Exercises core→contracts→api→ui with no host/agent involvement.

### Phase 1 test gate

Launch the task in the dashboard; from any view (sidebar or detail) a browser tab
opens — or, if popup-blocked, a notification with the link appears. Confirm
before starting Phase 2.

## Phase 2 — Capture (host bridge)

Bridges an external agent's browser-open attempt into `ctx.request_browser_open`,
reusing the optio.log marker channel.

### optio-host — marker parsing (always on)

In `packages/optio-host/src/optio_host/protocol/parser.py`: add

```python
_RE_BROWSER = re.compile(r"^BROWSER:\s*(.+?)\s*$")

@dataclass(frozen=True)
class BrowserEvent:
    url: str
```

(add `BrowserEvent` to the `LogEvent` union and a parse branch). In
`packages/optio-host/src/optio_host/protocol/session.py` `_tail_and_dispatch`,
add a branch: `BrowserEvent → await ctx.request_browser_open(ev.url)`. Parsing is
always on — a `BROWSER:` line is simply never emitted unless capture shims are
installed, so this is harmless for all existing tasks.

### optio-host — capture shims (opt-in, default OFF)

A reusable helper, e.g. `optio_host.browser_capture.enable(host) -> dict[str, str]`:

- Writes shim scripts to `<workdir>/bin/{xdg-open,gio,open,sensible-browser,www-browser}`
  whose body **appends `BROWSER: "$1"` to `<workdir>/optio.log` and exits 0**
  (capture-only — there is no real browser on the worker).
- Returns env additions: `BROWSER=<shim path>` and a PATH prepend of
  `<workdir>/bin`, to be merged into the agent launch env (the same `env`-dict /
  PATH-prepend mechanism opencode already uses, `host_actions.launch_*`).

This mirrors opencode's existing browser-**suppression** shims (which write the
same files but `exit 0` with no capture). Because the capture feature is
**opt-in**, the two shim sets are mutually exclusive per task and never collide.

### Adopters

- **optio-opencode:** untouched. Does not call `enable`; keeps its own startup
  browser suppression. (opencode already runs a web frontend and only opens a
  browser once at startup, which it suppresses.)
- **optio-claudecode:** the one-line flag-flip that calls `enable` on the claude
  launch is **deferred to seed-branch integration** — its only test harness
  (`fake_claude`) lives on the seed branch, not on `main`. Not built here.

### optio-demo — Phase 2 test task

A host-based task "Open browser via tool" that runs, via optio-host's
`run_log_protocol_session`, a small Python script —

```python
import webbrowser
webbrowser.open("https://github.com/deai-network/optio")
# then append "DONE" to optio.log and exit
```

— with `browser_capture.enable` on. `webbrowser` respects `$BROWSER` → invokes
the shim → appends the `BROWSER:` marker → `parser.py` → `_tail_and_dispatch` →
`ctx.request_browser_open` → surfaced (Phase 1). Exercises the full capture path
end-to-end with no claude.

### Phase 2 test gate

Launch the task; the script's `webbrowser.open` is captured by the shim and the
URL surfaces in the dashboard (tab opens, or link in the notification).

## Error handling

- **Popup blocked:** expected for `window.open` without a user gesture; the
  notification link is the always-available fallback.
- **Duplicate delivery:** the ~1s poll re-sends the full array each tick; client
  dedup by `requestId` ensures each opens once. Reload re-surfaces pending
  requests (harmless).
- **Capture shim is pure capture:** it never launches anything and always
  `exit 0`, so it never blocks or fails the agent.
- **Malformed URL:** schema is permissive (`z.string()`); the client may open or
  link whatever it receives. (Login URLs come from the agent, which we trust no
  more/less than its other output.)

## Testing

- **optio-core:** unit test — `request_browser_open` appends a
  `{requestId, url}` to `browserOpenRequests` and returns the id.
- **optio-host:** unit test — `parse_log_line("BROWSER: https://x")` →
  `BrowserEvent(url=...)`; an integration test running a subprocess that invokes
  an installed shim and asserting `ctx.request_browser_open` fired with the URL.
- **optio-ui:** the shared handler dedups by `requestId` and triggers
  open/notification (RTL/unit, or verified via the manual tasks).
- **Manual (the two gates):** Phase 1 github-url task; Phase 2 webbrowser task.

## Out of scope

- Approach-A machinery (dedicated queue, per-session SSE, ack endpoint, session
  id).
- Callback-port tunneling (claude login has no localhost callback).
- The optio-claudecode flag-flip and opencode adoption (claudecode adoption is
  the seed-branch integration step; opencode does not adopt).
- Clearing/GC of `browserOpenRequests` on terminal state.
- A one-time refactor unifying the three client feeds behind a single provider
  (considered; not needed — the shared handler covers uniformity).
