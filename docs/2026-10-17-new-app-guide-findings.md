# Findings from writing the new-app guide (2026-10-17)

Notes for a cleanup round, collected while building the pressroom scratch app for
`docs/how-to-set-up-a-new-optio-app.md`. **Seen** = observed running it; **Read** =
from reading the code or docs only.

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
  fails as a 500 after the 30 s clamator timeout. **Read** (to verify in the guide's
  troubleshooting appendix)
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
- optio-ui renders no widget unless `OptioProvider` knows the database (discovery fills
  it only with exactly one visible instance); only a `console.warn` says so. **Read**
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

## Vite dev proxy (affects excavator too, probably)

- Vite 8's `server.proxy` does not close the upstream request when an HTTP/2 client
  abandons a stream (an EventSource on a page you leave): 45-50 leaked SSE connections
  to the API in ~15 minutes. Over HTTP/1.1 it closes them. Fix used in the guide:
  `configure: proxy => proxy.on('proxyReq', (proxyReq, _req, res) => res.on('close', ()
  => { if (!res.writableEnded) proxyReq.destroy(); }))` -- note `writableFinished` is
  already true on an aborted HTTP/2 response. Excavator's http-proxy-middleware plugin
  has no such handling either; worth checking there. **Seen**
- Excavator's vite.config.ts comment says server.proxy forces HTTP/1.1; true for Vite 6,
  not for 7/8 (http2.createSecureServer with allowHTTP1 in both cases). **Read**

## optio-dashboard 0.2.4 (published)

- Filter bar shows raw i18n keys: `processes.search`, `processes.filterAll`,
  `processes.showDetails`, `processes.showSpecial`. **Seen**
- Pins optio-ui 0.8.1 (current 0.8.2) and optio-conversation-ui 0.9.1. **Read**
- Without a database in `MONGODB_URL`, it lists every optio instance on the server
  and stores its login collections in a database named `optio`. Documented in the
  guide; maybe warn at startup. **Seen**
- Prints a Fastify deprecation warning at startup (`disableRequestLogging`). **Seen**

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
