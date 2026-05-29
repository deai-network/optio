# Client-Directed Events (Phase 2)

This spec was written against the following baseline:

**Base revision:** `5e7c15a7ad87286c12d5114011130fdb5e0e1cca` on branch `main` (as of 2026-05-29T15:10:29Z)

> Supersedes `docs/2026-05-29-browser-open-surfacing-design.md` (browser-open was one of three capabilities; this consolidates all three and retargets to the post-phase-1 layout where the agent-coordination layer lives in `optio-agents`).

## Summary

Three related capabilities that let a **running task drive the operator's client**, plus the shared routing infrastructure they need. All three fold their payload into the **process status document** and ride **SSE** to the client — no new optio-api DB writes (the engine owns writes; optio-api stays read-only).

- **`browser_open(url)`** — open a URL in the operator's real browser (canonical trigger: Claude Code `/login`). **View-scoped**: delivered to whoever is observing the process.
- **`need_attention(reason)`** — a background task asks for human attention. **Initiator-session-scoped**: reaches the browser session that *launched* the task, even with no view mounted.
- **`domain_message(keyword, data)`** — an application-defined, domain-specific message to the frontend. **Initiator-session-scoped**, same delivery as attention.

Shared infrastructure: an opaque, client-minted **`sessionId`** (a **required** `launch` parameter), recorded on the process as `originatingSessionId`; a **session-scoped always-on SSE** carrying a unified `sessionEvents` stream (attention + domain); and three new **agent-emittable** optio.log keywords (`BROWSER:`/`ATTENTION:`/`DOMAIN_MESSAGE:`) parsed and documented in **optio-agents**.

## Motivation

optio tasks run server-side (containerized / over SSH); the only client touchpoint today is the iframe widget. Several needs can't be met from there: Claude Code's `/login` opens a browser the operator never sees (no localhost callback — server-side approval + paste-code, so *surfacing the URL* suffices); long-running unattended tasks occasionally need the operator's attention; applications want to push domain-specific signals to their own frontend. Each requires widening the optio interface with a generic server→client channel. The capabilities are generic (any task), so they belong in optio-core / optio-agents / optio-api / optio-ui — not in a single agent package.

## Approach (decided)

**Approach B — fold into process status, reuse SSE.** (Rejected Approach A: per-feature queues + ack endpoint + per-browser session routing baked into optio-api — it would require optio-api DB writes, which a repo lint forbids.) Two delivery **scopes**:

- **View-scoped (browser_open).** Delivered on the *existing* per-process feeds. A shared optio-ui handler dedups by `requestId`, attempts `window.open`, and renders a fallback link. Any observer of the process can act; multiple watchers acting is acceptable (low-probability, harmless).
- **Session-scoped (attention + domain_message).** Delivered on a *new* always-on singleton SSE keyed by the client's opaque `sessionId`, matched against the process's `originatingSessionId`. Reaches the launching session regardless of which (or whether a) view is mounted.

`sessionId` is an opaque routing token, **not** an identity — optio stores and matches it without interpreting it, staying owner-agnostic. It is *not* used for browser_open (that is view-scoped by design).

## Layered design

### optio-core

`ProcessContext` methods (alongside `set_widget_*`), each `$push`-ing a `{requestId: uuid4().hex, …}` record via a new `optio_core/store.py` helper:

- `request_browser_open(url: str) -> str` → `browserOpenRequests` += `{requestId, url}`.
- `need_attention(reason: str) -> str` → `sessionEvents` += `{requestId, type: "attention", reason}`.
- `domain_message(keyword: str, data) -> str` → `sessionEvents` += `{requestId, type: "domain", keyword, data}`.

`launch` gains a **required** `session_id: str | None` parameter, threaded through the existing `resume` path: `lifecycle.launch` → `executor.launch_process` → `_execute_process` → `ProcessContext` (stored as `ctx.session_id`) and written to the process doc as **`originatingSessionId`**. Required (no default) so every call site must consciously supply it; `None` is the explicit "unattended / no initiating session" value. Per-launch (not baked in config) because the same task may be launched from different sessions over time (incl. resume from another tab), and each launch re-points routing. **Children inherit** the parent's `session_id` (propagated in `run_child`), so a child's attention/domain events route to the root's initiating session.

Launch call-site handling (full inventory established in phase-1 planning):
- optio-ui `useProcessActions.launch` → the tab's `sessionId` (the real case).
- optio-api `handlers.ts` launch → from the request (UI sends it).
- Programmatic / app server-side launch → the app tunnels its client's token through its own steps.
- Scheduler/cron launch (`lifecycle.py`) → explicit `None` (unattended).
- Engine/executor tests, `interop/run.ts` → explicit `None`/dummy.
- Children (`run_child`) → inherited; not a `launch` call.

