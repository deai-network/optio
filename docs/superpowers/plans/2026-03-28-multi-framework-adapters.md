# Multi-Framework Adapter Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Express and Next.js (Pages + App Router) adapters to optio-api, refactor the Fastify adapter to a single registration function, and update all downstream consumers and docs.

**Architecture:** Each adapter is a self-contained file under `src/adapters/` that imports framework-agnostic handlers and stream pollers, wiring them to the target framework's HTTP conventions via the appropriate ts-rest binding. No shared adapter middleware layer.

**Tech Stack:** TypeScript, ts-rest (`@ts-rest/express`, `@ts-rest/next`, `@ts-rest/serverless`), Express, Next.js, Vitest, Fastify, MongoDB, ioredis

---

### Task 1: Refactor Fastify adapter to single registration function

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-dashboard/src/server.ts`

- [ ] **Step 1: Update Fastify adapter — merge into `registerOptioApi`**

Replace the two exported functions with a single one in `packages/optio-api/src/adapters/fastify.ts`:

```typescript
// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { FastifyInstance } from 'fastify';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { db, redis } = opts;
  const s = initServer();

  // --- REST routes via ts-rest ---
  const routes = s.router(apiContract.processes, {
    list: async ({ params, query }) => {
      const result = await handlers.listProcesses(db, params.prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params }) => {
      const result = await handlers.getProcess(db, params.prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params }) => {
      const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    cancel: async ({ params }) => {
      const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params }) => {
      const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
      const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  app.register(s.plugin(routes));

  // --- SSE stream endpoints ---
  app.get('/api/processes/:prefix/:id/tree/stream', async (request: any, reply: any) => {
    const { prefix: urlPrefix, id } = request.params as { prefix: string; id: string };
    const { maxDepth } = request.query as { maxDepth?: string };
    const maxDepthNum = maxDepth !== undefined ? parseInt(maxDepth, 10) : undefined;

    const col = db.collection(`${urlPrefix}_processes`);
    const proc = await col.findOne({ _id: new ObjectId(id) });
    if (!proc) {
      reply.code(404).send({ message: 'Process not found' });
      return;
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createTreePoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => reply.raw.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: maxDepthNum,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/:prefix/stream', async (request: any, reply: any) => {
    const { prefix: urlPrefix } = request.params as { prefix: string };

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createListPoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });
}
```

- [ ] **Step 2: Update optio-dashboard to use new API**

In `packages/optio-dashboard/src/server.ts`, replace:

```typescript
import { registerProcessRoutes, registerProcessStream } from 'optio-api/fastify';
```

with:

```typescript
import { registerOptioApi } from 'optio-api/fastify';
```

And replace:

```typescript
  await registerProcessRoutes(app, { db, redis, prefix: config.prefix });
  await registerProcessStream(app, { db, redis, prefix: config.prefix });
```

with:

```typescript
  await registerOptioApi(app, { db, redis, prefix: config.prefix });
```

- [ ] **Step 3: Build and verify**

Run: `cd packages/optio-api && npx tsc --noEmit`
Expected: No errors

Run: `cd packages/optio-dashboard && npx tsc --noEmit`
Expected: No errors

---

### Task 2: Add Express adapter

**Files:**
- Create: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/package.json` (add export + dependencies)

- [ ] **Step 1: Add Express dependencies to package.json**

Add to `packages/optio-api/package.json`:

In `optionalDependencies`, add:
```json
"@ts-rest/express": "^3.51.0"
```

In `peerDependencies`, add:
```json
"express": "^4.21.0 || ^5.0.0"
```

In `peerDependenciesMeta`, add:
```json
"express": { "optional": true }
```

In `exports`, add:
```json
"./express": {
  "import": "./dist/adapters/express.js",
  "types": "./dist/adapters/express.d.ts"
}
```

- [ ] **Step 2: Install Express and ts-rest/express as dev dependencies for development**

Run: `cd /home/csillag/deai/optio && pnpm add -D express @types/express @ts-rest/express --filter optio-api`

- [ ] **Step 3: Create Express adapter**

Create `packages/optio-api/src/adapters/express.ts`:

```typescript
// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initContract } from '@ts-rest/core';
import { createExpressEndpoints } from '@ts-rest/express';
import { processesContract } from 'optio-contracts';
import type { Express, Request, Response } from 'express';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: Express, opts: OptioApiOptions) {
  const { db, redis } = opts;

  // --- REST routes via ts-rest ---
  createExpressEndpoints(apiContract.processes, {
    list: async ({ params, query }) => {
      const result = await handlers.listProcesses(db, params.prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params }) => {
      const result = await handlers.getProcess(db, params.prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params }) => {
      const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    cancel: async ({ params }) => {
      const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params }) => {
      const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
      const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  }, app);

  // --- SSE stream endpoints ---
  app.get('/api/processes/:prefix/:id/tree/stream', async (req: Request, res: Response) => {
    const { prefix: urlPrefix, id } = req.params;
    const maxDepth = req.query.maxDepth as string | undefined;
    const maxDepthNum = maxDepth !== undefined ? parseInt(maxDepth, 10) : undefined;

    const col = db.collection(`${urlPrefix}_processes`);
    const proc = await col.findOne({ _id: new ObjectId(id) });
    if (!proc) {
      res.status(404).json({ message: 'Process not found' });
      return;
    }

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createTreePoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => res.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: maxDepthNum,
    });

    poller.start();
    req.on('close', () => poller.stop());
  });

  app.get('/api/processes/:prefix/stream', async (req: Request, res: Response) => {
    const { prefix: urlPrefix } = req.params;

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createListPoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => res.end(),
    });

    poller.start();
    req.on('close', () => poller.stop());
  });
}
```

- [ ] **Step 4: Build and verify**

Run: `cd packages/optio-api && npx tsc --noEmit`
Expected: No errors

---

### Task 3: Add Next.js Pages Router adapter

**Files:**
- Create: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/package.json` (add export + dependencies)

- [ ] **Step 1: Add Next.js Pages dependencies to package.json**

Add to `packages/optio-api/package.json`:

In `optionalDependencies`, add:
```json
"@ts-rest/next": "^3.51.0"
```

In `peerDependencies`, add:
```json
"next": "^13.0.0 || ^14.0.0 || ^15.0.0"
```

In `peerDependenciesMeta`, add:
```json
"next": { "optional": true }
```

In `exports`, add:
```json
"./nextjs/pages": {
  "import": "./dist/adapters/nextjs-pages.js",
  "types": "./dist/adapters/nextjs-pages.d.ts"
}
```

- [ ] **Step 2: Install Next.js and ts-rest/next as dev dependencies**

Run: `cd /home/csillag/deai/optio && pnpm add -D next @ts-rest/next --filter optio-api`

- [ ] **Step 3: Create Next.js Pages Router adapter**

Create `packages/optio-api/src/adapters/nextjs-pages.ts`:

```typescript
// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initContract } from '@ts-rest/core';
import { createNextHandler } from '@ts-rest/next';
import { processesContract } from 'optio-contracts';
import type { NextApiRequest, NextApiResponse } from 'next';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioHandler(opts: OptioApiOptions) {
  const { db, redis } = opts;

  const tsRestHandler = createNextHandler(apiContract.processes, {
    list: async ({ params, query }) => {
      const result = await handlers.listProcesses(db, params.prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params }) => {
      const result = await handlers.getProcess(db, params.prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params }) => {
      const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    cancel: async ({ params }) => {
      const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params }) => {
      const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
      const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  return async function optioHandler(req: NextApiRequest, res: NextApiResponse) {
    const url = req.url ?? '';

    // SSE: tree stream
    const treeStreamMatch = url.match(/\/api\/processes\/([^/]+)\/([^/]+)\/tree\/stream/);
    if (treeStreamMatch && req.method === 'GET') {
      const [, urlPrefix, id] = treeStreamMatch;
      const maxDepth = req.query.maxDepth as string | undefined;
      const maxDepthNum = maxDepth !== undefined ? parseInt(maxDepth, 10) : undefined;

      const col = db.collection(`${urlPrefix}_processes`);
      const proc = await col.findOne({ _id: new ObjectId(id) });
      if (!proc) {
        res.status(404).json({ message: 'Process not found' });
        return;
      }

      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      });

      const sendEvent = (data: unknown) => {
        res.write(`data: ${JSON.stringify(data)}\n\n`);
      };

      const poller = createTreePoller({
        db,
        prefix: urlPrefix,
        sendEvent,
        onError: () => res.end(),
        rootId: proc.rootId.toString(),
        baseDepth: proc.depth,
        maxDepth: maxDepthNum,
      });

      poller.start();
      req.on('close', () => poller.stop());
      return;
    }

    // SSE: list stream
    const listStreamMatch = url.match(/\/api\/processes\/([^/]+)\/stream/);
    if (listStreamMatch && req.method === 'GET') {
      const [, urlPrefix] = listStreamMatch;

      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      });

      const sendEvent = (data: unknown) => {
        res.write(`data: ${JSON.stringify(data)}\n\n`);
      };

      const poller = createListPoller({
        db,
        prefix: urlPrefix,
        sendEvent,
        onError: () => res.end(),
      });

      poller.start();
      req.on('close', () => poller.stop());
      return;
    }

    // Delegate to ts-rest for REST routes
    return tsRestHandler(req, res);
  };
}
```

- [ ] **Step 4: Build and verify**

Run: `cd packages/optio-api && npx tsc --noEmit`
Expected: No errors

---

### Task 4: Add Next.js App Router adapter

**Files:**
- Create: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/package.json` (add export + dependencies)

- [ ] **Step 1: Add App Router dependencies to package.json**

Add to `packages/optio-api/package.json`:

In `optionalDependencies`, add:
```json
"@ts-rest/serverless": "^3.51.0"
```

In `exports`, add:
```json
"./nextjs/app": {
  "import": "./dist/adapters/nextjs-app.js",
  "types": "./dist/adapters/nextjs-app.d.ts"
}
```

Note: `next` is already in peerDependencies from Task 3.

- [ ] **Step 2: Install ts-rest/serverless as dev dependency**

Run: `cd /home/csillag/deai/optio && pnpm add -D @ts-rest/serverless --filter optio-api`

- [ ] **Step 3: Create Next.js App Router adapter**

Create `packages/optio-api/src/adapters/nextjs-app.ts`:

```typescript
// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initContract } from '@ts-rest/core';
import { tsr } from '@ts-rest/serverless/next';
import { processesContract } from 'optio-contracts';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioRouteHandlers(opts: OptioApiOptions) {
  const { db, redis } = opts;

  const tsRestHandlers = tsr.routeHandler(apiContract.processes, {
    list: async ({ params, query }) => {
      const result = await handlers.listProcesses(db, params.prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params }) => {
      const result = await handlers.getProcess(db, params.prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params }) => {
      const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    cancel: async ({ params }) => {
      const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params }) => {
      const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
      return result as any;
    },
    resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
      const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  async function handleSSE(request: Request, db: Db): Promise<Response | null> {
    const url = new URL(request.url);
    const path = url.pathname;

    // SSE: tree stream
    const treeStreamMatch = path.match(/\/api\/processes\/([^/]+)\/([^/]+)\/tree\/stream/);
    if (treeStreamMatch && request.method === 'GET') {
      const [, urlPrefix, id] = treeStreamMatch;
      const maxDepth = url.searchParams.get('maxDepth') ?? undefined;
      const maxDepthNum = maxDepth !== undefined ? parseInt(maxDepth, 10) : undefined;

      const col = db.collection(`${urlPrefix}_processes`);
      const proc = await col.findOne({ _id: new ObjectId(id) });
      if (!proc) {
        return new Response(JSON.stringify({ message: 'Process not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      const stream = new ReadableStream({
        start(controller) {
          const sendEvent = (data: unknown) => {
            controller.enqueue(`data: ${JSON.stringify(data)}\n\n`);
          };

          const poller = createTreePoller({
            db,
            prefix: urlPrefix,
            sendEvent,
            onError: () => controller.close(),
            rootId: proc.rootId.toString(),
            baseDepth: proc.depth,
            maxDepth: maxDepthNum,
          });

          poller.start();

          // Clean up when the client disconnects
          request.signal.addEventListener('abort', () => {
            poller.stop();
            controller.close();
          });
        },
      });

      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    }

    // SSE: list stream
    const listStreamMatch = path.match(/\/api\/processes\/([^/]+)\/stream/);
    if (listStreamMatch && request.method === 'GET') {
      const [, urlPrefix] = listStreamMatch;

      const stream = new ReadableStream({
        start(controller) {
          const sendEvent = (data: unknown) => {
            controller.enqueue(`data: ${JSON.stringify(data)}\n\n`);
          };

          const poller = createListPoller({
            db,
            prefix: urlPrefix,
            sendEvent,
            onError: () => controller.close(),
          });

          poller.start();

          request.signal.addEventListener('abort', () => {
            poller.stop();
            controller.close();
          });
        },
      });

      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    }

    return null;
  }

  return {
    GET: async (request: Request) => {
      const sseResponse = await handleSSE(request, db);
      if (sseResponse) return sseResponse;
      return tsRestHandlers(request);
    },
    POST: async (request: Request) => {
      return tsRestHandlers(request);
    },
  };
}
```

- [ ] **Step 4: Build and verify**

Run: `cd packages/optio-api && npx tsc --noEmit`
Expected: No errors

---

### Task 5: Integration tests for Fastify adapter

**Files:**
- Create: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Install test utilities**

Run: `cd /home/csillag/deai/optio && pnpm add -D mongodb-memory-server ioredis-mock --filter optio-api`

- [ ] **Step 2: Write Fastify integration tests**

Create `packages/optio-api/src/adapters/__tests__/fastify.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import Fastify from 'fastify';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import Redis from 'ioredis-mock';
import { registerOptioApi } from '../fastify.js';

let mongod: MongoMemoryServer;
let mongoClient: MongoClient;
let db: any;
let redis: any;

beforeAll(async () => {
  mongod = await MongoMemoryServer.create();
  mongoClient = new MongoClient(mongod.getUri());
  await mongoClient.connect();
  db = mongoClient.db('test');
  redis = new Redis();
});

afterAll(async () => {
  await mongoClient.close();
  await mongod.stop();
});

beforeEach(async () => {
  await db.collection('optio_processes').deleteMany({});
});

function createApp() {
  const app = Fastify();
  registerOptioApi(app, { db, redis });
  return app;
}

async function seedProcess(overrides: Record<string, unknown> = {}) {
  const id = new ObjectId();
  const doc = {
    _id: id,
    processId: 'test-task',
    name: 'Test Task',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0,
    order: 0,
    rootId: id,
    cancellable: true,
    metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc);
  return doc;
}

describe('Fastify adapter', () => {
  it('GET /api/processes/:prefix — lists processes', async () => {
    await seedProcess();
    const app = createApp();
    const res = await app.inject({
      method: 'GET',
      url: '/api/processes/optio?limit=10',
    });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns single process', async () => {
    const proc = await seedProcess();
    const app = createApp();
    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/optio/${proc._id}`,
    });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns 404 for missing process', async () => {
    const app = createApp();
    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/optio/${new ObjectId()}`,
    });
    expect(res.statusCode).toBe(404);
  });

  it('POST /api/processes/:prefix/:id/launch — launches idle process', async () => {
    const proc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${proc._id}/launch`,
    });
    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:prefix/:id/launch — returns 409 for running process', async () => {
    const proc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${proc._id}/launch`,
    });
    expect(res.statusCode).toBe(409);
  });

  it('POST /api/processes/:prefix/:id/cancel — cancels running process', async () => {
    const proc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${proc._id}/cancel`,
    });
    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:prefix/:id/dismiss — dismisses done process', async () => {
    const proc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${proc._id}/dismiss`,
    });
    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:prefix/resync — triggers resync', async () => {
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: '/api/processes/optio/resync',
      payload: {},
    });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.message).toBe('Resync requested');
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd /home/csillag/deai/optio/packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests pass

---

### Task 6: Integration tests for Express adapter

**Files:**
- Create: `packages/optio-api/src/adapters/__tests__/express.test.ts`

- [ ] **Step 1: Install supertest for HTTP testing**

Run: `cd /home/csillag/deai/optio && pnpm add -D supertest @types/supertest --filter optio-api`

- [ ] **Step 2: Write Express integration tests**

Create `packages/optio-api/src/adapters/__tests__/express.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import Redis from 'ioredis-mock';
import { registerOptioApi } from '../express.js';

