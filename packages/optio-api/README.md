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

## SSE Streams

- `GET /api/processes/:prefix/stream` — live flat process list, polls every 1 s
- `GET /api/processes/:prefix/:id/tree/stream` — live process tree with log deltas, polls every 1 s

## Exported Publishers

Use these in domain code to send commands to the Optio worker via Redis streams.

| Function | Signature | Description |
|----------|-----------|-------------|
| `publishLaunch` | `(redis: Redis, prefix: string, processId: string) => Promise<void>` | Request launch of a process |
| `publishResync` | `(redis: Redis, prefix: string, clean?: boolean) => Promise<void>` | Request a resync; pass `clean: true` for a nuke-and-resync |

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

## See Also

- [Optio Overview](../../README.md)
