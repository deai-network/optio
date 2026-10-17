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

## optio-ui

- `usePrefixes` / `usePrefixDiscovery` call `client.discovery.prefixes`, which
  `discoveryContract` does not have: dead exports. **Read**

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

## clamator codegen 0.1.9

- The generated TS client imports `<camelCase service>Contract` from the
  `--ts-contract-import` module, whatever the contract's export is called. The coupling
  is undocumented; a mismatch only shows up when the API typechecks. **Seen**
- Python output is named after the service with a hyphen (`pressroom-engine.py`), which
  Python cannot import; every app renames it in its Makefile. **Seen**
- No `__init__.py` is written into `--out-py`. **Seen**
- `--src` is scanned only at the top level. **Read** (documented in the guide)
