# Optio — capabilities reference

**What this is.** A consolidated, verified account of what the optio monorepo provides: its
capabilities, its extension points, its wire surfaces, its hard constraints, and the places where
its own documentation is wrong. Written to be reused as background for design work rather than read
once.

**Basis.** Six parallel read-only surveys of `/home/csillag/optio` at commit `f5da73ab` (main,
clean), covering all 20 packages — roughly 132k lines of source and tests — plus the root
`README.md`/`AGENTS.md`, the `Makefile`, `optio-demo`, and a themed sweep of the 189-file `docs/`
corpus. Every `file:line` below was read. Claims that were inferred rather than executed are marked
*not verified*.

**How to read it.** Sections 1–3 are orientation. Section 4 is the capability catalogue and section
5 the extension-point catalogue — those two are the reusable core. Sections 6–8 are the agent
platform, the wire surfaces and the constraints. Sections 9–11 are what a designer most needs before
committing to anything: the defects found, the documentation trust map, and the open threads.

---

## 1. What optio is

Optio is a **process-management framework for applications that run long, observable, cancellable
background work**. You define async Python tasks; optio persists their state, progress, logs and
parent/child trees in MongoDB, schedules them, cancels them cooperatively, and — layer by layer —
exposes them over RPC, over REST+SSE, and in a React dashboard.

Since 2026-04 it has grown a second identity that now dominates the codebase: **a platform for
running coding agents as managed, resumable, sandboxed optio tasks**. Seven agents are wrapped
(Claude Code, opencode, Codex, Cursor, Grok, Kimi Code, Antigravity), local or over SSH, surfaced
either as an embedded terminal in an iframe or as a native chat UI.

The framing is a **progressive stack** (`README.md:20`, `AGENTS.md:81-93`): level 1 Python+Mongo,
level 2 +Redis RPC, level 3 +REST, level 4 +React. Each level adds a dependency and you take only
what you need.

### What it is not

- **Not a distributed job queue.** No work-stealing, no sharding, no retry/backoff policy engine.
  One engine process owns a `(database, prefix)` namespace.
- **Not preemptive.** Cancellation is cooperative; user code must poll `ctx.should_continue()`. A
  force path exists only after the grace deadline.
- **Not an authority at the API layer.** `optio-api` performs zero Mongo writes and holds zero
  state-machine logic.
- **Not multi-tenant-authenticated out of the box.** Auth is a caller-supplied callback returning
  `'viewer' | 'operator' | null`. There is no per-process ACL, no tenant scoping, no audit trail.
- **Not continuously integrated.** There is no CI (verified: no `.github/` directory). Releases run
  from a developer machine.

---

## 2. Architecture

### 2.1 The two load-bearing invariants

Both are stated repeatedly in the docs **and enforced mechanically**, which is the strongest
maturity signal in the repo.

**The authority rule** (`README.md:28-33`, `AGENTS.md:645-654`, `AGENTS.md:1140`). `optio-core` is
the sole writer to MongoDB. `optio-api` reads Mongo directly for GETs, SSE, the widget proxy and
discovery, and forwards every mutation over clamator RPC. Enforcement:
`packages/optio-api/eslint.config.mjs:9-17` bans eleven Mongo write methods via
`no-restricted-syntax`, and `Makefile:107-115` (`lint-no-direct-writes`, a prerequisite of `lint`)
greps for the same and fails the build. Verified: no such call exists in `src/` outside tests.

**Control-plane convergence** (`AGENTS.md:1154-1164`). One verb = one method on `Optio` in
`lifecycle.py`. Adapters translate wire shape and nothing else — no `??` defaulting, no `new Date()`,
no `new ObjectId()` in write paths. A new channel calls existing `Optio` methods; a new verb adds one
`Optio` method plus one thin adapter per channel.

### 2.2 Process boundaries

```
 ┌──────────────── browser ────────────────┐
 │ React (optio-ui + optio-conversation-ui)
 │  • REST via @ts-rest/react-query   GET /api/processes/...
 │  • SSE  /api/processes/stream                    (list)
 │  • SSE  /api/processes/:id/tree/stream           (tree + logs + widgetData)
 │  • SSE  /api/processes/tree/multi/stream         (multiplexed)
 │  • SSE  /api/session-events/stream               (task → this browser)
 │  • iframe → /api/widget/:db/:prefix/:pid/*       (HTTP + SSE + WS proxy)
 └───────────────┬─────────────────────────┘
                 │ HTTP / SSE / WS
 ┌───────────────▼──── Node process ───────────────────────────┐
 │ optio-api (Fastify | Express | Next.js pages | Next.js app) │
 │   reads  ──────────────────────────────► MongoDB (read-only)│
 │   mutates ─► OptioEngineClient (generated) ─► clamator RPC  │
 │   discovery: Mongo *_processes scan + redis heartbeat key   │
 └───────────────┬──────────────────────────┬─────────────────┘
                 │ Redis Streams (clamator) │
 ┌───────────────▼──── Python worker ───────▼──────────────────┐
 │ optio-core: RedisRpcServer(key_prefix=f"{db}/{prefix}")     │
 │             + OptioEngineService (9 verbs)                  │
 │   Optio.lifecycle ── sole writer ──► MongoDB                │
 │   APScheduler (cron)    executor (asyncio tasks)            │
 │   your tasks: execute(ctx)                                  │
 │        └─ agent wrapper task ──► optio-host (Local | SSH)   │
 └──────────────────────────────┬──────────────────────────────┘
                                │ subprocess / asyncssh
 ┌──────────────────────────────▼──── target host ─────────────────────┐
 │ tmux + ttyd (TUI agents)  |  agent's own web SPA  |  headless stdio │
 │ claustrum (Landlock) fs sandbox; HOME=<workdir>/home isolation      │
 │ ./optio.log keyword channel: STATUS: / DELIVERABLE: / DONE / ERROR  │
 └─────────────────────────────────────────────────────────────────────┘
```

Notes that matter:

- **Redis is optional.** `init(redis_url=None)` with no `rpc_server` disables the whole command
  surface; you then call `optio.launch()` in-process.
- **Namespacing.** One `(database, prefix)` pair = one optio instance. Collections are
  `{prefix}_processes`, `{prefix}_launch_blocks`, `{prefix}_<engine>_session_snapshots`; the Redis
  key prefix is `{database}/{prefix}`.
- **Remote hosts add a hop.** `Host.establish_tunnel(remote_port)` returns a worker-local port
  (identity locally, an SSH local forward remotely); that port becomes the widget upstream, so a
  remote agent's UI reaches the browser through two hops.

### 2.3 Package map

