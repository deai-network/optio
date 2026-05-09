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

- `optio-api` — framework-agnostic handlers, Redis publishers, and stream pollers
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
   (`dbOpts`, `engineCache`, `redis`). Constructed once at adapter
   registration via `createOptioContext`.

When extending the package, the test for placing code in an adapter is:
*"Would I write this same code in the other three adapters?"* If yes, the
code belongs in the handler or context layer, not the adapter.

## Quick Setup

### Fastify

```typescript
import Fastify from 'fastify';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerOptioApi } from 'optio-api/fastify';

const app = Fastify();
const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

registerOptioApi(app, { db, redis });

await app.listen({ port: 3000 });
```

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

registerOptioApi(app, { db, redis });

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

export default createOptioHandler({ db, redis });
```

### Next.js App Router

```typescript
// app/api/processes/[...optio]/route.ts
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { createOptioRouteHandlers } from 'optio-api/nextjs/app';

const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

export const { GET, POST } = createOptioRouteHandlers({ db, redis });
```

## REST Endpoints

All adapters mount the same endpoints under `/api/processes/:prefix/...`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/processes/:prefix` | List processes (cursor pagination, filtering) |
| GET | `/api/processes/:prefix/:id` | Get single process |
| GET | `/api/processes/:prefix/:id/tree` | Get process subtree |
| GET | `/api/processes/:prefix/:id/log` | Get process log |
| GET | `/api/processes/:prefix/:id/tree/log` | Get merged subtree log |
| POST | `/api/processes/:prefix/:id/launch` | Launch a process |
| POST | `/api/processes/:prefix/:id/cancel` | Cancel a process |
| POST | `/api/processes/:prefix/:id/dismiss` | Dismiss a process |
| POST | `/api/processes/:prefix/resync` | Re-sync task definitions |

### List Query Parameters

The list endpoint (`GET /api/processes/:prefix`) accepts an optional `metadataFilter` query
parameter for metadata-based filtering. Pass it as a URL-encoded JSON string, e.g.
`?metadataFilter=%7B%22project%22%3A%22x%22%7D`.

## Breaking Changes

### Metadata filter query parameter

The legacy `?metadata.<key>=<value>` query param style has been replaced by
`?metadataFilter=<URL-encoded JSON>`. Requests using the legacy form return 400
with an explicit migration message.

## SSE Streams

- `GET /api/processes/:prefix/stream` — live flat process list, polls every 1 s. Accepts the same optional `?metadataFilter=<URL-encoded JSON>` query param as the REST list endpoint; the legacy `?metadata.<key>=<value>` form returns 400.
- `GET /api/processes/:prefix/:id/tree/stream` — live process tree with log deltas, polls every 1 s

## Exported Publishers

Use these in domain code to send commands to the Optio worker via Redis streams.

| Function | Signature | Description |
|----------|-----------|-------------|
| `publishLaunch` | `(redis: Redis, prefix: string, processId: string) => Promise<void>` | Request launch of a process |
| `publishResync` | `(redis: Redis, prefix: string, clean?: boolean, metadataFilter?: ProcessMetadataFilter) => Promise<void>` | Request a resync; pass `clean: true` for a nuke-and-resync; pass `metadataFilter` to restrict regeneration to matching tasks only |

`prefix` defaults to `"optio"` when not specified in `OptioApiOptions`.

Commands are written to the `{prefix}:commands` Redis stream.

## Building Custom Adapters

Import handler functions and stream pollers directly from `optio-api`:

```typescript
import {
  listProcesses, getProcess, getProcessTree,
  getProcessLog, getProcessTreeLog,
  launchProcess, cancelProcess, dismissProcess, resyncProcesses,
  createListPoller, createTreePoller,
  type ListQuery, type PaginationQuery, type TreeLogQuery, type CommandResult,
  type StreamPollerOptions, type TreePollerOptions, type ListPollerHandle,
} from 'optio-api';
```

Handler functions take `db: Db` and `prefix: string` as their first two arguments,
followed by any query or command parameters. Command handlers (`launchProcess`,
`cancelProcess`, `dismissProcess`) also require `redis: Redis` and return a
`CommandResult` union (`200 | 404 | 409`) that you can map to HTTP responses.

Stream pollers expose a `{ start(), stop() }` handle; call `start()` when the
client connects and `stop()` when they disconnect.

## Return value

`registerOptioApi`, `createOptioRouteHandlers`, and `createOptioHandler`
return an object that exposes the underlying clamator engine client(s)
plus a teardown function:

- **Single-db mode** (`db` supplied): `{ engine, closeAll }`
  - `engine: EngineClient` — typed client ready to call engine RPC methods.
  - `closeAll(): Promise<void>` — drains every cached client. Idempotent.
- **Multi-db mode** (`mongoClient` supplied): `{ getEngine, closeAll }`
  - `getEngine(database, prefix): EngineClient` — looks up or lazily
    constructs the client for `(database, prefix)`. Repeat lookups
    return the same instance.
  - `closeAll(): Promise<void>` — same as above.

Fastify wires `closeAll` into its `onClose` lifecycle hook
automatically. Express and Next.js have no equivalent; callers wire
`closeAll` into their shutdown handler manually:

```typescript
// Express:
import { registerOptioApi } from 'optio-api/express';

const { engine, closeAll } = registerOptioApi(app, { db, redis });
const server = app.listen(3000);
process.on('SIGTERM', async () => {
  server.close();
  await closeAll();
});
```

Next.js: `closeAll` is exposed on the same handle returned from `createOptioRouteHandlers` / `createOptioHandler`. Wire it into whatever shutdown hook your deployment provides (typically not needed for serverless, since process death drops the Redis socket).

The returned `engine` (or `getEngine(...)`) can be shared with non-HTTP
code paths (custom RPC integrations, server-side scheduled jobs) so
they do not need to construct their own clamator client.

## See Also

- [Optio Overview](../../README.md)
