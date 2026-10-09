# optio-api — LLM Reference

## Architectural rule

**Engine owns all writes.** This package reads MongoDB directly for queries (REST GETs, SSE streams, widget proxy) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. The engine is the single source of truth for what commands are allowed and what state results.

---

## Package

- **name**: `optio-api`
- **entry points**:
  - `optio-api` → `dist/index.js` / `dist/index.d.ts`
  - `optio-api/fastify` → `dist/adapters/fastify.js` / `dist/adapters/fastify.d.ts`
- **dependencies**: `optio-contracts`, `@ts-rest/core`, `mongodb`, `ioredis`, `@clamator/protocol`, `@clamator/over-redis`, `zod`
- **note**: `zod` is declared explicitly here even though `optio-api` does not import it directly. `@clamator/protocol` declares `zod` as a peerDependency; without an explicit declaration in this package, pnpm can resolve `@clamator/protocol`'s peer requirement against a different physical `zod` copy than the one used by `optio-contracts`, which causes TypeScript to reject the codegenned `_generated/optio-engine.ts`. See `@clamator/protocol`'s README for the canonical statement of this consumer requirement.
- **optionalDependencies**: `@ts-rest/fastify`
- **peerDependencies**: `fastify ^5.2.0` (optional)

## Layered architecture

| Layer | Provides | Audience |
|-------|----------|----------|
| 1 | `createOptioTransports(redis): OptioTransports` — cache of `RpcClient` per `(database, prefix)` | RPC-only consumers (e.g., Excavator) + custom HTTP adapter authors |
| 2 | `createOptioContext({ dbOpts, redis }): OptioContext` — bundles `dbOpts`, `transports`, `redis`, `closeAll` | HTTP hosts (typical) |
| 3a | `registerOptioApi(app, { ctx, authenticate })` (or sugar form) — binds HTTP routes onto a framework | HTTP hosts |

The transport cache (Layer 1) is contract-agnostic. Any clamator contract — `OptioEngineClient` plus future consumer contracts — wraps a cached transport. RPC-only consumers stop at Layer 1; HTTP-handler code uses Layer 2.

## Exports

Re-exported from the main `optio-api` entry point:

- `OptioEngineClient` (re-exported from `_generated/optio-engine.ts`) — typed clamator client for the optio-engine RPC contract. Constructed as `new OptioEngineClient(transport)`.
- `createOptioTransports(redis: Redis): OptioTransports` — Layer 1 factory. Caches one `RpcClient` per `(database, prefix)`. Audience: RPC-only consumers, custom adapter authors.
- `OptioTransports` (type) — interface `{ get(database: string, prefix: string): RpcClient; closeAll(): Promise<void> }`.
- `createOptioContext({ dbOpts, redis }): OptioContext` — Layer 2 factory. Wraps Layer 1 + Mongo `Db`.
- `OptioContext` (type) — interface `{ dbOpts: DbOptions; transports: OptioTransports; redis: Redis; closeAll(): Promise<void> }`. Threaded into every handler call.
- `resolveDb(dbOpts, query)` — extract `db`, `database`, `prefix` from a query plus dbOpts.
- `resolveOptioEngine(ctx, query): OptioEngineClient` — Layer 2 helper combining `resolveDb` and `new OptioEngineClient(ctx.transports.get(...))`. Use inside per-request handler paths.

### Patterns

External RPC-only consumer (e.g., Excavator):

```typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';

const transports = createOptioTransports(redis);
const optioEngine = new OptioEngineClient(transports.get('mydb', 'optio'));
await optioEngine.launch({ processId: 'foo' });

// Consumer's own contract on the same transport infrastructure:
const myClient = new MyDomainClient(transports.get('mydb', 'mydomain'));
```

HTTP host:

```typescript
import { createOptioContext, registerOptioApi, resolveOptioEngine } from 'optio-api';

const ctx = createOptioContext({ dbOpts: { db }, redis });
registerOptioApi(app, { ctx, authenticate });  // explicit form
app.addHook('onClose', () => ctx.closeAll());

// Programmatic engine access in the host's own code:
const engine = resolveOptioEngine(ctx, {});
await engine.launch({ processId: 'foo' });
```