| Package | Lang | Ver | Role |
|---|---|---|---|
| `optio-core` | Py | 0.3.1 | The engine. Sole Mongo writer, state machine, executor, scheduler. |
| `optio-host` | Py | 0.2.7 | Local-or-SSH host abstraction; `Host` protocol; download task. |
| `optio-agents` | Py | 0.5.0 | Agent-framework SSOT: log-protocol driver, `Conversation`, `HookContext`, seeds, claustrum. |
| `optio-{claudecode,opencode,codex,cursor,grok,kimicode,antigravity}` | Py | 0.2–0.5 | Seven concrete agent wrappers. |
| `optio-agents-all` | Py | 0.1.2 | Static meta-factory over the seven. |
| `optio-contracts` | TS | 0.3.1 | Zod + ts-rest + clamator wire contracts; codegen source for both sides. |
| `optio-api` | TS | 0.2.8 | REST+SSE library; four framework adapters. |
| `optio-ui` | TS | 0.3.1 | React components, hooks, and the widget registry. |
| `optio-conversation-ui` | TS | 0.3.1 | Engine-neutral chat widget; 7 engine views. |
| `optio-agents-ui` | TS | 0.1.0 | Generated `{slug,name,url}` metadata mirror of the Python SSOT. |
| `optio-dashboard` | TS | 0.1.5 | Turnkey standalone app (`npx optio-dashboard`). Not embeddable by design. |
| `filtrum-core` / `filtrum-mongo` | TS | 0.1.1 | Backend-agnostic filter predicate language + Mongo dialect. |
| `optio-demo` | Py | 0.2.3 | The worked example and the cross-engine parity guard. |

---

## 3. The domain model

The nouns, defined once. Getting these right is most of understanding the system.

**TaskInstance** (`optio-core/src/optio_core/models.py:65`) — the declarative unit of work the
application's generator returns: `execute` (async fn of a `ProcessContext`), `process_id`, `name`,
`description`, `params`, `metadata`, `schedule` (5-field cron), `special`, `warning`, `cancellable`,
`ui_widget`, `supports_resume`, `ttl_seconds`, `auto_cancel_children`, `auto_resume`.
`TaskInstanceCore:49` is the child-applicable subset — children inherit metadata and lifecycle and
cannot carry a schedule.

**Process** — a Mongo document in `{prefix}_processes`; the runtime instance of a task, or of a child
spawned at runtime. Identified two ways interchangeably: Mongo `_id` hex **or** the application
`processId` string. When several documents share a `processId`, **the newest `_id` wins**
(`lifecycle.py:953`) — an orphan hazard that `resync()` mitigates by returning an explicit
`{processId: oid_hex}` map.

**Process tree** — `parentId` / `rootId` / `depth` / `order`. Roots have `parentId=None` and
`rootId == _id`.

**ProcessContext (`ctx`)** (`optio-core/src/optio_core/context.py:53`) — the only argument to an
execute function and the task's entire interface to the engine.

**ParallelGroup** (`context.py:756`) — async context manager scoping concurrent children behind a
semaphore.

**Metadata** — a plain dict on the task. **Everything hinges on it**: it is the sole dimension for
scoped resync, group cancel, launch blocking and listing. Any design on top of optio should treat the
metadata key schema as a first-class decision.

**Launch block** — an in-memory (optionally Mongo-persisted) metadata filter that refuses matching
launches while active.

**Widget channels** — `uiWidget` names a React component; `widgetData` is arbitrary JSON pushed to
the UI; `widgetUpstream` is a URL + inner auth that the API's proxy forwards to and which **never
reaches a client**; `controlUpstream` is the sibling channel for injecting human input.

**Session event / browser-open request** — client-directed side channels appended to the process
document and routed to the launching browser via `originatingSessionId`.

**Blob** — a GridFS file in the same database, tagged `{processId, prefix, name}`.

**Deadline / grace** — a monotonic timestamp on a cancel request. One deadline threads through the
whole cancelled subtree; **first deadline wins** (`executor.py:470`).

### Agent-layer nouns

**Agent** — not a class. A *package* supplying four artifacts: a `TaskConfig` frozen dataclass with
an `agent_type` discriminator, a `create_<engine>_task` factory, an `AGENT_INFO` constant, and
optionally a class structurally satisfying the `Conversation` Protocol.

**Session** — one run of one task: `run_<engine>_session(ctx, config)`, bracketed by `host.connect()`
and a teardown `finally`.

**Deliverable** — a file the agent places under `<workdir>/deliverables/` and announces with a
`DELIVERABLE:` line in `optio.log`. Validated, relativized, queued (bound 64), fetched, UTF-8
decoded, handed to `on_deliverable`, and **always acknowledged** back to the agent — including lines
the harness rejects mechanically before the callback runs.

**Seed** — a stored, optionally encrypted `tar.gz` of the *environment* subset of an agent's isolated
HOME: credentials, settings, global config — **no conversation data**. It solves the axis resume
cannot: *start a fresh session that is already logged in*. Pool + lease semantics with a 60 s TTL
lease.

**Snapshot** — one Mongo document per terminal run per `processId`, holding an encrypted home blob
and a plaintext workdir tar. Retention 5.

**`seed_blob_*` vs `session_blob_*`** — two independent crypto channels. `session_blob_*` wraps *this
process's* snapshot (per-process); `seed_blob_*` wraps the *shared pool account's* seed tar
(pool-scoped), falling back to `session_blob_*` when unset. Currently on claudecode and opencode
only, and **not** in the parity guard's core set.

**Claustrum** — the Landlock, fail-closed filesystem sandbox binary (pinned `v0.1.2`). It applies an
allowlist to itself then `execve`s the target, so the agent *and every tool subprocess* inherit the
confinement. Universal by design across all seven engines; vendor sandboxes are explicitly not
trusted.

**`delivery_type`** — the subdir under `deliverables/` used to route the "a newer claustrum release
is available" security notice. **Mandatory whenever `fs_isolation` is on**, which is the default.

**Conversation mode vs iframe mode** — `iframe` = tmux + ttyd serving the agent TUI in an iframe
widget; `conversation` = headless stdio, the task publishes a live `Conversation` object consumed by
the native chat UI.

---

## 4. Capability catalogue

What optio can actually do, with the entry point for each.

### 4.1 Process lifecycle

Eight states (`state_machine.py:3-18`): `idle`, `scheduled`, `running`, `cancel_requested`,
`cancelling`, `done`, `failed`, `cancelled`. Transitions: `idle→scheduled`;
`scheduled→{running, cancel_requested}`; `running→{done, failed, cancel_requested}`;
`done|failed|cancelled→{scheduled, idle}`; `cancel_requested→cancelling`; `cancelling→cancelled`.

Note: **`validate_transition` is never called by the engine** (grep-verified). State writes are
direct `$set`s guarded by *conditional Mongo queries* — the invariant lives in the query, not in a
type.

