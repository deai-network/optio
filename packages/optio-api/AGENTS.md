# optio-api — LLM Reference

## Package

- **name**: `optio-api`
- **entry points**:
  - `optio-api` → `dist/index.js` / `dist/index.d.ts`
  - `optio-api/fastify` → `dist/adapters/fastify.js` / `dist/adapters/fastify.d.ts`
- **dependencies**: `optio-contracts`, `@ts-rest/core`, `mongodb`, `ioredis`, `@clamator/protocol`, `@clamator/over-redis`, `zod`
- **note**: `zod` is declared explicitly here even though `optio-api` does not import it directly. `@clamator/protocol` declares `zod` as a peerDependency; without an explicit declaration in this package, pnpm can resolve `@clamator/protocol`'s peer requirement against a different physical `zod` copy than the one used by `optio-contracts`, which causes TypeScript to reject the codegenned `_generated/engine.ts`. See `@clamator/protocol`'s README for the canonical statement of this consumer requirement.
- **optionalDependencies**: `@ts-rest/fastify`
- **peerDependencies**: `fastify ^5.2.0` (optional)

## Exports (phase 2+)

In addition to the handler functions and stream pollers listed below, the following
symbols are re-exported from the main `optio-api` entry point:

- `EngineClient` (re-exported from `_generated/engine.ts`) — typed clamator client
  for the engine RPC contract. Use to call engine methods from non-HTTP code paths.
- `createEngineCache(redis: Redis): EngineCache` — framework-agnostic factory that
  lazily constructs and caches `EngineClient` instances per `(database, prefix)`.
  Custom adapters consume this rather than rolling their own cache.
- `EngineCache` — the type returned by `createEngineCache`. Interface:
  `get(database: string, prefix: string): EngineClient` and
  `closeAll(): Promise<void>`.

## OptioApiOptions

```typescript
interface OptioApiOptions {
  db: Db;                                  // MongoDB Db instance
  redis: Redis;                            // ioredis Redis instance
  prefix?: string;                         // Collection prefix; default 'optio'.
                                           // Reads/writes `{prefix}_processes`.
  authenticate: AuthCallback<TRequest>;    // TRequest depends on adapter (FastifyRequest,
                                           // express Request, web Request, NextApiRequest).
                                           // Returns 'viewer' | 'operator' | null.
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

## Fastify Adapter

Imported from `optio-api/fastify`.

**Return shape (phase 2+):**

`registerOptioApi(app, opts)` returns:

- `{ engine: EngineClient, closeAll: () => Promise<void> }` in single-db mode (`db` supplied).
- `{ getEngine: (database: string, prefix: string) => EngineClient, closeAll: () => Promise<void> }` in multi-db mode (`mongoClient` supplied).

Fastify wires `closeAll` into its `onClose` hook automatically. The returned `engine`
(or result of `getEngine(...)`) can be shared with non-HTTP code paths so they do not
need to construct their own clamator client.

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

Both routes set `Content-Type: text/event-stream`, poll every 1 s, and call `poller.stop()` on request close.

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
async function listProcesses(
  db: Db,
  prefix: string,
  query: ListQuery,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number }>

async function getProcess(
  db: Db,
  prefix: string,
  id: string,
): Promise<object | null>

async function getProcessTree(
  db: Db,
  prefix: string,
  id: string,
  maxDepth?: number,
): Promise<object | null>

async function getProcessLog(
  db: Db,
  prefix: string,
  id: string,
  query: PaginationQuery,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

async function getProcessTreeLog(
  db: Db,
  prefix: string,
  id: string,
  query: TreeLogQuery,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

// Command handlers
async function launchProcess(
  db: Db,
  redis: Redis,
  database: string,
  prefix: string,
  id: string,
  resume?: boolean,  // default: false
): Promise<CommandResult>
// Returns 409 with message "This task does not support resume" when resume=true is
// requested against a process whose supportsResume field is false.
// (409 is used instead of 400 because CommandResult has no 400 variant.)

async function cancelProcess(
  db: Db,
  redis: Redis,
  prefix: string,
  id: string,
): Promise<CommandResult>

async function dismissProcess(
  db: Db,
  redis: Redis,
  prefix: string,
  id: string,
): Promise<CommandResult>

async function resyncProcesses(
  redis: Redis,
  prefix: string,
  clean?: boolean,  // default: false
  metadataFilter?: ProcessMetadataFilter,  // omit or pass {} for full sync
): Promise<{ message: string }>
```

