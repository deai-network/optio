# Findings from writing the new-app guide (2026-10-17)

Notes for a cleanup round, collected while building the pressroom scratch app for
`docs/how-to-set-up-a-new-optio-app.md`. **Seen** = observed running it; **Read** =
from reading the code or docs only.

## Security

- **optio-api leaks `controlUpstream` credentials to clients.** `stripServerSideFields`
  (handlers.ts) removes only `widgetUpstream`, but optio-core also stores
  `controlUpstream` (an agent session's input listener: `{url, innerAuth}`) on the process
  record. Reproduced on pressroom with a fake value: its password came back in
  `GET /api/processes/<id>`, `GET /api/processes` and `GET /api/processes/<id>/tree`
  (the SSE streams use projections and do not include it). Any caller allowed to read
  processes, a `viewer` included, gets the credentials. Fix: strip `controlUpstream` too
  (better: strip by an allow-list or every `*Upstream`). **Seen**
  **Fixed** in optio-api 0.3.1 (ed6ed7e4); optio-dashboard 0.2.5 pins it.

## Docs vs code (left open by the docs pass)

- README / AGENTS.md say optio-api performs no database writes; `/api/widget-upload`
  stages files in GridFS and deletes them afterwards (upload-forward.ts). Decide which
  is wrong. **Read**
- optio-core's `launch` can answer `shutting-down`, but the contract's
  `LaunchFailureReason` and optio-api's status map do not include it: a launch during
  shutdown probably fails result validation or gets no HTTP status. **Read**

## Stale documentation (would break code copied from it)

- optio-core README: `launch_and_wait("my-task")` lacks the now-required keyword
  `session_id` (TypeError). **Read**
- optio-core README: `async def get_tasks(services)` takes one argument; optio calls
  it with two, `(services, metadata_filter)`. **Read** (code at lifecycle.py:1680)
- optio-core: the ImportError for a missing redis extra says `pip install optio[redis]`;
  the package is `optio-core`. **Read**
- optio-api README quick setups omit the required `authenticate` option. **Read**
- Route tables with a `:prefix` path segment, which no longer exists (prefix is a query
  parameter): optio-contracts README/AGENTS.md, root AGENTS.md. **Read**
- optio-ui README: peer `vultus-core >=0.4.0` (package.json says `>=0.5.3`); its
  cross-origin `baseUrl="http://localhost:3000"` example cannot carry cookies on SSE.
  **Read**
- optio-ui AGENTS.md says 0.7.0 and shows old `OptioProviderProps`; optio-contracts
  AGENTS.md says 0.1.0 and "private"; root AGENTS.md shows old `init`, `launch` and
  `OptioProvider` signatures. **Read**
- optio-dashboard README omits the required `OPTIO_PASSWORD`. **Seen**
- docs/optio-capabilities.md (written against 4aebde18): says there is no tenant
  scoping, that `prefix` is dead, that streams poll, that `checkAuth` is not exported;
  all changed since. **Read**
- No optio doc shows app-level migrations (quaestor) or `deferred_migrations` for an
  app; optio-demo has none. The guide now covers the basic case.

## optio-api

- `registerOptioApi` (Fastify) adds `authenticate` as a root-level `onRequest` hook:
  it gates every route of the host app, including the app's own routes and static
  files (401). Registering it inside an encapsulated plugin
  (`app.register(async scope => registerOptioApi(scope, ...))`) confines it to optio's
  routes. Consider doing that inside `registerOptioApi`. **Seen**
- Depends on `mongodb ^6.12` and `ioredis ^5.4`; current majors are mongodb 7 and
  ioredis 6. Apps must stay on the older majors to share one copy. **Seen**
- Routes without `?prefix=` silently use `optio` and return an empty list. A warning
  (or 400 when the prefix has no collection) would save debugging. **Seen**
- Engine RPC calls in handlers have no try/catch: with the engine down, a command
  fails as `500 {"message":"call timeout"}` after 30 s. **Seen**
- **A call that timed out is still executed later**: a launch answered `call timeout`
  while the engine was stopped ran the moment the engine came back (the request waits
  in the Redis stream). Surprising for a user who got an error; consider an expiry on
  requests (clamator) or mapping the timeout to a "may still happen" reason. **Seen**
- `@ts-rest/fastify` 3.52.1 declares `fastify ^4`; works with 5 (peer warning).
  Upstream. **Seen**

## Widgets

- optio-api AGENTS.md (435-452) and optio-ui AGENTS.md (381) still describe a widget URL
  `/api/widget/:processId/*`; the real one is `/api/widget/:db/:prefix/:pid/*`. **Read**
- No optio doc shows a minimal app-defined widget end to end (engine `ui_widget` +
  `set_widget_upstream` + `set_widget_data`, UI `registerWidget`); the guide's section 10
  now does. **Seen**
- Right after a process ends, the widget proxy answers 502 for up to 5 s (cached
  upstream, now closed), then 404. Could drop the cache entry when the upstream is
  cleared. **Seen**
- optio-ui renders no widget unless it knows the database; discovery fills it in when
  exactly one instance is visible (a widget showed without the `database` prop on
  pressroom). With several instances only a `console.warn` would say so. **Seen/Read**
- `store_blob` blobs are never cleaned up by optio (relaunch, dismiss, delete). **Read**
- optio-conversation-ui depends on optio-ui as a regular dependency (exact version), not
  a peer; a version mismatch means two widget registries. **Read**

## optio-ui

- `usePrefixes` / `usePrefixDiscovery` call `client.discovery.prefixes`, which
  `discoveryContract` does not have: dead exports. **Read**

- optio-ui components use antd APIs deprecated in antd 6.6: `Progress size="default"`
  (use `"medium"`) and the `List` component; every page logs the warnings. **Seen**
- optio-ui's i18n contract is undocumented in its README beyond a partial table; the
  guide lists the required keys (`common.noData`, `status.*`, `processes.launch|cancel|
  search|filterAll|filterActive|filterHideCompleted|filterErrors|showDetails|
  showSpecial`). Some texts are hardcoded English (ProcessTreeView "Live", "Hide
  finished sub-tasks"; ProcessDetailView "Select a process to view details", ...). **Read**
- `useProcessActions` is fire-and-forget (no error reporting), so it cannot back a
  `useAction` with an error route; excavator's `useAgentSetup` / `EntityOverview`
  `checkAction` set `errorRoute` on such calls, which never fires. **Read**
- `useProcessListStream` is a per-tab singleton without cleanup on unmount;
  `useProcessStream` never retries after a 404 probe. **Read**

## vultus

- `useAction` reads the form-error context where the hook is called, not where
  `FormSubmitButton` renders. Creating the action in the component that renders
  `<FormPanel>` sends field errors to a pop-up instead of the field. Excavator's
  `AddSourceModalBody` has that shape. Either `FormSubmitButton` should hand its context
  to the action, or this needs documenting. **Seen** (pressroom)
- vultus-antd depends on vultus-core (`^0.5.3`) as a regular dependency, not a peer; a
  registry install got two vultus-core copies (0.5.3 and 0.5.4) until `pnpm dedupe`.
  Make it a peer. **Seen**

## Vite dev proxy

- Vite 7/8's built-in `server.proxy` does not close the upstream request when an HTTP/2
  client abandons a stream (an EventSource on a page you leave): 45-50 leaked SSE
  connections to the API in ~15 minutes. Over HTTP/1.1 it closes them. Fix used in the
  guide: `configure: proxy => proxy.on('proxyReq', (proxyReq, _req, res) => res.on('close', ()
  => { if (!res.writableEnded) proxyReq.destroy(); }))` -- note `writableFinished` is
  already true on an aborted HTTP/2 response. **Seen**
- Measured on 2026-10-17 (same test: an SSE stream over HTTP/2, client dropped): excavator's
  dev server (http-proxy-middleware plugin, HTTP/2) and optio-dashboard's (Vite 6, whose
  proxy mode serves HTTP/1.1 even over HTTPS) both close the upstream connection: **no
  leak, nothing to fix.** **Seen**
- Excavator's vite.config.ts comment says server.proxy forces HTTP/1.1; true for Vite 6
  (and confirmed with optio-dashboard), not for Vite 7/8. **Seen/Read**

## optio-dashboard 0.2.4 (published)

- Filter bar shows raw i18n keys: `processes.search`, `processes.filterAll`,
  `processes.showDetails`, `processes.showSpecial`. **Seen**
  **Fixed** in optio-dashboard 0.2.5 (b29446bb): a fresh build translates every label;
  `processes.search` added, i18next/react-i18next deduped.
- Pins optio-ui 0.8.1 (current 0.8.2) and optio-conversation-ui 0.9.1. **Read**
- Without a database in `MONGODB_URL`, it lists every optio instance on the server
  and stores its login collections in a database named `optio`. Documented in the
  guide; maybe warn at startup. **Seen**
- Prints a Fastify deprecation warning at startup (`disableRequestLogging`), and
  `@fastify/reply-from might not behave as expected when used with @fastify/multipart`
  (optio-api's adapter; every optio-api app logs it). **Seen**
- Sign-in works only from `http://localhost:<PORT>`: `http://127.0.0.1:<PORT>` or a
  machine name fails with "Invalid origin" (better-auth trustedOrigins). It listens on
  all interfaces, so the address it is reachable at and the one it accepts differ.
  **Seen** (by the fresh-reader test)

## optio-core

- `init(rpc_server=...)` does not run the heartbeat, so discovery reports the
  instance `live: false`. **Read**
- During shutdown the supervisor loop exits (`while self._running`), so stubborn
  tasks are force-cancelled only at grace + drain margin, not at grace as the comment
  at lifecycle.py:1396 and docs/2026-04-29-deadline-driven-cancel-design.md:348 say.
  Observed: engine exit 15.9 s after Ctrl-C with grace 10. **Seen** (timing) / **Read**
- The engine ignores a second SIGINT (by design?). Under watchfiles, the second
  Ctrl-C kills watchfiles with a KeyboardInterrupt traceback and the prompt returns
  while the engine still shuts down in the background. **Seen**
- optio-core README is stale on cancellation and shutdown: "a task that never checks
  should_continue() cannot be cancelled (it will remain in cancelling)" (line ~175),
  `shutdown()` "waits up to 5 seconds", no timing knobs in the `init()` table, no
  `supports_resume` / `auto_resume` / `auto_cancel_children` in the TaskInstance table.
  Root AGENTS.md: `shutdown(grace_seconds=5.0)` (the default is None = the cancel
  grace), no `auto_resume`. **Read**
- `cancel_and_wait` exists but is not exported from `optio_core`. **Read**
- The executor's final-state writes are unconditional, so a late `cancelled` can
  overwrite a forced `failed`; a force-cancelled sequential `run_child` throws
  `CancelledError` into the parent's code too. Worth a look. **Read**
- Shutdown sets no `cancel_requested`/`cancelling` states (goes running -> cancelled),
  and `cancellable=False` does not protect a task from shutdown. Probably right, but
  undocumented. **Read**

## clamator codegen 0.1.9

- The generated TS client imports `<camelCase service>Contract` from the
  `--ts-contract-import` module, whatever the contract's export is called. The coupling
  is undocumented; a mismatch only shows up when the API typechecks. **Seen**
- Python output is named after the service with a hyphen (`pressroom-engine.py`), which
  Python cannot import; every app renames it in its Makefile. **Seen**
- No `__init__.py` is written into `--out-py`. Harmless: setuptools' pyproject-based
  package finding includes namespace directories (checked with a wheel build). **Seen**
- `--src` is scanned only at the top level. **Read** (documented in the guide)

## Found during the docs pass (code, not docs)

The docs pass (branch csillag/docs-cleanup) corrected the documentation; these are
behaviors in the code it ran into.

### optio-core

- **A full sync deletes root ad-hoc processes.** `init()`, the start of `run()` and
  `resync()` without a filter treat every root process the generator does not produce as
  stale (`find_stale` / `remove_stale` match `processId not in generator, parentId None`),
  so a process from `adhoc_define` is deleted by the next full sync. Reproduced:
  `adhoc_define` → present, `resync()` → gone. An ad-hoc process defined between
  `init()` and `run()` is deleted when `run()` starts. **Seen**
- `launch_and_wait` skips `launch()`'s checks (shutting-down, no-resume-support, the
  saved-state downgrade) and returns `None` silently when the process is missing or not
  launchable. **Read**
- `group_cancel_and_wait(purge_records=True)` returns before purging when nothing
  matching is active, so terminal records stay; `group_cancel` purges in that case.
  **Read**
- `VALID_TRANSITIONS` / `validate_transition` are used only by tests, and the engine's
  real transitions differ from them. **Read**
- The `[redis]` extra is redundant: `clamator-over-redis`, a hard dependency, requires
  `redis`, so the "install optio-core[redis]" ImportError branch cannot be reached in a
  normal install. **Read**
- `report_progress`'s docstring says percent is 0..100 but its examples use 0.42 and
  1.0. **Read**

### optio-api

- The Express and both Next.js adapters drop the browser's `sessionId` on launch (they
  pass `null`); only Fastify forwards it, so per-browser attention / client messages work
  only on Fastify. **Read**
- The Next.js adapters' tree and multi-tree lookups accept only a Mongo `_id`
  (`new ObjectId(id)`); the App Router's tree stream throws on a `processId`. **Read**
- The documented Next.js route files (`[...optio]` catch-alls under `/api/processes`)
  probably do not match `GET /api/processes` itself or `/api/optio/instances`; a
  `[[...optio]]` file or a catch-all at `/api` may be needed. Not checked (no Next.js app
  in the repo). **Read**

### optio-ui

- `OptioProvider` passes its `prefix` prop, not the discovered prefix, to
  `startSessionEvents`: with discovery (no `prefix` prop), `onAttention` and
  `onClientMessage` never connect. **Read**
- `ProcessTreeView` shows its stop button for `isActive && cancellable` (including
  cancel_requested / cancelling); `ProcessItem` uses `isCancellable`. **Read**
- `FilteredProcessListProps.onLaunch` is typed without the `opts` argument. **Read**
