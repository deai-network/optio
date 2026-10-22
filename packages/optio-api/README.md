# optio-api

REST API handlers and SSE streams for Optio process management.
Framework-agnostic handlers with ready-to-use adapters for Fastify, Express, and Next.js.

## Install

```bash
npm install optio-api optio-contracts
```

Then install the adapter dependencies for your framework:

```bash
# Fastify
npm install fastify @ts-rest/fastify

# Express
npm install express @ts-rest/express

# Next.js (Pages Router)
npm install next @ts-rest/next

# Next.js (App Router)
npm install next @ts-rest/serverless
```

## Entry Points

- `optio-api` — framework-agnostic handlers and stream pollers
- `optio-api/fastify` — Fastify adapter
- `optio-api/express` — Express adapter
- `optio-api/nextjs/pages` — Next.js Pages Router adapter
- `optio-api/nextjs/app` — Next.js App Router adapter

## Internal structure

The package has three layers:

1. **Adapter layer** (`src/adapters/`): one file per supported web framework
   (`fastify`, `express`, `nextjs-app`, `nextjs-pages`). Owns only framework
   integration — route registration, request/response wrangling, lifecycle
   hooks. Framework-agnostic code is forbidden here; see `AGENTS.md` for the
   binding rules.
2. **Handler layer** (`src/handlers.ts` and collaborators): framework-agnostic
   functions taking `OptioContext` + per-request data. Owns read-path Mongo
   queries, write-path RPC calls, request → response shaping.
3. **Context layer** (`src/context.ts`): owns durable per-app resources
   (`dbOpts`, `transports` cache, `redis`, `closeAll`). Constructed once at
   adapter registration via `createOptioContext`. The `transports` cache
   (Layer 1, in `src/optio-transports.ts`) is contract-agnostic — wraps
   `RpcClient` per `(database, prefix)` — and is exposed to RPC-only
   consumers via `createOptioTransports`.

When extending the package, the test for placing code in an adapter is:
*"Would I write this same code in the other three adapters?"* If yes, the
code belongs in the handler or context layer, not the adapter.

## Quick Setup

`authenticate` is required by every adapter. It returns the caller's role:
`'operator'` may do everything, `'viewer'` may only read, `null` means 401. The
examples let everyone do everything; plug in your real auth there. Write
`'operator' as const`: in an async arrow a plain `'operator'` is inferred as
`string`, which TypeScript rejects. To build a complete app around optio, see
[How to set up a new optio app](../../docs/how-to-set-up-a-new-optio-app.md).

### Fastify

```typescript
import Fastify from 'fastify';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerOptioApi } from 'optio-api/fastify';

const app = Fastify();
const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

registerOptioApi(app, {
  db, redis,
  authenticate: async () => 'operator' as const,  // everyone may do everything: plug in real auth
});

await app.listen({ port: 3000 });
```

Fastify's `authenticate` runs as an `onRequest` hook on the instance you pass, so on
the root instance it gates every route of the app, your own routes and static files
included, not only optio's. To confine it to optio's routes, register optio in its own
scope: `await app.register(async (scope) => { registerOptioApi(scope, { ... }); });`.

### Express

```typescript
import express from 'express';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerOptioApi } from 'optio-api/express';

const app = express();
app.use(express.json());
const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

registerOptioApi(app, {
  db, redis,
  authenticate: async () => 'operator' as const,  // everyone may do everything: plug in real auth
});

app.listen(3000);
```

### Next.js Pages Router

```typescript
// pages/api/processes/[...optio].ts
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { createOptioHandler } from 'optio-api/nextjs/pages';

const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

const { handler } = createOptioHandler({
  db, redis,
  authenticate: async () => 'operator' as const,  // everyone may do everything: plug in real auth
});
export default handler;
```

### Next.js App Router

```typescript
// app/api/processes/[...optio]/route.ts
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { createOptioRouteHandlers } from 'optio-api/nextjs/app';

const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

export const { GET, POST } = createOptioRouteHandlers({
  db, redis,
  authenticate: async () => 'operator' as const,  // everyone may do everything: plug in real auth
});
```

## REST Endpoints