Commands on the module singleton or an `Optio` instance (`lifecycle.py`): `launch`, `launch_and_wait`,
`launch_and_await_result`, `cancel`, `cancel_and_wait`, `dismiss`, `resync`, `get_process`,
`list_processes`, `adhoc_define`, `adhoc_delete`, `block_launches`, `unblock_launches`,
`group_cancel`, `group_cancel_and_wait`, `materialize_upload`, `read_blob_bytes`,
`get_published_result`.

### 4.2 Cancellation

Two-stage and unusually well-developed — the heaviest test suites in the repo mirror it.

*Cooperative*: `cancel` computes an effective monotonic deadline, flips `scheduled` straight to
`cancelled` in one conditional update, or otherwise goes `→cancel_requested`, sets an `asyncio.Event`,
records the deadline, then conditionally `→cancelling` in a way that will not overwrite a terminal
state the task already raced to. Downward propagation honours `auto_cancel_children` per level and
shares the deadline.

*Forced*: a supervisor scans every 500 ms; past-deadline entries get `Task.cancel()` plus a bounded
shield wait, then a terminal **`failed`** write with `FORCE_CANCEL_ERROR`, then an unconditional
recursive cascade to active direct children.

**Consequence a designer must respect: a task that never polls `should_continue()` lands in `failed`,
not `cancelled`.** Downstream code treating `failed` as "bug" must special-case it.

*Upward propagation*: a child `failed` with `survive_failure=False` cancels siblings only and raises
`ChildProcessFailed`; a child `cancelled` with `survive_cancel=False` sets the parent's flag and
cascades. In a `ParallelGroup`, **failure dominates cancel**, and `__aexit__` raises an
`ExceptionGroup`, not a `RuntimeError`.

### 4.3 Hierarchical work

`ctx.run_child` / `run_child_task` / `run_child_with_result` / `run_child_task_with_result` /
`parallel_group(max_concurrency=10)`. Progress roll-up helpers in `progress_helpers.py`:
`sequential_progress`, `average_progress`, `mapped_progress(ctx, start, end)` — each returns an
`on_child_progress` callback. Four-level nesting is exercised in the demo
(`optio-demo/.../tasks/terraforming.py:217-283`).

### 4.4 Progress and logging

`ctx.report_progress(percent, message=None)`, buffered with adaptive avalanche coalescing: percent-only
calls coalesce silently and emit no log line; more than 10 message-bearing calls inside 0.1 s drops
intermediates and emits a synthetic `"(N messages dropped)"` line. Child-progress callbacks throttle
to 10/s. Env knob `OPTIO_PROGRESS_FLUSH_INTERVAL_MS`.

### 4.5 Scheduling, TTL, reconciliation, resume

- **Cron** via APScheduler 4.x, `TaskInstance.schedule`. **Caveat: APScheduler failures degrade
  silently** — `start` swallows exceptions and leaves the scheduler `None`, after which
  `sync_schedules` early-returns and cron simply never fires (`scheduler.py:28-51`).
- **TTL eviction** via `ttl_seconds` → `expireAt` + a Mongo TTL index. **Blast radius:** migration
  `m004` creates that index on *every* `*_processes` collection in the database, not just this
  prefix's.
- **Restart reconciliation** (`lifecycle.py:1169`): any document in an ACTIVE state at `init()` must
  be orphaned, so it flips to `failed` ("Process was interrupted by server restart").  `widgetData`
  is deliberately preserved.
- **Checkpoint/resume**: `supports_resume` + `ctx.resume` + `mark/clear_has_saved_state()` +
  `store_blob`/`load_blob`. `auto_resume` opts a top-level task into automatic post-restart relaunch
  after `auto_resume_delay_seconds` (default 300). Note force-killed (`failed`) processes are
  excluded from auto-resume by query.
- **Graceful shutdown**: refuses new launches, stamps auto-resume eligibility, deadlines every live
  cancellation, polls until drained or `grace + margin`, force-cancels the remainder, and only then
  sets the shutdown event in a `finally` so `run()` cannot return before the drain completes.

### 4.6 Admission control

`block_launches(filter)` as an async CM, `unblock_launches`, and `group_cancel(block_new_launches=True,
persist=True)`. Blocks stack independently. Asymmetry worth knowing: `group_cancel*` **reject** an
empty filter with `ValueError`, whereas `block_launches({})` blocks *everything*.

### 4.7 In-process result channel

`ctx.publish_result(obj)` plus `launch_and_await_result` / `run_child_with_result` → `ChildHandle`.
**Once per run, in-memory only** — invisible to a resumed or cross-process consumer. This is the
mechanism the conversation mode uses to hand a live `Conversation` object to the UI layer.

### 4.8 Remote execution

`optio-host` gives a structural `Host` protocol (16 methods) with local and SSH implementations behind
`make_host(ssh=..., taskdir=...)`: `run_command`, `launch_subprocess` (with `env_remove` credential
scrubbing and a 256 MiB stream limit), `terminate_subprocess`, `put_file_to_host`,
`fetch_bytes_from_host`, `tail_file`, `establish_tunnel`, `archive_workdir` / `restore_workdir`,
`setup_workdir`, `cleanup_taskdir`.

Key discipline: **`setup_workdir` wipes the workdir every run** and chmods to `0o700`. Anything that
must survive a run lives in `taskdir`, not `workdir`. That split is what makes resume possible.

### 4.9 Filtering

`filtrum-core` is a backend-agnostic predicate language: `{AND:[…]}`, `{OR:[…]}`, `{NOT:…}`, or a
field→ops map with exactly **nine operators** — `eq`, `ne`, `in`, `nin`, `exists`, `gt`, `gte`, `lt`,
`lte`. `filtrum-mongo` is the reference dialect. Injection defence lives entirely in the caller's
`fieldPath` validator (`optio-contracts/src/schemas/process.ts:92-94`, which bans `$`), because
`compile()` itself never validates.

**Critical asymmetry**: the TS `metadataFilter` accepts the full language, while Python's
`matches_filter` (`models.py:245`) is **flat AND-equality only**. See §9.

### 4.10 UI capabilities

Listing, live tree + logs over SSE, launch/cancel/dismiss/resync controls, per-process detail view,
filtering, instance discovery, and a widget host. Plus the conversation chat widget: transcript
rendering with markdown/KaTeX/mermaid, streaming, tool-call visibility, thinking visibility,
permission approve/deny, generic session controls, file upload and download.

### 4.11 Agent capabilities

See §6. Summary: install, launch (local or SSH), sandbox, seed/login reuse, snapshot/resume, live
conversation, interrupt, permission gating, model and reasoning-effort switching, deliverable
exchange, file upload/download, and teardown with credential save-back.

---

## 5. Extension points

Ranked by how much a downstream project would use them.

### 5.1 `get_task_definitions(services, metadata_filter) -> list[TaskInstance]`