Sugar form (host doesn't need to manage ctx explicitly):

```typescript
const ctx = registerOptioApi(app, { db, redis, authenticate });
// fastify wires onClose to ctx.closeAll automatically in the sugar form.
```

## OptioApiOptions

```typescript
interface OptioApiOptions {
  db: Db;                                  // MongoDB Db instance
  redis: Redis;                            // ioredis Redis instance
  prefix?: string;                         // Limits instance discovery
                                           // (GET /api/optio/instances) to this
                                           // prefix; unset, every prefix. Routes
                                           // take the prefix from each request's
                                           // query (default 'optio').
  authenticate: AuthCallback<TRequest>;    // TRequest depends on adapter (FastifyRequest,
                                           // express Request, web Request, NextApiRequest).
                                           // Returns 'viewer' | 'operator' | null.
  scope?: ScopeCallback<TRequest>;         // Optional tenant boundary. See "Access scope".
  authorize?: AuthorizeCallback<TRequest>; // Optional per-action decision. See "Access scope".
}
```

## Authentication

The `authenticate` callback is invoked on every request to every route across
all four adapters:

- REST endpoints from `processesContract` under `/api/processes/...`
- SSE streams: `/api/processes/stream` and `/api/processes/:id/tree/stream`
- Discovery: `/api/optio/instances`
- Widget reverse-proxy under `/api/widget/...` (Fastify only)

The callback receives the framework-native request object and returns an
`OptioRole` (`'viewer'` or `'operator'`) or `null`. Returning `null` denies
the request with `401 Unauthorized`. `'viewer'` permits safe HTTP methods
(`GET`, `HEAD`, `OPTIONS`); `'operator'` permits all methods. A mutating
method with a `viewer` role yields `403 Forbidden`.

Enforcement is implemented per adapter via the framework's request-lifecycle
hook (Fastify `onRequest`, Express `app.use('/api', …)`, Next.js inline at
the top of `GET`/`POST` or the returned Pages handler). The Fastify
widget-proxy plugin's `preHandler` retains its own `checkAuth` call as
defense in depth.

## Access scope and authorization

Two optional hooks, accepted by all four adapters next to `authenticate`, let
a multi-tenant host confine each request to its tenant's processes and decide
individual actions. Types live in `auth.ts`; the shared enforcement in
`access-scope.ts` and the handlers.

```typescript
type ScopeFilter = Record<string, string | number | boolean | null>;
type ScopeCallback<TRequest> = (req: TRequest) => ScopeFilter | null | Promise<ScopeFilter | null>;

type OptioAction = 'read' | 'launch' | 'cancel' | 'dismiss' | 'resurrect' | 'resync'
  | 'widget' | 'widget-control' | 'widget-upload' | 'instances';
interface ProcessRef { _id: string; processId: string; name: string; parentId?: string; rootId: string; metadata: Record<string, unknown> }
interface AuthorizeInput {
  role: OptioRole; action: OptioAction;
  process?: ProcessRef;                       // single-process actions
  metadataFilter?: Record<string, unknown>;   // resync: the effective (scoped) filter
  clean?: boolean;                            // resync
}
type AuthorizeCallback<TRequest> = (req: TRequest, input: AuthorizeInput) => boolean | Promise<boolean>;
```

`'resurrect'` is a single-process action like `'launch'`: `authorize` receives the
target `process`.

**`scope(req)`** returns a flat exact-match metadata map, or `null` / `{}` for
an unscoped request. A process is in scope when every key equals its
`metadata` value (strict equality); a process whose own metadata lacks a key is
judged by its root's; one whose root lacks it too is out (fail closed).
Enforcement:

| Route | Out of scope |
|---|---|
| `GET /api/processes`, list stream, session-events stream | excluded: the scope is `$and`ed into the query, so a client filter cannot widen it |
| `GET /api/processes/:id`, `/tree`, `/log`, `/tree/log`, `/:id/tree/stream` | 404 `Process not found` |
| `GET /api/processes/tree/multi/stream` | reported in `missing` |
| `POST /api/processes/:id/launch` / `cancel` / `dismiss` / `resurrect` | 404 `{ reason: 'not-found' }`; the engine is not called |
| widget proxy, `widget-control`, `widget-upload` (Fastify) | the route's not-found response |
| `POST /api/processes/resync` | runs with the client's flat filter plus the scope keys (the engine filters regenerated tasks and pruned records by it); 403 if the filter contradicts the scope, 400 for a predicate-tree filter |

**`authorize(req, input)`** runs after the scope check for launch, cancel,
dismiss, resurrect, resync, the three widget routes and `GET /api/optio/instances`;
`false` gives **403** `{ message: 'Forbidden' }` and the engine is not called.

Without either hook behaviour is exactly as before: commands go straight to
the engine and no extra lookups run.

Per request, `checkAuth` records the authenticated role (keyed by the native
request object) and `accessFor(req, hooks)` resolves the hooks once into an
`Access` (`{ restricted, scope, authorize }`) that adapters pass to the
handlers. The Next.js adapters carry it into the ts-rest routes through an
`AsyncLocalStorage`, because ts-rest's Next handlers see their own request
objects. Custom adapters: call `checkAuth`, then `accessFor`, then pass the
`Access` as the handlers' trailing argument and use `findScopedProcess` /
`gateProcess` / `gateInstances` for routes outside the handlers.

## Fastify Adapter

Imported from `optio-api/fastify`.

**Return shape (two-form):**

`registerOptioApi(app, opts)` accepts either:

- **Sugar form** — `{ db | mongoClient, redis, authenticate, prefix?, verbose? }`. Adapter constructs an `OptioContext` internally, wires fastify's `onClose` to `ctx.closeAll()`, and returns the constructed `OptioContext` so the host can reach `transports`, etc.
- **Explicit form** — `{ ctx, authenticate, prefix?, verbose? }`. Caller owns ctx lifecycle; adapter does NOT wire `onClose` and returns `void`.

Engine access for non-HTTP code goes through the context (sugar: returned `OptioContext`; explicit: caller's own):

```typescript
const engine = resolveOptioEngine(ctx, {});
// or for a custom contract:
const my = new MyClient(ctx.transports.get(database, prefix));
```

```typescript
function registerProcessRoutes(app: FastifyInstance, opts: OptioApiOptions): void
```

Registers all REST routes under `/api/processes/:prefix/...` using `@ts-rest/fastify`
against the `processesContract` from `optio-contracts`.

```typescript
function registerProcessStream(app: FastifyInstance, opts: OptioApiOptions): void
```

Registers two SSE routes (raw HTTP, not ts-rest):

- `GET /api/processes/:prefix/stream` — flat list stream (uses `createListPoller`)
- `GET /api/processes/:prefix/:id/tree/stream?maxDepth=N` — tree + log delta stream (uses `createTreePoller`)

Both routes set `Content-Type: text/event-stream`, read MongoDB when a change is relevant to them (see "Stream Poller"), and call `poller.stop()` on request close.

```typescript
function registerWidgetProxy(app: FastifyInstance, opts: OptioWidgetProxyOptions): void

interface OptioWidgetProxyOptions {
  db: Db;
  prefix: string;
  authenticate: AuthCallback<FastifyRequest>;  // viewer role for reads, operator for writes
  ttlMs?: number;  // widgetUpstream TTL cache duration; default 5000 ms
}
```

Imported from `optio-api/fastify`. Wires the widget proxy under `/api/widget/:processId/*`
supporting HTTP, SSE, and WebSocket. Per-request behavior:

1. Extracts `processId` (24-hex ObjectId) from the URL; 404 on malformed URL.
2. Calls `authenticate` — viewer permission for safe HTTP methods, operator for mutating methods.
3. Looks up `widgetUpstream` from MongoDB, TTL-cached for `ttlMs` ms (default 5 s); 404 when missing.
4. Injects inner auth: `BasicAuth`/`HeaderAuth` → request headers; `QueryAuth` → URL query parameter.
5. Strips `/api/widget/:processId` prefix and forwards the sub-path to the upstream URL.
6. Maps upstream connection errors to 502 Bad Gateway; full error detail is logged server-side only.

## Handler Functions

All handlers are exported from `optio-api` (main entry point).

```typescript
// Query handlers
// Every handler takes an optional trailing `access: Access` (default
// UNRESTRICTED); see "Access scope and authorization".
async function listProcesses(
  ctx: OptioContext,
  query: ListProcessesQuery,
  access?: Access,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number }>

async function getProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<object | null>

async function getProcessTree(
  ctx: OptioContext,
  query: { database?: string; prefix?: string; maxDepth?: number },
  id: string,
): Promise<object | null>

async function getProcessLog(
  ctx: OptioContext,
  query: GetProcessLogQuery,
  id: string,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

async function getProcessTreeLog(
  ctx: OptioContext,
  query: GetProcessTreeLogQuery,
  id: string,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

// Command handlers route to the optio-engine clamator contract via
// `resolveOptioEngine(ctx, query).{launch,cancel,dismiss,resurrect,resync}(...)`. Engine
// owns all command-acceptance rules (state allowlists, supportsResume guard,
// persistent launch blocks); API handlers translate the discriminated-union
// result into HTTP status + body.
async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume?: boolean,  // default: false
): Promise<LaunchCommandResult>

async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CancelCommandResult>

async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<DismissCommandResult>

// POST /api/processes/:id/resurrect: saves the work a failed run left on its
// host, then resumes (engine RPC `resurrect`). `sessionId` is the initiating
// session (request body `{ sessionId?: string | null }`).
async function resurrectProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  sessionId?: string | null,  // default: null
  access?: Access,
): Promise<ResurrectCommandResult>

async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean?: boolean,  // default: false
  metadataFilter?: ProcessMetadataFilter,  // omit or pass {} for full sync
  access?: Access,
): Promise<ResyncCommandResult>  // { status: 202; body: { message } } | { status: 400 | 403; body: { message } }
```

Command results also carry `{ status: 403; body: { message: 'Forbidden' } }`
(`ForbiddenResult`) when the host's `authorize` hook refuses.

The 404/409 response body for `launchProcess` / `cancelProcess` / `dismissProcess` /
`resurrectProcess` is `{ reason, message }`, typed via `LaunchErrorBody` / `CancelErrorBody` /
`DismissErrorBody` / `ResurrectErrorBody` in `optio-contracts/src/api-to-frontend.ts`. The
`reason` discriminator is one of the engine failure-reason enums from
`optio-contracts/src/engine-failure-reasons.ts`.

**Adapters** (`fastify`, `express`, `nextjs-app`, `nextjs-pages`): all four extract `body?.resume`
from the request body and forward it to `launchProcess` as the sixth argument. For resurrect they
forward `body?.sessionId ?? null` (the body is optional) to `resurrectProcess` as the fourth argument.

Per the architectural rule above, command handlers do not validate state — they forward the raw
id to the engine via `engine.launch / cancel / dismiss / resurrect` and translate the discriminated-union
result into HTTP status + body. Failure reasons (`not-found`, `not-launchable`, `no-resume-support`,
`launch-blocked`, `not-cancellable`, `not-dismissable`, and for resurrect `not-resurrectable`,
`no-resurrect-support`, `resurrect-in-progress`, `shutting-down`) come from the engine; the API only
maps each to 404 or 409 (`not-found` is 404, every other reason 409).

## Types

```typescript
interface ListQuery {
  cursor?: string;              // ObjectId string; cursor-based pagination
  limit: number;
  rootId?: string;              // filter by root process ObjectId
  state?: string;               // filter by status.state
  metadataFilter?: ProcessMetadataFilter;  // exact-match metadata filter; parsed from URL-encoded JSON
}

interface PaginationQuery {
  cursor?: string;  // numeric string index into log array
  limit: number;
}

interface TreeLogQuery extends PaginationQuery {
  maxDepth?: number;  // relative depth limit from queried process
}

// Per-command result types. The 200 body is a process; the 404/409 body is
// `{ reason, message }` where `reason` is the corresponding engine failure-reason
// enum from `optio-contracts/src/engine-failure-reasons.ts`.
type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: LaunchFailureReasonType; message: string } };

type CancelCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: CancelFailureReasonType; message: string } };

type DismissCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: DismissFailureReasonType; message: string } };

type ResurrectCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: ResurrectFailureReasonType; message: string } };
```

## Stream Poller

```typescript
interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;  // called with JSON-serializable event objects
  onError: () => void;                 // called on a failed read; the stream stops itself first
  metadataFilter?: ProcessMetadataFilter;
  scope?: ScopeFilter | null;          // access scope; $and-ed into the query
  hub?: ProcessChangeHub;              // default: the shared hub of (db, prefix)
}

interface TreePollerOptions extends StreamPollerOptions {
  rootId: string;     // ObjectId string of the tree root
  baseDepth: number;  // absolute depth of the queried process
  maxDepth?: number;  // relative depth limit from baseDepth
}

interface ListPollerHandle {
  start(): void;  // subscribes to the hub, then reads once
  stop(): void;   // unsubscribes; no read after it
}

function createListPoller(opts: StreamPollerOptions): ListPollerHandle
function createTreePoller(opts: TreePollerOptions): ListPollerHandle
```

**How a stream learns about changes** (`process-change-hub.ts`,
`process-change-relevance.ts`; spec `docs/2026-10-09-api-change-streams-design.md`).
Every stream (list, tree, multi-tree, session events) subscribes to the
`ProcessChangeHub` of its `(database, prefix)` (`getProcessChangeHub(db, prefix)`;
shared per MongoClient, database name and prefix). The hub holds one MongoDB
change stream on `{prefix}_processes` while it has subscribers and asks a
stream to re-read only for a change relevant to it, judged from the change
event alone (no `fullDocument` lookup; a `$project` in the change stream's own pipeline cuts each event down to the operation, the id, the changed field names, an insert's `rootId` / `originatingSessionId` and an update's new `originatingSessionId`, so no log lines or documents reach the API): the list for any insert, a shown
process's sent fields, or any `metadata` change; a tree for an insert under its
root or any change of a member; session events for a process joining the
session or a member's `sessionEvents`. While a stream's read is in flight every
change counts as relevant. A stream re-reads at most once a second (the first
change after a quiet second at once). Where change streams are unavailable (a
standalone mongod refuses with error 40573; no permission to watch: 13; a
sharded cluster's mongos watches fine) the hub polls -- every stream re-reads once a second -- and tries
again every 5 minutes; after any other error it polls and reopens with backoff
(1 s doubling to 30 s); every reopen makes every stream re-read once.
`OPTIO_API_CHANGE_STREAMS=off` makes every hub poll. Event shapes are unchanged.

### SSE event shapes emitted by `createListPoller`

```typescript
{ type: 'update'; processes: Array<{ _id, processId, name, status, progress, cancellable, special, warning, metadata, depth }> }
```

### SSE event shapes emitted by `createTreePoller`

```typescript
{ type: 'update'; processes: Array<{ _id, parentId, name, status, progress, cancellable, depth, order, widgetData }> }
{ type: 'log'; entries: Array<{ ...logEntry, processId, processLabel }> }
{ type: 'log-clear' }
```

Every poller's `update` processes also carry `supportsResurrect`, `hasUnsavedWork` and
`resurrecting` (`false` when absent), all part of the snapshot fingerprint.

`createTreePoller` sends all existing log entries on the first poll, then only deltas. It detects log truncation (e.g. after resync) and emits `log-clear` before new entries.

The `widgetData` field is included in tree-stream `update` events and is part of the snapshot fingerprint, so worker-side mutations (via `ctx.set_widget_data`) trigger a new SSE event. The list stream (`createListPoller`) does **not** include `widgetData` — it is omitted from sidebar payloads.

`createListPoller` reads only the fields it sends (a projection: no `log`, `widgetData`, `params` or `sessionEvents`), so a poll costs the summary, not the processes' logs.

`widgetUpstream` is **never** included in any client-facing payload (list stream, tree stream, or REST responses).

## Building Custom Adapters

When writing a custom framework adapter (not Fastify/Express/Next.js), follow these rules:

1. **Accept an `OptioContext`** (or build one yourself from `createOptioContext({ dbOpts, redis })`). Do NOT construct `RedisRpcClient` directly; the ctx's `transports` cache ensures one client per `(database, prefix)` and handles lifecycle.

2. **For per-request engine access, call `resolveOptioEngine(ctx, query)`**. It encapsulates `resolveDb` + `new OptioEngineClient(...)`.

3. **Lifecycle**: when sugar form (you constructed ctx), wire `ctx.closeAll()` into your framework's shutdown hook. When explicit form (caller passed ctx), do NOT call closeAll — caller owns lifecycle.

Example skeleton:

```typescript
import {
  createOptioContext,
  resolveOptioEngine,
  type OptioContext,
  launchProcess, cancelProcess, dismissProcess, /* ... */
} from 'optio-api';

export function registerOptioApiMyFramework(app: MyApp, opts: SugarOpts | { ctx: OptioContext }) {
  const explicit = 'ctx' in opts;
  const ctx = explicit ? opts.ctx : createOptioContext({ dbOpts: { db: opts.db }, redis: opts.redis });

  // ... mount routes; handlers take (ctx, query, id?, ...) ...

  if (!explicit) {
    app.onClose(async () => ctx.closeAll());
    return ctx;
  }
  return;
}
```

## Layer rules (binding)

The `optio-api` package has three internal layers. Code lives in the layer that
matches its responsibility. These rules are binding: PR review will reject
violations.

### 1. Adapter layer — `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`

**Sole purpose:** integrate with the corresponding web framework.

**Allowed:**

- Framework-native request/response wrangling.
- Route registration via the framework's API.
- Framework lifecycle hooks (e.g. fastify `onClose`).
- Framework-specific SSE response writers (`reply.raw.writeHead` / `res.write` /
  Next.js `ReadableStream`).
- Body parser and middleware registration.

**Forbidden:** any code that would be repeated identically across the four
adapters. This explicitly includes:

- `resolveDb(...)` calls — extract to handler via `OptioContext`.
- Default-value fallbacks (`x ?? N`) — defaults belong in the contract Zod
  schemas (e.g. `PaginationQuerySchema.default(20)`).
- `parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth`
  coercion — use `sse-options.ts`.
- Engine cache instantiation — use `createOptioContext`.
- Business logic, RPC mechanics, `ObjectId` coercion.

**Test before adding code to an adapter:** *"Would I write this same code in
the other three adapters?"* If yes, extract.

**Test before adding a default:** check whether the contract layer
(`@optio/contracts`, `processesContract`) can express it via Zod
`.default(...)`. Defaults belong in the contract.

### 2. Handler layer — `packages/optio-api/src/handlers.ts` and collaborators

Framework-agnostic. Receives `OptioContext` + per-request data. Owns:

- Read-path Mongo queries.
- Write-path RPC calls (post-phase-3).
- Request → response shaping.
- Status-code mapping.

Collaborators: `process-id-resolver.ts`, `metadata-filter-query.ts`,
`sse-options.ts`.

### 3. Context layer — `packages/optio-api/src/context.ts`

Owns durable per-app resources: `dbOpts`, `transports` (Layer 1 cache), `redis`, and a `closeAll` teardown function. Constructed once at adapter registration via `createOptioContext({ dbOpts, redis })`. Threaded into every handler call.