All adapters mount the same endpoints under `/api/processes/...`; the instance is chosen with the optional `?database=&prefix=` query parameters:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/processes?database=&prefix=` | List processes (cursor pagination, filtering) |
| GET | `/api/processes/:id?database=&prefix=` | Get single process |
| GET | `/api/processes/:id/tree?database=&prefix=` | Get process subtree |
| GET | `/api/processes/:id/log?database=&prefix=` | Get process log |
| GET | `/api/processes/:id/tree/log?database=&prefix=` | Get merged subtree log |
| GET | `/api/processes/:id/conversation?database=&prefix=` | Read a suspended process's saved conversation, proxied from the engine's conversation archive (the engine decrypts). 200 `{ events, capturedAt }`; 403/404/409/500/503 with `{reason, message}` per `ConversationFailureReason` (503 `engine-unavailable`: no engine, unreachable, no answer within 15 s, or a body over 64 MiB). Gated as the `'widget'` action; always `Cache-Control: no-store`. |
| POST | `/api/processes/:id/launch?database=&prefix=` | Forward launch to engine; optional body `{ resume?: boolean, sessionId?: string \| null, params?: Record<string, unknown>, ephemeral?: boolean }` (`params`: a task factory's values; `ephemeral`: overrides the factory's retention of the launch's record). 200 on success (a task factory's launch: the child's record); 400/404/409 with `{reason, message}` per `LaunchFailureReason` (400 `parameters-required` / `invalid-parameters`, 404 `not-found`, 409 the others). |
| POST | `/api/processes/:id/cancel?database=&prefix=` | Forward cancel to engine. 200 on success; 404/409 per `CancelFailureReason`. |
| POST | `/api/processes/:id/dismiss?database=&prefix=` | Forward dismiss to engine. 200 on success (body `null` when dismissing deleted the record, an ended factory launch); 404/409 per `DismissFailureReason`. |
| POST | `/api/processes/:id/resurrect?database=&prefix=` | Forward resurrect (save a failed run's unsaved work, then resume) to engine; optional body `{ sessionId?: string \| null }`. 200 on success; 404/409 per `ResurrectFailureReason`. |
| POST | `/api/processes/resync?database=&prefix=` | 202 Accepted; engine handles resync asynchronously. |

Command endpoints do not validate state in this package. The engine owns all command-acceptance rules; the API translates the engine's discriminated-union result into HTTP status + body. See the architectural rule at the top of `AGENTS.md`.

### List Query Parameters

The list endpoint (`GET /api/processes`) accepts an optional `metadataFilter` query
parameter for metadata-based filtering. Pass it as a URL-encoded JSON string, e.g.
`?metadataFilter=%7B%22project%22%3A%22x%22%7D`.

## Breaking Changes

### Metadata filter query parameter

The legacy `?metadata.<key>=<value>` query param style has been replaced by
`?metadataFilter=<URL-encoded JSON>`. Requests using the legacy form return 400
with an explicit migration message.

## SSE Streams

Like the REST endpoints, every stream takes the optional `?database=&prefix=` query parameters.

- `GET /api/processes/stream` — live flat process list. Accepts the same optional `?metadataFilter=<URL-encoded JSON>` query param as the REST list endpoint; the legacy `?metadata.<key>=<value>` form returns 400.
- `GET /api/processes/:id/tree/stream` — live process tree with log deltas (optional `?maxDepth=N`)
- `GET /api/processes/tree/multi/stream?treeIds=&flatIds=` — several trees and single processes in one stream (Fastify and Next.js Pages Router only)
- `GET /api/session-events/stream?sessionId=` — new `sessionEvents` of the processes whose `originatingSessionId` is that session (Fastify only)

The widget routes (`/api/widget/:database/:prefix/:processId/*`, `POST /api/widget-control/:database/:prefix/:processId`, `POST /api/widget-upload/:database/:prefix/:processId`) are Fastify only.

A stream reads MongoDB once when it opens, then only when the shared change stream of its
`(database, prefix)` reports a change relevant to it, at most once a second. Without change
streams (a standalone MongoDB, or `OPTIO_API_CHANGE_STREAMS=off`) every stream re-reads once a
second.

## Building Custom Adapters

Import handler functions, stream pollers, and the context factory directly
from `optio-api`:

```typescript
import {
  createOptioContext, type OptioContext,
  listProcesses, getProcess, getProcessTree,
  getProcessLog, getProcessTreeLog,
  launchProcess, cancelProcess, dismissProcess, resurrectProcess, resyncProcesses,
  getProcessConversation,
  createListPoller, createTreePoller,
  type ListQuery, type PaginationQuery, type TreeLogQuery,
  type LaunchCommandResult, type CancelCommandResult, type DismissCommandResult,
  type ResurrectCommandResult, type ConversationResult,
  type StreamPollerOptions, type TreePollerOptions, type ListPollerHandle,
} from 'optio-api';
```

Handler functions take an `OptioContext` (constructed once via
`createOptioContext({ dbOpts, redis })`) as the first argument, a per-request
`query` object as the second, and an optional `id` / additional parameters as
later positional args. Command handlers (`launchProcess`, `cancelProcess`,
`dismissProcess`, `resurrectProcess`) return per-command result unions
(`LaunchCommandResult` / `CancelCommandResult` / `DismissCommandResult` /
`ResurrectCommandResult`) whose error bodies (404/409, and 400 for a launch)
are `{ reason, message }` — map these to HTTP responses directly.

`getProcessConversation` returns `{ status, body, headers }`: send `headers`
(`Cache-Control: no-store`) with the response.

Stream pollers expose a `{ start(), stop() }` handle; call `start()` when the
client connects and `stop()` when they disconnect.

## Multi-tenant access

Besides `authenticate` (viewer / operator), every adapter accepts three optional
hooks for hosts that serve several tenants -- or several users of one tenant --
from one optio database:

```typescript
import { registerOptioApi } from 'optio-api/fastify';
import { or, not, eq } from 'optio-api';

registerOptioApi(app, {
  ctx,
  authenticate: (req) => (req.user ? 'operator' : null),
  // Confine the request to its tenant: a flat exact-match metadata map, or
  // null for an unrestricted user. Other tenants' processes behave as missing.
  scope: (req) => (req.user.admin ? null : { customerId: req.user.customerId }),
  // Hide individual processes within the tenant: a metadata predicate built
  // with the re-exported predicate builders, or null for no restriction. A
  // hidden process behaves as missing everywhere -- reads, streams and
  // actions alike. `eq(key, null)` matches a *missing* key too (MongoDB
  // semantics), and an `undefined` value anywhere in the predicate is
  // rejected outright (the request fails) rather than silently admitting
  // everything -- so `req.user.id` must never be undefined here; this hook
  // only runs once `authenticate` has already accepted the request, which
  // guarantees `req.user` is set.
  visibility: (req) => or(not(eq('kind', 'data-chat')), eq('ownerId', req.user.id)),
  // Decide individual actions: launch, cancel, dismiss, resurrect (a
  // single-process action like launch), resync, the widget routes (also
  // gating the conversation read) and instance discovery. false -> 403.
  authorize: (req, { action, process, clean }) =>
    action === 'instances' ? req.user.admin
      : action === 'resync' && clean ? req.user.admin
      : true,
});
```

Without them nothing changes. The exact enforcement per route is in
`AGENTS.md` ("Access scope and authorization").

## Return value

`registerOptioApi`, `createOptioRouteHandlers`, and `createOptioHandler`
accept two forms:

- **Sugar form** (`{ db | mongoClient, redis, authenticate, ... }`): the adapter constructs an `OptioContext` internally. Return shape:
  - fastify/express: returns the constructed `OptioContext`.
  - nextjs-app: returns `{ GET, POST, ctx }`.
  - nextjs-pages: returns `{ handler, ctx }`.

  Fastify additionally wires its `onClose` lifecycle hook to `ctx.closeAll()` automatically.

- **Explicit form** (`{ ctx, authenticate, ... }`): caller supplies a pre-built `OptioContext` and owns its lifecycle. Return shape:
  - fastify/express: returns `void`.
  - nextjs-app: returns `{ GET, POST }` (no `ctx`).
  - nextjs-pages: returns `{ handler }`.

Construct the context explicitly via `createOptioContext({ dbOpts, redis })` and call `ctx.closeAll()` on shutdown:

```typescript
// Express:
import { registerOptioApi } from 'optio-api/express';

const ctx = registerOptioApi(app, { db, redis, authenticate })!;  // sugar form returns ctx
const server = app.listen(3000);
process.on('SIGTERM', async () => {
  server.close();
  await ctx.closeAll();
});
```

Next.js: same pattern — sugar form returns `{ GET, POST, ctx }` (app router) or `{ handler, ctx }` (pages router). Wire `ctx.closeAll()` into whatever shutdown hook your deployment provides (typically not needed for serverless, since process death drops the Redis socket).

Programmatic engine access for the host's own non-HTTP code:

```typescript
import { resolveOptioEngine } from 'optio-api';
const engine = resolveOptioEngine(ctx, {});
await engine.launch({ processId: 'my-task', sessionId: null });  // sessionId is required, nullable
```

RPC-only consumers (no Mongo, no HTTP — e.g., Excavator) use the Layer-1 transport cache directly:

```typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';
const transports = createOptioTransports(redis);
const optioEngine = new OptioEngineClient(transports.get('mydb', 'optio'));
// Plus any other clamator contract:
const myDomain = new MyDomainClient(transports.get('mydb', 'mydomain'));
```

A transport is keyed `<database>/<prefix>` in Redis, the same key prefix optio-core's
RPC server uses. A service the app registers on that server
(`optio_core.rpc_server.register_service(...)`) therefore shares optio's key prefix:
reach it with `transports.get(database, optioPrefix)`, the same transport as
`OptioEngineClient`. `transports.get('mydb', 'mydomain')` only works for a separate RPC
server that uses the key prefix `mydb/mydomain`.

## See Also

- [Optio Overview](../../README.md)