`lifecycle.py:120`. **The primary seam** — the application declares everything optio manages. Called
on `init()`, on every `run()` start and on every `resync()`. The framework post-filters by
`metadata_filter`, so a callback may ignore the argument. The async form of the aggregator is the
pattern for *dynamically generated* task lists (read Mongo, decide what tasks exist).

### 5.2 `ProcessContext`

The task body's entire interface. Beyond progress and cancellation:
`publish_result`, `set_widget_upstream` / `set_widget_data` / `set_control_upstream`,
`register_upload_writer`, `request_browser_open`, `need_attention`, `client_message`,
`store_blob` / `load_blob` / `delete_blob`, `mark_ephemeral`, `mark_has_saved_state`.

### 5.3 `services` dependency injection

`init(services={...})` surfaces unchanged as `ctx.services`. The demo puts the engine itself in there
so tasks can call `fw.resync()`.

### 5.4 The widget registry (UI)

`registerWidget(name, Component)` — `optio-ui/src/widgets/registry.ts:15`. A task declares
`ui_widget="<name>"`; the process document carries it as `uiWidget`; `useProcessWidget(tree)`
(`components/ProcessWidget.tsx:21`) is the **layout-free** seam that owns only the readiness gate and
the proxy-URL build and returns a bare element, so the caller decides the chrome.

**The single most fragile assumption in the stack**: the registry is a module-level `Map`, so a
duplicated `optio-ui` instance silently splits it. The only defence is bundler dedupe
(`optio-dashboard/vite.config.ts:21`), which every external consumer must replicate — and which no
README or AGENTS.md warns about.

Two widgets ship built in: `iframe` and `iframe-input` (the latter adds a textarea that POSTs to the
widget-control route and forwards nav keys as tmux `send-keys` names).

### 5.5 The widget reverse proxy

A task calls `ctx.set_widget_upstream(url, inner_auth)`; the API proxies HTTP, SSE **and** WebSocket
to it at `/api/widget/:database/:prefix/:processId/*`, injecting Basic/header/query inner auth,
stripping the browser `Origin`, stripping `X-Frame-Options` and CSP `frame-ancestors`, with a 5 s TTL
cache and `timeout: 0` upstream (deliberate, for OAuth device-code flows). `widgetData.stripProxyPrefix`
switches HTML rewriting between the ttyd contract and the SPA contract.

### 5.6 `Host` protocol

Structural (PEP 544), so a third host type — container exec, k8s — needs no inheritance, only the 16
methods plus `workdir`/`taskdir`. The documented composition pattern is **free functions over `Host`**
living in the consumer package, not methods on the host.

### 5.7 The agent wrapper contract

See §6. Add a wrapper by supplying a package and making **four edits** in `optio-agents-all`.

### 5.8 Auth callback

`authenticate(req) -> 'viewer' | 'operator' | null`, the only mandatory option on every adapter.
Write detection is **method-only**.

**Adapter gotcha:** on Fastify the gate is an `onRequest` hook, encapsulation-scoped to the *whole
instance*, so registering on the root app gates the host's own unrelated routes too. The reference
consumer works around exactly this (`optio-dashboard/src/server.ts:80-82`). Express scopes to
`app.use('/api', …)` — different blast radius between adapters.

### 5.9 RPC-only integration

`createOptioTransports(redis)` + `new OptioEngineClient(transports.get(db, prefix))` — control the
engine with no HTTP at all. This is the documented seam for the out-of-repo consumer "excavator".
Any clamator client can wrap the same cached transport.

### 5.10 Extra RPC services

`optio_core.rpc_server.register_service(contract, impl)` before `run()` registers app-specific verbs
alongside optio's nine. Alternatively `init(rpc_server=...)` supplies a pre-built server whose
lifecycle optio does not own (mutually exclusive with `redis_url`).

### 5.11 Filter language extension

New backend: implement `Dialect<T>` (proven with a `Dialect<string>` in the tests). New operator: two
steps — declare via `makeFilterSchema({extraLeafOps: {...}})` *and* handle the name in the dialect's
`op`. Note a custom op can silently **override a built-in** (spread order,
`filtrum-mongo/src/dialect.ts:23`) — powerful, undocumented as intentional, unguarded.

### 5.12 Custom API adapters

All nine handlers are framework-agnostic and exported, and both docs tell you to write your own
adapter. **In practice this is not actionable** — `checkAuth`, `AuthCallback`, `OptioRole`,
`isWriteMethod`, `parseSseOptions`, `checkLegacyMetadataParams` and `findProcessByEitherId` are all
absent from `src/index.ts`.

### 5.13 Smaller seams

`ctx.register_upload_writer(writer)`; `adhoc_define` / `adhoc_delete` for runtime task registration;
`OptioProvider`'s `onAttention` / `onClientMessage` callbacks; `ProcessItem.inactiveContent` and
`launchDenyReason`; `useProcessFilter().filterFn`; `MultiProcessStreamProvider` as an opt-in
connection-multiplexing optimisation with no call-site changes; `optio_host.testing.sshd_container`
as a reusable SSH-over-Docker test harness; env knobs `OPTIO_PROGRESS_FLUSH_INTERVAL_MS` and
`OPTIO_CANCEL_TRACE`.

### 5.14 What is *not* extensible

- **Conversation protocols.** `optio-conversation-ui`'s engine dispatch is a hard-coded `if` chain
  (`ConversationWidget.tsx:29-34`), not a registry. A downstream project can add a *widget* but
  cannot add a *conversation protocol* without patching the package.
- **Agent registration.** `optio-agents-all` has no entry-point discovery; registration is four
  static edits, so adding an engine means editing that package.
- **The dashboard.** Explicitly not embeddable and not extensible — no plugin system, no custom
  routes, no router (hence no deep-linkable per-process URLs).
- **Widget proxy `ttlMs`.** On the private options type but never forwarded, so the 5 s TTL is not
  configurable at all.

---

## 6. The agent platform

### 6.1 The abstraction

**There is no `Agent` base class and no ABC.** Confirmed: no `abc.ABC`, no abstract methods anywhere
in `optio-agents/src`. The only `Protocol`s are `Conversation` and `HookContextProtocol`, both
`typing.Protocol`. Everything is inversion of control into the shared driver
`run_log_protocol_session` (`optio-agents/src/optio_agents/protocol/session.py:127`).

The real contract surface is **three callbacks**:

- **`prepare(host, hook_ctx)`** — runs after `host.setup_workdir()` and before the log tail
  subscribes. Install the agent binary, ttyd if iframe, claustrum if `fs_isolation`, then restore the
  snapshot.
- **`body(host, hook_ctx)`** — launch and stay alive while the agent runs.
- **`_agent_sender(message)`** — push one message into the live session.

…plus a **9-member `Conversation` Protocol** (`conversation.py:43-90`): `send`, `on_event`,
`on_message`, `on_permission_request`, `is_pending`, `interrupt`, `set_control`, `close`, and the
`closed` property.

### 6.2 The keyword channel