### optio-agents (the migrated coordination layer — phase-1 home)

- `protocol/parser.py`: add `BrowserEvent(url)`, `AttentionEvent(reason)`, `DomainMessageEvent(keyword, data)` to the `LogEvent` union with regexes `_RE_BROWSER` / `_RE_ATTENTION` / `_RE_DOMAIN_MESSAGE`. `DOMAIN_MESSAGE:` line format = `DOMAIN_MESSAGE: <keyword> <single-line-JSON>`; parser splits the first token as keyword and `json.loads` the remainder; malformed JSON → log + drop (not dispatched). Line-oriented protocol → data is single-line.
- Session driver (`protocol/session.py` `_tail_and_dispatch`): route `BrowserEvent → ctx.request_browser_open`, `AttentionEvent → ctx.need_attention`, `DomainMessageEvent → ctx.domain_message`. Parsing is always on (harmless if a keyword is never emitted).
- `protocol/prompt.py` SSOT (`LOG_CHANNEL_PROTOCOL`): add `BROWSER:`/`ATTENTION:`/`DOMAIN_MESSAGE:` to the documented keyword set. **All three are agent-emittable** and documented here (the single source consumed by every agent's prompt builder). For `BROWSER:`, the primary path is the capture shim, but an agent may emit it directly.
- `browser_capture.enable(host) -> dict[str, str]` (opt-in, default off): writes shims `<workdir>/bin/{xdg-open,gio,open,sensible-browser,www-browser}` that append `BROWSER: "$1"` to `<workdir>/optio.log` and `exit 0` (capture-only — no real browser on the worker), and returns env additions (`BROWSER=<shim>` + a `<workdir>/bin` PATH prepend) to merge into the agent launch env. Mirrors opencode's existing *suppression* shims; because it is opt-in, the two shim sets never collide.

### optio-contracts

- `ProcessSchema`: `browserOpenRequests?: { requestId: string; url: string }[]`; `sessionEvents?: ({ requestId: string; type: "attention"; reason: string } | { requestId: string; type: "domain"; keyword: string; data: unknown })[]`. (`url`/strings permissive — not `.url()`.)
- `launch` RPC params: add required `sessionId: string | null`.
- New contract for the session-events SSE response shape.

### optio-api (read-only preserved; engine owns writes)

- Add `browserOpenRequests` to the `update`-event field allow-list **and the change-detection comparison snapshot** of **all three** pollers in `stream-poller.ts` (`createListPoller`, `createTreePoller`, `createMultiTreePoller`). Client-bound → not stripped.
- New SSE endpoint `GET /api/session-events/stream?sessionId=<token>` — poll-backed (same ~1s mechanism), emits each process's new `sessionEvents` for processes whose `originatingSessionId` matches the subscriber's `sessionId`. No filter → matches nothing (single-operator deployments still get per-launch routing because the UI always sends its token). Read-only.
- `launch` handler forwards `sessionId` to the engine via clamator RPC (mutation through the engine, never a direct DB write).

### optio-ui

- **Browser-open (view-scoped) — uniform handling.** There is no single observation path: three independent feeds drive independent client chokepoints with no shared store (`useProcessListStream` → sidebar `ProcessItem`; `MultiProcessStreamProvider` → `ProcessDetailView`; `useProcessStream` per-pid fallback). So: a **single shared module-level handler** (`Set<requestId>` dedup) is invoked from **every** chokepoint with the `browserOpenRequests` each `update` carries; it attempts `window.open(url)` and raises an app-level antd `notification` with a "Open in a new tab ↗" link (the fallback when `window.open` is popup-blocked — no user gesture in an SSE callback). Imperative/global, visible regardless of mounted view. `browserOpenRequests` must therefore be added to all three feeds (above). Exported from optio-ui; optio-dashboard inherits it.
- **Session-events (session-scoped).** A single always-on `EventSource` manager, mounted once at `OptioProvider`, subscribes `/api/session-events/stream` with the tab's `sessionId`; dedups by `requestId`; dispatches by `type` to **app-supplied callbacks**: `onAttention(processId, reason)` and `onDomainMessage(processId, keyword, data)`. How the app reacts (navigate, toast, custom) is not optio-ui's concern; exactly-once per `requestId` (a reload re-fires still-present events — acceptable).
- **`sessionId` lifecycle.** Minted once per tab and persisted in **`sessionStorage`** (survives reload, per-tab, dies with the tab); on load, reuse the stored token or mint+store if absent. Exposed with a **`resetSession()`** (via `OptioProvider`/hook) that mints a fresh token + reconnects the session-events SSE — the app calls it on logout / any cutoff. Attached to every launch (`useProcessActions.launch` adds it to the launch body).
- App callbacks (`onAttention`, `onDomainMessage`) and `resetSession` exposed via `OptioProvider`.

### optio-demo

- **browser_open test (generic):** "Open optio repo" — a pure-Python task calling `ctx.request_browser_open("https://github.com/deai-network/optio")`.
- **browser_open capture test (host bridge):** "Open browser via tool" — a host task running, via the optio-agents session driver, a Python script (`import webbrowser; webbrowser.open(URL)` then `DONE`) with `browser_capture.enable` on; exercises shim → `BROWSER:` marker → parser → `ctx.request_browser_open` end-to-end, no claude.
- **attention + domain test:** a task calling `ctx.need_attention(...)` and `ctx.domain_message(...)`; the demo's `OptioProvider` supplies `onAttention` (navigate to the process via `setSelectedProcessId`) and `onDomainMessage` (console/toast) callbacks. (Dashboard routing is in-state `selectedProcessId`, not URL.)

### Adopters

- **optio-opencode:** gets attention + domain_message for free (agent emits the keywords; routes via `sessionId`). Does **not** call `browser_capture.enable` (keeps its own startup browser suppression; opt-in → no collision).
- **optio-claudecode:** the `browser_capture.enable` flag-flip on the claude launch is **deferred to phase 3** (claudecode is not on `main` yet; it adopts optio-agents + these features when its parked branch is finished). The `ATTENTION:`/`DOMAIN_MESSAGE:` keyword docs are inherited via the SSOT.

## Data model

- `browserOpenRequests: [{ requestId, url }]` — view-scoped, append-only.
- `sessionEvents: [{ requestId, type: "attention"|"domain", … }]` — session-scoped, append-only.
- `originatingSessionId: <opaque str> | null` on the process doc, set at launch.
- Client dedup is by `requestId` (in-memory); a reload re-surfaces still-present events (harmless — a pending attention/login is still pending).

## Error handling

- **Popup blocked** (expected without a user gesture): the notification link is the always-available fallback.
- **Duplicate delivery:** the ~1s poll re-sends arrays each tick; client `requestId` dedup ensures each fires once.
- **Malformed `DOMAIN_MESSAGE` JSON:** logged and dropped, not dispatched.
- **Capture shim** is pure capture (`exit 0`, never launches anything) — never blocks or fails the agent.
- **Unattended launch (`sessionId = None`):** session-scoped events route to no subscriber (logged); browser_open still works (view-scoped).

## Testing

- **optio-core:** each ctx method appends the right record + returns the `requestId`; `launch(session_id=…)` writes `originatingSessionId`; child inherits the parent `session_id`.
- **optio-agents:** `parse_log_line` yields `BrowserEvent`/`AttentionEvent`/`DomainMessageEvent` (incl. `DOMAIN_MESSAGE` keyword+JSON split and malformed-drop); `_tail_and_dispatch` routes each to the right ctx method; `browser_capture.enable` shim appends the `BROWSER:` marker and a subprocess invoking it is captured end-to-end.
- **optio-api:** all three pollers include `browserOpenRequests`; the session-events SSE delivers only matching-`originatingSessionId` events; the launch handler forwards `sessionId`.
- **optio-ui:** the shared browser-open handler dedups across all feed chokepoints; the session-events manager dispatches by `type` to the right callback; `sessionId` persists across reload and rotates on `resetSession`.
- **Manual:** the four demo test tasks.

## Out of scope

- The optio-claudecode `browser_capture` flag-flip + any opencode adoption beyond what is free (phase 3; opencode keeps its suppression shims).
- Callback-port tunneling (Claude login has no localhost callback).
- Approach-A machinery (per-feature queues, ack endpoint, optio-api DB writes).
- Clearing / GC of `browserOpenRequests` / `sessionEvents` on terminal state.
- A refactor unifying the three client feeds behind one provider (the shared handler covers uniformity without it).

## Implementation note

The implementation plan for this spec is to be authored **parallel-shaped**: every file owned by exactly one task, atomic moves, and all verification (pytest / tsc / lint / grep) batched into final task(s) rather than gated per task — so the work executes as a concurrent fan-out.
