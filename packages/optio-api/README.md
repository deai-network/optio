# optio-api

REST API handlers and SSE streams for Optio process management.
Framework-agnostic handlers with a ready-to-use Fastify adapter.

## Install

```bash
npm install optio-api optio-contracts
```

`optio-api` has peer dependencies on `fastify` (optional, only needed for the Fastify adapter) and runtime dependencies on `mongodb` and `ioredis`.

## Entry Points

- `optio-api` — framework-agnostic handlers, Redis publishers, and stream pollers
- `optio-api/fastify` — Fastify adapter (registers routes and SSE streams)

## Quick Setup (Fastify)

```typescript
import Fastify from 'fastify';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import {
  registerProcessRoutes,
  registerProcessStream,
  type OptioApiOptions,
} from 'optio-api/fastify';

const app = Fastify();
const db = (await new MongoClient(process.env.MONGO_URL!).connect()).db();
const redis = new Redis(process.env.REDIS_URL!);

const opts: OptioApiOptions = {
  db,
  redis,
};

registerProcessRoutes(app, opts);
registerProcessStream(app, opts);

await app.listen({ port: 3000 });
```

`registerProcessRoutes` mounts all REST endpoints under `/api/processes/:prefix/...`.
`registerProcessStream` mounts two SSE endpoints:

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

Handler functions take `db: Db` and `prefix: string` as their first two arguments
(the Fastify adapter defaults `prefix` to `"optio"` when not specified in `OptioApiOptions`),
followed by any query or command parameters. Command handlers (`launchProcess`,
`cancelProcess`, `dismissProcess`) also require `redis: Redis` and return a
`CommandResult` union (`200 | 404 | 409`) that you can map to HTTP responses.

Stream pollers expose a `{ start(), stop() }` handle; call `start()` when the
client connects and `stop()` when they disconnect.

## See Also

- [Optio Overview](../../README.md)