The agent writes lines into `<workdir>/optio.log`; the driver tails and parses them into a nine-variant
`LogEvent` union (`protocol/parser.py:67-71`):

| Line | Event | Gate |
|---|---|---|
| `STATUS: [N%] msg` | `StatusEvent` | always |
| `DELIVERABLE: path` | `DeliverableEvent` | always |
| `DONE[: summary]` | `DoneEvent` | always |
| `ERROR[: msg]` | `ErrorEvent` | always |
| `BROWSER: url` | `BrowserEvent` | `features.browser == "redirect"` |
| `ATTENTION: reason` | `AttentionEvent` | always |
| `CLIENT_MESSAGE: kw json` | `ClientMessageEvent` | `features.client_messages` |
| `CALLER_MESSAGE: kw json` | `CallerMessageEvent` | `features.caller_messages` |
| *(anything else)* | `UnknownLine` | fallthrough |

`ProtocolFeatures` gates **both the parser and the LLM-facing documentation**, so the agent is never
told about a channel nobody enabled — a disabled keyword's line degrades to `UnknownLine`, because
"an agent cannot trigger a facility nobody enabled".

Path safety: `validate_deliverable_path` (realpath + escape rejection) and
`relativize_deliverable_path` (must be *strictly under* `<workdir>/deliverables/`).

### 6.3 The capability model — the weakest part

**There is no `capabilities()` query.** A consumer cannot ask "does engine X support resume / MCP /
permissions?" programmatically. Capability is expressed four ways, all largely implicit:

1. `ProtocolFeatures` — three facets only (browser, client messages, caller messages).
2. **The `TaskConfig` field set** — a capability an engine lacks is simply an absent field, and
   illegal combinations raise at construction time. The only machine-readable form is
   `dataclasses.fields(<Engine>TaskConfig)`.
3. **`SessionControl[]` emitted at runtime** into `widgetData.controls` — the only *dynamic*
   capability report. Presence is the signal: no effort control means the running model has no graded
   effort. A control collapsing to ≤1 option is auto-disabled with a reason.
4. `AGENTS` / `get_agent_info` — identity metadata only, not capability.

**MCP has no first-class surface anywhere.** Three ACP wrappers send `"mcpServers": self._mcp_servers`
on the wire and nothing populates it from config.

### 6.4 The seven wrappers

| | codex | opencode | cursor | grok | kimicode | antigravity |
|---|---|---|---|---|---|---|
| **Invocation** | `codex app-server` stdio | `opencode web` + HTTP/SSE client | `cursor-agent acp` stdio | `grok agent … stdio` | `kimi acp` stdio | repeated one-shot `agy -p` under a PTY |
| **Wire** | JSON-RPC 2.0 JSONL (not ACP) | HTTP + SSE | ACP | ACP | ACP + `configOptions` | transcript.jsonl tail @ 0.4 s |
| **iframe** | tmux+ttyd | own SPA | tmux+ttyd | tmux+ttyd | own SPA (`#token=`) | tmux+ttyd |
| **Streaming** | yes | yes | yes | yes | yes | **no token deltas** |
| **Interrupt** | `turn/interrupt` | `POST /abort` | ACP cancel | ACP cancel | ACP cancel | **coarse** (kills the process) |
| **Reasoning effort** | 6 levels + slider | per-prompt variant | **none** | 4 levels, **no slider** | 5+6 levels + slider | **none** |
| **MCP** | no | no | wire slot only | wire slot only | wire slot only | n/a |
| **Permission gate** | yes | **no field** | yes | yes | yes | field exists, **no-op seam** |
| **src LOC** | 4 803 | 3 740 | 5 022 | 4 701 | 4 789 | 4 803 |
| **test:src** | 1.42 | **2.01** | 1.08 | **1.00** | 1.40 | 1.13 |

Maturity read: **codex** (audited, 28/29 parity) and **opencode** (deepest suite) are production-grade.
**kimicode** is substantively mature but under-documented (14-line README). **grok** is the clean ACP
reference yet has the thinnest suite and **zero real-binary env gates**. **cursor** ships a large
surface on a wire it has never authenticated against — 21 `runtime-unverified` annotations.
**antigravity** is newest, explicit about its limits, and carries 6 open `TODO(S2/S3)` markers, all
real-binary verification debt including an unconfirmed self-update suppression.

### 6.5 The de facto wrapper template

Every wrapper is the same 13–16 module package: `info.py`, `__init__.py`, `types.py`, `session.py`,
`host_actions.py`, `conversation.py`, `conversation_listener.py`, `prompt.py`, `snapshots.py`,
`seed_manifest.py`, `cred_watcher.py`, `verify.py`, `fs_allowlist.py`, `models.py`.

**Cost to add an eighth**, evidence-based:

- **Mechanical, ~1 800–2 200 lines (1–2 days)**: copy and rename the listener, prompt, snapshots,
  seed-manifest CRUD, credential watcher, fs allowlist, the validation ladder, the widget branch, four
  `optio-agents-all` registrations, Makefile lists and the demo trio.
- **Real thought, ~2 500–3 000 lines (the schedule)**: the wire protocol (`conversation.py`), install
  + launch + teardown (`host_actions.py` — the largest module in five of six and the least shareable),
  the lifecycle probe, and auth.

**Sharp edges for a newcomer:**

- **The declared `Conversation` Protocol is narrower than the real contract.** cursor/grok/kimicode
  also expose `attach`, `bootstrap`, `drain`, `emit_event`, `replay_history`, `session_id` and
  `set_controls_builder`, which the per-task listener calls. Implementing only the Protocol gives a
  class the listener cannot drive.
- `fs_isolation` defaults **True**, which makes `delivery_type` **mandatory** — a config omitting it
  raises at construction.
- Both halves of resume awareness are required: the `resume.log` pull section *and* the pushed
  `RESUME_NOTICE`, in every launch mode.
- The lifecycle probe is empirical, and three of six wrappers carry scars from getting it wrong:
  kimi's SIGTERM-ignore (`_eof_shutdown`), grok's SIGKILL-beats-token-flush (`_teardown_aggressive`),
  kimi's arbitrary session pick (`_recover_session_id`).

### 6.6 Duplication across wrappers

The extraction habit exists — `fs_grants`, `claustrum`, `model_probe`, `config_types` are all
genuinely shared — but has not been applied to several obvious candidates. Measured on the
comment-stripped token stream:

1. **`conversation_listener.py` — 5 copies, ~1 546 lines.** grok↔kimicode similarity **1.00**; the
   entire textual diff is 37 lines, nearly all comments, plus one statement reorder. It is
   engine-neutral and belongs in `optio-agents`.
2. `prompt.py` — 0.97–0.99 across four wrappers.
3. `types.py.__post_init__` — ~100 lines per wrapper of the same validation ladder, differing only in
   the class name in error strings. ~500 lines recoverable via a `ConversationConfigMixin`.