let mongod: MongoMemoryServer;
let mongoClient: MongoClient;
let db: any;
let redis: any;

beforeAll(async () => {
  mongod = await MongoMemoryServer.create();
  mongoClient = new MongoClient(mongod.getUri());
  await mongoClient.connect();
  db = mongoClient.db('test');
  redis = new Redis();
});

afterAll(async () => {
  await mongoClient.close();
  await mongod.stop();
});

beforeEach(async () => {
  await db.collection('optio_processes').deleteMany({});
});

function createApp() {
  const app = express();
  app.use(express.json());
  registerOptioApi(app, { db, redis });
  return app;
}

async function seedProcess(overrides: Record<string, unknown> = {}) {
  const id = new ObjectId();
  const doc = {
    _id: id,
    processId: 'test-task',
    name: 'Test Task',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0,
    order: 0,
    rootId: id,
    cancellable: true,
    metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc);
  return doc;
}

describe('Express adapter', () => {
  it('GET /api/processes/:prefix — lists processes', async () => {
    await seedProcess();
    const app = createApp();
    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
    expect(res.body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns single process', async () => {
    const proc = await seedProcess();
    const app = createApp();
    const res = await request(app).get(`/api/processes/optio/${proc._id}`);
    expect(res.status).toBe(200);
    expect(res.body.name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns 404 for missing process', async () => {
    const app = createApp();
    const res = await request(app).get(`/api/processes/optio/${new ObjectId()}`);
    expect(res.status).toBe(404);
  });

  it('POST /api/processes/:prefix/:id/launch — launches idle process', async () => {
    const proc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/optio/${proc._id}/launch`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:prefix/:id/launch — returns 409 for running process', async () => {
    const proc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/optio/${proc._id}/launch`);
    expect(res.status).toBe(409);
  });

  it('POST /api/processes/:prefix/:id/cancel — cancels running process', async () => {
    const proc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();
    const res = await request(app).post(`/api/processes/optio/${proc._id}/cancel`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:prefix/:id/dismiss — dismisses done process', async () => {
    const proc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/optio/${proc._id}/dismiss`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:prefix/resync — triggers resync', async () => {
    const app = createApp();
    const res = await request(app).post('/api/processes/optio/resync').send({});
    expect(res.status).toBe(200);
    expect(res.body.message).toBe('Resync requested');
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd /home/csillag/deai/optio/packages/optio-api && npx vitest run src/adapters/__tests__/express.test.ts`
Expected: All tests pass

---

### Task 7: Integration tests for Next.js Pages Router adapter

**Files:**
- Create: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

- [ ] **Step 1: Install node-mocks-http for Next.js API route testing**

Run: `cd /home/csillag/deai/optio && pnpm add -D node-mocks-http @types/node-mocks-http --filter optio-api`

- [ ] **Step 2: Write Next.js Pages Router integration tests**

Create `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { createMocks } from 'node-mocks-http';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioHandler } from '../nextjs-pages.js';

let mongod: MongoMemoryServer;
let mongoClient: MongoClient;
let db: any;
let redis: any;

beforeAll(async () => {
  mongod = await MongoMemoryServer.create();
  mongoClient = new MongoClient(mongod.getUri());
  await mongoClient.connect();
  db = mongoClient.db('test');
  redis = new Redis();
});

afterAll(async () => {
  await mongoClient.close();
  await mongod.stop();
});

beforeEach(async () => {
  await db.collection('optio_processes').deleteMany({});
});

async function seedProcess(overrides: Record<string, unknown> = {}) {
  const id = new ObjectId();
  const doc = {
    _id: id,
    processId: 'test-task',
    name: 'Test Task',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0,
    order: 0,
    rootId: id,
    cancellable: true,
    metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc);
  return doc;
}

describe('Next.js Pages Router adapter', () => {
  it('GET /api/processes/:prefix — lists processes', async () => {
    await seedProcess();
    const handler = createOptioHandler({ db, redis });
    const { req, res } = createMocks({
      method: 'GET',
      url: '/api/processes/optio?limit=10',
      query: { limit: '10' },
    });
    await handler(req as any, res as any);
    expect(res._getStatusCode()).toBe(200);
    const body = JSON.parse(res._getData());
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns single process', async () => {
    const proc = await seedProcess();
    const handler = createOptioHandler({ db, redis });
    const { req, res } = createMocks({
      method: 'GET',
      url: `/api/processes/optio/${proc._id}`,
    });
    await handler(req as any, res as any);
    expect(res._getStatusCode()).toBe(200);
    const body = JSON.parse(res._getData());
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns 404 for missing process', async () => {
    const handler = createOptioHandler({ db, redis });
    const { req, res } = createMocks({
      method: 'GET',
      url: `/api/processes/optio/${new ObjectId()}`,
    });
    await handler(req as any, res as any);
    expect(res._getStatusCode()).toBe(404);
  });

  it('POST /api/processes/:prefix/:id/launch — launches idle process', async () => {
    const proc = await seedProcess({ status: { state: 'idle' } });
    const handler = createOptioHandler({ db, redis });
    const { req, res } = createMocks({
      method: 'POST',
      url: `/api/processes/optio/${proc._id}/launch`,
    });
    await handler(req as any, res as any);
    expect(res._getStatusCode()).toBe(200);
  });

  it('POST /api/processes/:prefix/resync — triggers resync', async () => {
    const handler = createOptioHandler({ db, redis });
    const { req, res } = createMocks({
      method: 'POST',
      url: '/api/processes/optio/resync',
      body: {},
    });
    await handler(req as any, res as any);
    expect(res._getStatusCode()).toBe(200);
    const body = JSON.parse(res._getData());
    expect(body.message).toBe('Resync requested');
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd /home/csillag/deai/optio/packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-pages.test.ts`
Expected: All tests pass

---

### Task 8: Integration tests for Next.js App Router adapter

**Files:**
- Create: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`

- [ ] **Step 1: Write Next.js App Router integration tests**

Create `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { MongoClient, ObjectId } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioRouteHandlers } from '../nextjs-app.js';

let mongod: MongoMemoryServer;
let mongoClient: MongoClient;
let db: any;
let redis: any;

beforeAll(async () => {
  mongod = await MongoMemoryServer.create();
  mongoClient = new MongoClient(mongod.getUri());
  await mongoClient.connect();
  db = mongoClient.db('test');
  redis = new Redis();
});

afterAll(async () => {
  await mongoClient.close();
  await mongod.stop();
});

beforeEach(async () => {
  await db.collection('optio_processes').deleteMany({});
});

async function seedProcess(overrides: Record<string, unknown> = {}) {
  const id = new ObjectId();
  const doc = {
    _id: id,
    processId: 'test-task',
    name: 'Test Task',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0,
    order: 0,
    rootId: id,
    cancellable: true,
    metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc);
  return doc;
}

describe('Next.js App Router adapter', () => {
  it('GET /api/processes/:prefix — lists processes', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });
    const request = new Request('http://localhost/api/processes/optio?limit=10');
    const res = await GET(request);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns single process', async () => {
    const proc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });
    const request = new Request(`http://localhost/api/processes/optio/${proc._id}`);
    const res = await GET(request);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:prefix/:id — returns 404 for missing process', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis });
    const request = new Request(`http://localhost/api/processes/optio/${new ObjectId()}`);
    const res = await GET(request);
    expect(res.status).toBe(404);
  });

  it('POST /api/processes/:prefix/:id/launch — launches idle process', async () => {
    const proc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis });
    const request = new Request(`http://localhost/api/processes/optio/${proc._id}/launch`, {
      method: 'POST',
    });
    const res = await POST(request);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:prefix/resync — triggers resync', async () => {
    const { POST } = createOptioRouteHandlers({ db, redis });
    const request = new Request('http://localhost/api/processes/optio/resync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const res = await POST(request);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.message).toBe('Resync requested');
  });

  it('GET SSE tree stream — returns event stream for existing process', async () => {
    const proc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });
    const controller = new AbortController();
    const request = new Request(
      `http://localhost/api/processes/optio/${proc._id}/tree/stream`,
      { signal: controller.signal },
    );
    const res = await GET(request);
    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Type')).toBe('text/event-stream');
    controller.abort();
  });

  it('GET SSE tree stream — returns 404 for missing process', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis });
    const request = new Request(
      `http://localhost/api/processes/optio/${new ObjectId()}/tree/stream`,
    );
    const res = await GET(request);
    expect(res.status).toBe(404);
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd /home/csillag/deai/optio/packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-app.test.ts`
Expected: All tests pass

---

### Task 9: Update optio-api README

**Files:**
- Modify: `packages/optio-api/README.md`

- [ ] **Step 1: Rewrite the README with all adapter examples**

Replace the full contents of `packages/optio-api/README.md` with:

````markdown
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
````

---

### Task 10: Update root README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Level 3 description to mention all frameworks**

In `README.md`, find the Level 3 section:

```markdown
### Level 3: REST API (+ [optio-api](packages/optio-api))

Adds HTTP endpoints to your Node.js API server for process management and SSE streams for real-time status updates. Built on ts-rest contracts for type-safe client-server communication.
```

Replace it with:

```markdown
### Level 3: REST API (+ [optio-api](packages/optio-api))

Adds HTTP endpoints to your Node.js API server for process management and SSE streams for real-time status updates. Built on ts-rest contracts for type-safe client-server communication. Comes with ready-to-use adapters for Fastify, Express, and Next.js (both Pages Router and App Router).
```