**Adapters** (`fastify`, `express`, `nextjs-app`, `nextjs-pages`): all four extract `body?.resume`
from the request body and forward it to `launchProcess` as the sixth argument.

State guards enforced by command handlers:

- `launchProcess`: allowed states `idle | done | failed | cancelled`; 409 when `resume=true` and `supportsResume=false`
- `cancelProcess`: requires `proc.cancellable === true`; allowed states `running | scheduled`
- `dismissProcess`: allowed states `done | failed | cancelled`

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

type CommandResult =
  | { status: 200; body: any }
  | { status: 404; body: { message: string } }
  | { status: 409; body: { message: string } };
```

## Publishers

Imported from `optio-api` (main entry point). Write to the `{prefix}:commands` Redis stream.

```typescript
async function publishLaunch(redis: Redis, database: string, prefix: string, processId: string, resume?: boolean): Promise<void>
// resume=true is included in the Redis launch payload; the consumer forwards it to the executor.
async function publishResync(redis: Redis, prefix: string, clean?: boolean, metadataFilter?: ProcessMetadataFilter): Promise<void>
```

Internal-only (not exported from index, only used by handlers):

```typescript
async function publishCancel(redis: Redis, prefix: string, processId: string): Promise<void>
async function publishDismiss(redis: Redis, prefix: string, processId: string): Promise<void>
```

## Stream Poller

```typescript
interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;  // called with JSON-serializable event objects
  onError: () => void;                 // called on poll failure; poller stops itself first
}

interface TreePollerOptions extends StreamPollerOptions {
  rootId: string;     // ObjectId string of the tree root
  baseDepth: number;  // absolute depth of the queried process
  maxDepth?: number;  // relative depth limit from baseDepth
}

interface ListPollerHandle {
  start(): void;  // begins setInterval at 1 s
  stop(): void;   // clears interval
}

function createListPoller(opts: StreamPollerOptions): ListPollerHandle
function createTreePoller(opts: TreePollerOptions): ListPollerHandle
```

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

`createTreePoller` sends all existing log entries on the first poll, then only deltas. It detects log truncation (e.g. after resync) and emits `log-clear` before new entries.

The `widgetData` field is included in tree-stream `update` events and is part of the snapshot fingerprint, so worker-side mutations (via `ctx.set_widget_data`) trigger a new SSE event. The list stream (`createListPoller`) does **not** include `widgetData` — it is omitted from sidebar payloads.

`widgetUpstream` is **never** included in any client-facing payload (list stream, tree stream, or REST responses).

## Building Custom Adapters

When writing a custom framework adapter (not Fastify/Express/Next.js), follow these rules:

1. **Use `createEngineCache(opts.redis)`** to obtain an `EngineCache`. Do NOT construct
   `RedisRpcClient` or `EngineClient` directly — the cache ensures one client instance
   per `(database, prefix)` pair and handles connection lifecycle.

2. **Wire `cache.closeAll()` into your framework's shutdown hook** (where the framework
   provides one). If it does not, expose `closeAll` on the adapter's return value so
   callers can wire it into their own `SIGTERM` / `onClose` handler.

3. **Return the cache (or a `getEngine` wrapper)** from your adapter function so callers
   can share the `EngineClient` with non-HTTP code paths (scheduled jobs, custom RPC
   integrations, etc.) without constructing a second client.

Example skeleton:

```typescript
import { createEngineCache, type EngineClient } from 'optio-api';

export function registerOptioApiMyFramework(app: MyApp, opts: OptioApiOptions) {
  const cache = createEngineCache(opts.redis);
  const engine: EngineClient = cache.get(opts.db.databaseName!, opts.prefix ?? 'optio');

  // ... mount routes using handler functions from optio-api ...

  app.onClose(async () => cache.closeAll());

  return { engine, closeAll: () => cache.closeAll() };
}
```