4. `seed_manifest.py` (0.90–0.94), `cred_watcher.py` (up to 0.98), `snapshots.py` (up to 0.98), demo
   tasks (0.95–0.97).
5. In the UI, the opposite: `acp/events.ts` *is* shared and grok/cursor are 11-line bindings — but
   `kimicode/events.ts` is a 305-line full copy of it.
6. Six of seven conversation views are ~150-line near-clones with identical
   `post`/`onSend`/`onInterrupt`/`onPermission`.

---

## 7. Wire surfaces

### 7.1 Engine RPC — 9 verbs

Contract `optio-contracts/src/optio-engine-to-api.ts:53`, clamator service `'optio-engine'`:
`launch`, `cancel`, `dismiss`, `groupCancel`, `groupCancelAndWait`, `blockLaunches`,
`unblockLaunches`, `materializeUpload`, and `resync` as a **notification**.

`launch.params.sessionId` is **required but nullable** by deliberate design: every caller must
consciously supply the initiating session token or an explicit `null`.

`make codegen` regenerates both sides; a **pre-commit hook** re-runs it and fails the commit on drift.

**Only `launch`, `cancel`, `dismiss`, `resync` and `materializeUpload` have an HTTP route.**
`blockLaunches`, `unblockLaunches`, `groupCancel` and `groupCancelAndWait` are reachable over RPC
only.

### 7.2 HTTP — the real paths

All under `pathPrefix: '/api'`. **There is no `:prefix` path segment** — `database` and `prefix` are
optional *query* params.

| Method + path | Notes |
|---|---|
| `GET /api/processes` | query `cursor limit=20 rootId state database prefix metadataFilter` |
| `GET /api/processes/:id` | `:id` is an ObjectId hex **or** an application `processId` |
| `GET /api/processes/:id/tree` | query `maxDepth` |
| `GET /api/processes/:id/log` | cursor is a numeric index |
| `GET /api/processes/:id/tree/log` | entries extended with `processId`, `processLabel` |
| `POST /api/processes/:id/launch` | body `{resume?, sessionId?}` |
| `POST /api/processes/:id/cancel` | no body |
| `POST /api/processes/:id/dismiss` | no body |
| `POST /api/processes/resync` | body `{clean?, metadataFilter?}`; returns **202**, unconditionally |
| `GET /api/optio/instances` | discovery; all four adapters |
| `POST /api/widget-control/:db/:prefix/:pid` | **fastify only**; body `{text}` or `{key}` |
| `POST /api/widget-upload/:db/:prefix/:pid` | **fastify only**; multipart, 100 MiB, → GridFS |
| `ALL /api/widget/:db/:prefix/:pid/*` | **fastify only**; HTTP + WS reverse proxy |

Status mapping: `not-found` → 404; `not-launchable` / `no-resume-support` / `launch-blocked` /
`not-cancellable` / `not-dismissable` → 409.

### 7.3 SSE — four channels

All poll Mongo at **1000 ms** and emit `data: <json>\n\n`.

| Path | Adapters |
|---|---|
| `GET /api/processes/stream` | all four |
| `GET /api/processes/:id/tree/stream` | all four |
| `GET /api/processes/tree/multi/stream` | fastify + nextjs-pages (intentional, per its design doc) |
| `GET /api/session-events/stream` | fastify only |

**There is no delta protocol.** Change detection is a `JSON.stringify` fingerprint and every change
emits a **full replacement** `update`. Log entries are the only append-only channel. The one genuine
exception is conversation transcripts, which are incremental and rely on SSE `Last-Event-ID` resume
implemented server-side in each engine's `conversation_listener.py` — and the views install no
`onerror`, so correctness there rests entirely on the server honouring it.

---

## 8. Constraints and hard requirements

**Runtime services.** MongoDB (required; GridFS and a TTL index). Redis (optional for the engine —
RPC server and heartbeat only; required in practice for the API layer). Python ≥ 3.11 (the engine uses
`ExceptionGroup`/`except*`). Node pinned `v24.18.0`, pnpm `11.1.1` via corepack.

**For agent tasks.** A Linux/macOS POSIX worker. `tmux` for codex/cursor/grok/antigravity. **ttyd
1.7.7**, auto-downloaded from GitHub. `curl` — opencode and kimicode pipe `smart-install.sh` from
`raw.githubusercontent.com` **on every task start**. Vendor installers reached over the network for
cursor, grok and antigravity. A real interactive login per engine to capture a seed, with reachability
to `auth.openai.com`, `auth.x.ai`, `auth.kimi.com` or `accounts.google.com`.

**Two wrappers depend on maintainer forks, not upstream:** `github.com/csillag/opencode` and
`github.com/csillag/kimi-code`.

**Pinned versions:** codex `rust-v0.142.5` (its whole wire-facts docstring is pinned to that build),
claustrum `v0.1.2`, ttyd `1.7.7`.

**Scale.** Every SSE client triggers an unprojected `find().toArray()` of the whole collection —
full documents including `log` arrays — once per second, per client. `getProcessTreeLog` loads every
tree process with full logs and sorts in memory. `buildTree` is N+1. `listProcesses` runs `find` +
`countDocuments` on every page. The transport cache is explicitly unbounded. The maintainers' own
`docs/2026-05-08-more-rpc-cleanup-todo.md` calls the polling "the wrong mechanism".

**Memory.** `archive_workdir` builds the whole tar.gz **in memory**, and restore accumulates every
chunk into one `bytes` before extracting. Workdir size is bounded by RAM.

**Testing discipline.** The no-wall-clock rule (`AGENTS.md:38-77`) is the longest single rule in the
repo and is framed as non-negotiable, because `make test` fans out in parallel on an oversubscribed
host. No sleep-then-assert, no reading async-populated state immediately, no tight positive-wait
ceilings, no count-over-wallclock. Wait on the **condition**, bounded by a generous 60 s hang-ceiling.
Verification requires looping a timing fix 10–20× under deliberate CPU contention.

**Conventions.** Docs go flat under `docs/`. No self-credit lines in commits. Describe intent and get
explicit confirmation before implementing. Update a package's `AGENTS.md` in the same commit as any
public-API change — a rule that is currently being violated at scale (§10).

---

## 9. Defects and risks found

Each was verified by reading. None has been acted on; none is covered by a test unless noted.

### 9.1 Security

| | Where | What |
|---|---|---|
| **SSH host keys disabled** | `optio-host/src/optio_host/host.py:668` | `known_hosts=None` on every remote connection. MITM exposure on any untrusted network. Self-labelled MVP. |
| **Unfiltered tar extraction** | `optio-host/src/optio_host/archive.py:182` | `tar.extractall(dest)` with no `filter=` and no member checks — a hostile archive can escape `dest`. |
| **Untested auth on the newest routes** | `optio-api` | The auth-bypass fix is fully present, including the mandated 10-case test block in all four adapters. But `widget-control` and `widget-upload` — added later — have **no auth test at all**. |
| **`@ts-nocheck` on all four adapters** | `optio-api/src/adapters/*.ts:1` | This is precisely what hid the original dangling `checkAuth` reference. Still there, explicitly out of scope. |
| **Unbounded database choice** | `optio-api` | Any authenticated caller picks `?database=` freely with no allowlist, and `discoverInstances` enumerates every database on the cluster. |
| **Credentials in URLs** | `widget-proxy-core.ts:91-99` | `applyInnerAuthQuery` puts upstream credentials in the query string, which `@fastify/reply-from` logs when `verbose: true`. |

### 9.2 Correctness

- **`_maybe_refresh_on_resume` drops protocol documentation.**
  `optio-claudecode/src/optio_claudecode/session.py:1472-1518` calls `compose_agents_md` **without**
  `documentation=`, so it falls back to the default `ProtocolFeatures`. For any task with
  `use_client_messages=True` or `on_caller_message` set, every resume silently strips the
  `CLIENT_MESSAGE:`/`CALLER_MESSAGE:` documentation from CLAUDE.md — while the parser still accepts
  those keywords — and spuriously tags every resume `REFRESHED:CLAUDE.md`. No test covers it.
- **Next.js adapters 500 on a valid id.** `nextjs-app.ts:153` and `nextjs-pages.ts:150` resolve the
  tree-stream `:id` with an unguarded `new ObjectId(id)`. The contract explicitly permits the non-hex
  `processId` form, so those two adapters throw where fastify and express correctly 404. The adjacent
  *multi*-stream path was fixed, with a comment at `nextjs-pages.ts:182-185` acknowledging exactly
  this divergence — the single-tree path above it was missed.
- **`shutting-down` is not on the wire.** `LaunchOutcome.reason` can be `"shutting-down"`
  (`optio-core/.../models.py:170`), which is absent from the wire enum
  (`optio-contracts/src/engine-failure-reasons.ts:10`) and from the generated `LaunchResult2`. An RPC
  launch during shutdown therefore raises a Pydantic `ValidationError` instead of returning a typed
  failure.
- **Filter-language asymmetry.** The wire `metadataFilter` accepts the full filtrum language while
  Python's `matches_filter` handles flat AND-equality only. A rich filter over `groupCancel` or
  `blockLaunches` reaches Python as a Pydantic model that `list_processes` would iterate as a dict and
  fail on.
- **Missing null guards.** `getProcessLog` (`handlers.ts:145`) and `getProcessTreeLog` (`:182`) index
  `proc.log` with no guard while the pollers use `(p.log ?? [])`. A row without `log` 500s the REST
  routes. `toResponse` (`:22-30`) unconditionally calls `proc.rootId.toString()`.
- **List-stream fingerprint omits emitted fields.** `name`, `metadata`, `special`, `warning` and
  `cancellable` are in the payload but not in the change fingerprint, so changes to only those never
  push an event.
- **`sessionId` is fastify-only.** The other three adapters always send `null`, so session-event
  correlation silently cannot work there.
- **`LocalHost.tail_file` leaks.** It stores a single `_tail_proc`, so a second concurrent
  `tail_file` on the same host overwrites the handle and leaks the first process.
- **Dead option.** `prefix` is declared on `BaseOptioApiOptions` in all four adapters and read
  nowhere, yet documented as the collection prefix in two places.

### 9.3 Packaging

`optio-agents` imports `pymongo` at module top (`seeds.py:27`) and `aiohttp`
(`input_listener.py:19`) but declares **neither** in its `pyproject.toml`. Both arrive transitively
today; both are latent breakage.

### 9.4 Process

- **No CI at all** — verified, no `.github/`. Releases are gated only by a local preflight.
- **`make test` skips `packages/optio-demo`** — it is absent from `PY_PACKAGES` (`Makefile:4`) though
  present in `RELEASABLE_PY`. So the nine demo test files, **including the cross-engine
  `test_config_parity.py` guard that keeps seven wrappers honest**, never run in the standard suite.
- **No CHANGELOG**, no dry-run/TestPyPI mode, no pre-release tags, no npm provenance, no PyPI trusted
  publishing — all six deferrals from the release-infrastructure design remain open.
- **Two permanently skipped opencode tests** lose launch-env propagation coverage, local and remote.

---

## 10. Documentation trust map

Documentation drift is the dominant practical risk in this repo, and it is not cosmetic: the core
docs omit a **required argument**, the contract docs give **HTTP paths that do not exist**, and the
host README's **only example cannot execute**.

### Trust these

- `docs/writing-agent-wrappers.md` (1028 lines) — the standing guide, present-tense, deliberately
  code-free so it does not rot. Caveats in §10.3 below.
- `docs/release-cookbook.md` — self-labelled living reference.
- `filtrum-core` and `filtrum-mongo` README + AGENTS.md — **the only docs in the repo with no
  code disagreement found**, including the error string quoted verbatim.
- `optio-codex/README.md` — the only current wrapper README; names its audit and its two deliberate
  gaps.
- The parity ledgers `docs/2026-07-03-optio-kimicode-parity.md` and
  `docs/2026-07-02-optio-codex-parity-audit.md` — kept current by dated in-place updates with explicit
  `TRACKED GAP` entries.

### Do not trust these without checking the code

- **`packages/optio-ui/AGENTS.md`** (586 lines) — materially stale, ~15 concrete divergences: three
  wrong SSE URLs, four query keys missing `database`, five incomplete prop interfaces, version 0.1.0
  vs 0.3.1, and two entirely undocumented features (the `iframe-input` widget and
  `MultiProcessStreamProvider`). It also contradicts itself on the widget proxy URL.
- **Every route table except optio-api's AGENTS.md.** The `optio-api` README and
  `optio-contracts/AGENTS.md` both document `/api/processes/:prefix/...`. No such route exists — one
  unpropagated pre-multi-db revision, replicated.
- **`optio-core` docs on command signatures.** `session_id` is a **required keyword-only argument** on
  `launch`, `launch_and_wait` and `launch_and_await_result` with no default; every doc including root
  `AGENTS.md:151` shows the old signature. And commands return outcome objects
  (`LaunchOutcome`/`CancelOutcome`/`DismissOutcome`, `resync → dict`, `group_cancel* → int`), all
  documented as `-> None`. **Code written from the docs `TypeError`s on the first call.**
- **`optio-host/README.md`** — its only example is wrong in three ways at once: `make_host` requires
  a keyword-only `taskdir`, no host class defines `__aenter__`/`__aexit__`, and there is no `run()`
  (it is `run_command(command: str)` taking a shell string).
- **`optio-claudecode/AGENTS.md`** — names a renamed field (`claude_install_dir`, actively forbidden
  by a parity test), two wrong widget names, a missing permission mode, a superseded description of
  `claude_config`, and misses two listener endpoints. Both README quick-start examples **raise
  `ValueError` as written** (no `delivery_type` with `fs_isolation` defaulting True).
- **`optio-opencode/AGENTS.md`** (292 lines) — documents a removed field (`opencode_install_dir`,
  with a test asserting it is gone) and describes free functions as `Host` methods. Its "Known limits
  (MVP)" section is still labelled MVP at version 0.4.0.
- **`optio-demo/AGENTS.md`** — documents a `TaskInstance(id=, fn=, cron=)` API **that never
  existed**, and omits 9 of the 14 task modules.
- **Three wrapper READMEs** (cursor, grok, antigravity) and four `session.py` docstrings still say
  "Stage 0 (MVP)… resume, seeds, conversation mode arrive later" while all of it ships.
- **Plan checkboxes.** Every `-plan.md` in the corpus is 0-of-N ticked even for demonstrably shipped
  work. They are execution inputs handed to subagents and never checked back in. **Useless as a
  status signal** — infer completion from the tree and the git log instead.

### `writing-agent-wrappers.md` caveats

The guide is the best document in the repo and still has six known gaps: Part 2B **omits
`set_control`** from the `Conversation` method list (Appendix B lists it — the two halves disagree);
Appendix D still documents the deleted `default_model`; Part 2A's callback list omits `keywords`,
`browser_url_rewrite` and the before/after hooks; Appendix A row 8 predates the `seed_blob_*` split;
the Testing section claims both reference wrappers ship a docker-sshd harness, which is **false for
`optio-claudecode`** — the designated reference wrapper is in fact the least covered of the shipped
wrappers for real-binary and remote work.

### The coordination rule is not being met

Root `AGENTS.md:31-36` mandates same-commit `AGENTS.md` updates on any public-API change. Verified:
**9 of 20 packages have no `AGENTS.md` at all**; root `AGENTS.md` omits `ttl_seconds`, `auto_resume`,
`publish_result`, `launch_and_await_result`, `run_child_task` and the six `init()` timing knobs
entirely; its `OptioProviderProps` is wrong against the real 7-prop interface; and five symbols the
dashboard actually imports appear nowhere in either AGENTS.md.

---

## 11. Direction and open threads

### The arc

1647 commits, 2026-03-25 → 2026-07-19, under a different name at first ("feldwebel").

- **03 — genericization.** App-specific filters out, generic metadata filter in. The zero-code
  dashboard designed.
- **04 — widgets, the first agent, a cancellation sprint.** An external consumer's request list seeds
  the whole widget system. `optio-opencode` is born, followed immediately by platform-wide resume and
  `HookContext`. A dense reliability run at month end, then the first layering move: `optio-host`
  carved out with an explicit one-way import rule.
- **05 — the architectural month.** The engine-RPC migration (five phases) replaces fire-and-forget
  Redis commands with typed RPC and writes the authority rule verbatim. Phase 5's principle: "the
  public method **is** the implementation. The RPC adapter is thin." Second agent (claudecode), then
  `optio-agents` extracted as the keyword-protocol SSOT.
- **06 — the conversation pivot.** From "embed a terminal" to "native chat UI": headless stdio, a live
  `Conversation` object, the React chat widget, and **claustrum** — default-on, fail-closed Landlock
  isolation, local and remote.
- **07 — industrialization.** The wrapper guide is written on 07-01; **three wrappers are designed on
  07-02**, a fourth on 07-03, a fifth on 07-06. Then the de-duplication program: generic session
  controls, config harmonization, the meta-factory, one generic upload path, canonical agent metadata,
  universal claustrum.

**Every doc from 2026-07-05 onward is a de-duplication / uniformity doc, not a new-capability doc.**
The direction is unmistakable: *seven bespoke wrappers → one platform with seven backends*.

HEAD is ten days past the newest design doc; the last two weeks of work (the `seed_blob` channel, the
mechanical-deliverable-rejection reply, caller `claude_config` layering, the 0.2/0.3/0.5 release wave)
has **no design doc**.

### Open threads, by weight

1. **Account analysis — the live frontier.** Newest design (2026-07-09), **zero code**:
   `optio_agents/account.py` does not exist and `analyze_account`/`AccountInfo` have zero occurrences
   anywhere. The frame plan covers claudecode plus a coupled excavator update; **six per-engine
   analyzer plans are research-gated and unwritten**. The motivating failure is concrete and still
   live: an antigravity seed hit "Individual quota reached. Resets in 121h" and nothing detected it —
   **the seed still reads as usable**.
2. **Antigravity's spikes never ran.** Two of three pre-stage spikes were marked "REQUIRES USER" (a
   real Google login). Neither ran; the wrapper shipped and released against the *assumed* branch of
   each. Five `TODO(S…)` markers mark the unverified assumptions, including whether
   `doc["AutoUpdate"] = False` actually suppresses the updater — if it does not, a pinned binary can
   silently update under a running task.
3. **Post-RPC polling cleanup — a seed doc that was never designed.** All five items still open,
   including the real architectural decision: the API polls Mongo on an interval instead of the engine
   pushing change events. The TODO cited two `setInterval(poll, 1000)` sites; there are now four.
4. **`optio-ui` duplicates the engine's state sets** — deferred three separate times. Blocked on
   needing *runtime* (not type-only) exports from `optio-contracts`. No spec written.
5. **Twelve tracked codex/grok shared-template items**, deliberately unforked and fixed together —
   including an error vocabulary missing from snapshot `endState`, a transport failure that silently
   degrades to a fresh session, and a cred watcher that saves back *before* renewing its lease.
6. **Ten documented kimicode real-binary gaps** — this is the *good* pattern: named, tracked, with
   opt-in env vars, exactly as the guide requires.

### Two meta-rules quoted in the newest docs

- **The real-binary rule.** Verify against real agent binaries, not fakes, before claiming a
  capability works. The guide records three regressions that shipped green.
- **The seed-safety rule.** Never copy a seed into a throwaway database and refresh it — a refresh
  rotates the single-use refresh token upstream at the vendor. Use the real database plus lease plus
  save-back.

---

## 12. Verification notes

- Every `file:line` above was read in the working tree at `f5da73ab`. No test suite was run and no
  code was executed.
- Items explicitly marked *not verified*: whether any consumer outside `optio-demo` calls
  `optio_agents_all.create_task`; whether the 2 s body-exit tail grace is exercised by a test; the
  behaviour of `@clamator/protocol` and `@clamator/over-redis` internals; the two out-of-repo
  consumers named in the docs ("excavator", "windage"), for which no source is present here.
- The defects in §9.2 were found by reading and are, with the noted exception, uncovered by tests —
  so they are *verified as present in the source*, not *verified as failing at runtime*.
