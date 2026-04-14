# Multi-Database Instance Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move prefix from URL path to query parameter, add optional database query parameter, and extend discovery to scan all databases on a MongoDB server.

**Architecture:** The contract drops `:prefix` from all paths and adds `database`/`prefix` as optional query params. A shared `resolveDb` helper resolves `(db, prefix)` from query params + adapter options. Discovery returns `{ instances: [{ database, prefix }] }`. The `OptioApiOptions` discriminated union accepts either `db` (single-db) or `mongoClient` (multi-db). The UI context adds `database` alongside `prefix`.

**Tech Stack:** TypeScript, MongoDB driver, ts-rest, Zod, React, Vitest

---

## File Structure

**Create:**
- `packages/optio-api/src/resolve-db.ts` — shared helper to resolve `(db, prefix)` from query params and options

**Modify:**
- `packages/optio-contracts/src/contract.ts` — remove `:prefix` from all paths, add `database`/`prefix` query params, rename discovery endpoint
- `packages/optio-contracts/src/index.ts` — update export name if needed
- `packages/optio-api/src/discovery.ts` — add `discoverInstances` supporting both single-db and multi-db modes
- `packages/optio-api/src/index.ts` — update exports
- `packages/optio-api/src/adapters/fastify.ts` — use `resolveDb`, update routes, update `OptioApiOptions` union type
- `packages/optio-api/src/adapters/express.ts` — same
- `packages/optio-api/src/adapters/nextjs-app.ts` — same
- `packages/optio-api/src/adapters/nextjs-pages.ts` — same
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — update URLs, add multi-db tests
- `packages/optio-ui/src/client.ts` — update contract usage
- `packages/optio-ui/src/hooks/usePrefixDiscovery.ts` — rename to `useInstanceDiscovery.ts`, return instances
- `packages/optio-ui/src/hooks/useProcessQueries.ts` — use query params instead of path params
- `packages/optio-ui/src/hooks/useProcessStream.ts` — use query params in SSE URL
- `packages/optio-ui/src/hooks/useProcessListStream.tsx` — use query params in SSE URL
- `packages/optio-ui/src/hooks/useProcessActions.ts` — use query params instead of path params
- `packages/optio-ui/src/context/OptioProvider.tsx` — add `database` to context and discovery
- `packages/optio-ui/src/context/useOptioContext.ts` — add `useOptioDatabase` hook
- `packages/optio-ui/src/index.ts` — update exports
- `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts` — rename and update for instances
- `packages/optio-ui/src/__tests__/OptioProvider.test.tsx` — add database tests
- `packages/optio-dashboard/src/server.ts` — pass `mongoClient` instead of `db`
- `packages/optio-dashboard/src/app/App.tsx` — use instances instead of prefixes

**Test:**
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts`
- `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`

---

### Task 1: Update contract — remove prefix from paths, add query params

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts`
- Modify: `packages/optio-contracts/src/index.ts`

- [ ] **Step 1: Rewrite processesContract**

Replace the full `processesContract` in `packages/optio-contracts/src/contract.ts`. Remove `:prefix` from all paths. Remove `pathParams` that only had `prefix`. Add `database` and `prefix` as optional query params on every route. For routes that had both `prefix` and `id` in pathParams, keep only `id`.

```typescript
const InstanceQuerySchema = z.object({
  database: z.string().optional(),
  prefix: z.string().optional(),
});

export const processesContract = c.router({
  list: {
    method: 'GET',
    path: '/processes',
    query: PaginationQuerySchema.extend({
      database: z.string().optional(),
      prefix: z.string().optional(),
      rootId: ObjectIdSchema.optional(),
      state: ProcessStateSchema.optional(),
    }).passthrough(),
    responses: {
      200: PaginatedResponseSchema(ProcessSchema),
    },
    summary: 'List and filter processes',
  },
  get: {
    method: 'GET',
    path: '/processes/:id',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
    },
    summary: 'Get single process',
  },
  getTree: {
    method: 'GET',
    path: '/processes/:id/tree',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema.extend({
      maxDepth: z.coerce.number().int().min(0).optional(),
    }),
    responses: {
      200: ProcessTreeNodeSchema,
      404: ErrorSchema,
    },
    summary: 'Get full process subtree',
  },
  getLog: {
    method: 'GET',
    path: '/processes/:id/log',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: PaginationQuerySchema.extend({
      database: z.string().optional(),
      prefix: z.string().optional(),
    }),
    responses: {
      200: PaginatedResponseSchema(LogEntrySchema),
      404: ErrorSchema,
    },
    summary: 'Get log entries for a single process',
  },
  getTreeLog: {
    method: 'GET',
    path: '/processes/:id/tree/log',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: PaginationQuerySchema.extend({
      database: z.string().optional(),
      prefix: z.string().optional(),
      maxDepth: z.coerce.number().int().min(0).optional(),
    }),
    responses: {
      200: PaginatedResponseSchema(LogEntrySchema.extend({
        processId: ObjectIdSchema,
        processLabel: z.string(),
      })),
      404: ErrorSchema,
    },
    summary: 'Get merged log entries across subtree',
  },
  launch: {
    method: 'POST',
    path: '/processes/:id/launch',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Launch a process',
  },
  cancel: {
    method: 'POST',
    path: '/processes/:id/cancel',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Request process cancellation',
  },
  dismiss: {
    method: 'POST',
    path: '/processes/:id/dismiss',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: c.noBody(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Dismiss process (reset to idle)',
  },
  resync: {
    method: 'POST',
    path: '/processes/resync',
    query: InstanceQuerySchema,
    body: z.object({ clean: z.boolean().optional() }),
    responses: {
      200: z.object({ message: z.string() }),
    },
    summary: 'Re-sync process definitions',
  },
});
```

- [ ] **Step 2: Replace discoveryContract**

In the same file, replace `discoveryContract`:

```typescript
const InstanceSchema = z.object({
  database: z.string(),
  prefix: z.string(),
});

export const discoveryContract = c.router({
  instances: {
    method: 'GET',
    path: '/optio/instances',
    responses: {
      200: z.object({ instances: z.array(InstanceSchema) }),
    },
    summary: 'Discover active optio instances across databases',
  },
});
```

- [ ] **Step 3: Update exports if needed**

In `packages/optio-contracts/src/index.ts`, verify `discoveryContract` is still exported (it should be — the name didn't change, only its contents).

- [ ] **Step 4: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-contracts/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 2: Create resolveDb helper and update discovery

**Files:**
- Create: `packages/optio-api/src/resolve-db.ts`
- Modify: `packages/optio-api/src/discovery.ts`
- Modify: `packages/optio-api/src/index.ts`

- [ ] **Step 1: Create the resolveDb module**

Create `packages/optio-api/src/resolve-db.ts`:

```typescript
import type { Db, MongoClient } from 'mongodb';

export interface SingleDbOptions {
  db: Db;
  mongoClient?: never;
}

export interface MultiDbOptions {
  mongoClient: MongoClient;
  db?: never;
}

export type DbOptions = SingleDbOptions | MultiDbOptions;

export function resolveDb(
  opts: DbOptions,
  query: { database?: string; prefix?: string },
): { db: Db; prefix: string } {
  const prefix = query.prefix || 'optio';

  if ('db' in opts && opts.db) {
    return { db: opts.db, prefix };
  }

  if (!query.database) {
    throw new Error('database query parameter is required in multi-db mode');
  }

  return { db: opts.mongoClient!.db(query.database), prefix };
}
```

- [ ] **Step 2: Rewrite discovery.ts**

Replace `packages/optio-api/src/discovery.ts`:

```typescript
import type { Db, MongoClient } from 'mongodb';
import type { DbOptions } from './resolve-db.js';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

interface OptioInstance {
  database: string;
  prefix: string;
}

async function discoverPrefixesInDb(db: Db): Promise<string[]> {
  const collections = await db.listCollections().toArray();
  const candidates = collections
    .map((c) => c.name)
    .filter((name) => name.endsWith('_processes'))
    .map((name) => name.slice(0, -'_processes'.length));

  const confirmed: string[] = [];

  for (const prefix of candidates) {
    const doc = await db.collection(`${prefix}_processes`).findOne();
    if (doc && REQUIRED_FIELDS.every((f) => f in doc)) {
      confirmed.push(prefix);
    }
  }

  return confirmed.sort();
}

export async function discoverInstances(opts: DbOptions): Promise<OptioInstance[]> {
  if ('db' in opts && opts.db) {
    const prefixes = await discoverPrefixesInDb(opts.db);
    const dbName = opts.db.databaseName;
    return prefixes.map((prefix) => ({ database: dbName, prefix }));
  }

  const adminDb = opts.mongoClient!.db().admin();
  const { databases } = await adminDb.listDatabases();
  const instances: OptioInstance[] = [];

  for (const dbInfo of databases) {
    const db = opts.mongoClient!.db(dbInfo.name);
    const prefixes = await discoverPrefixesInDb(db);
    for (const prefix of prefixes) {
      instances.push({ database: dbInfo.name, prefix });
    }
  }

  return instances.sort((a, b) =>
    a.database.localeCompare(b.database) || a.prefix.localeCompare(b.prefix),
  );
}
```

- [ ] **Step 3: Update exports**

In `packages/optio-api/src/index.ts`, replace the discovery export and add the resolve-db exports:

Replace:
```typescript
export { discoverPrefixes } from './discovery.js';
```

With:
```typescript
export { discoverInstances } from './discovery.js';
export { resolveDb, type DbOptions, type SingleDbOptions, type MultiDbOptions } from './resolve-db.js';
```

- [ ] **Step 4: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors (adapters will have errors since they still reference old code, but `--noEmit` on just the non-adapter files should work). If there are errors from adapters, that's expected — they'll be fixed in Task 3.

---

### Task 3: Update Fastify adapter

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`

- [ ] **Step 1: Rewrite the Fastify adapter**

Replace the full content of `packages/optio-api/src/adapters/fastify.ts`:

```typescript
// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { FastifyInstance } from 'fastify';
import type { Db } from 'mongodb';
import type { MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
import { discoverInstances } from '../discovery.js';
import { resolveDb, type DbOptions } from '../resolve-db.js';
import { checkAuth, type AuthCallback } from '../auth.js';

export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { redis, authenticate } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient
    ? { mongoClient: opts.mongoClient }
    : { db: opts.db! };

  if (!authenticate) throw new Error('authenticate option is required');

  app.addHook('onRequest', async (request, reply) => {
    const isWrite = request.method === 'POST';
    const authError = await checkAuth(request, authenticate, isWrite);
    if (authError) {
      return reply.code(authError.status).send(authError.body);
    }
  });

  const s = initServer();

  const routes = s.router(apiContract.processes, {
    list: async ({ query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.listProcesses(db, prefix, query);
      return { status: 200 as const, body: result };
    },
    get: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcess(db, prefix, params.id);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTree: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessTree(db, prefix, params.id, query.maxDepth);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getLog: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessLog(db, prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    getTreeLog: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.getProcessTreeLog(db, prefix, params.id, query);
      if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
      return { status: 200 as const, body: result };
    },
    launch: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.launchProcess(db, redis, prefix, params.id);
      return result as any;
    },
    cancel: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.cancelProcess(db, redis, prefix, params.id);
      return result as any;
    },
    dismiss: async ({ params, query }) => {
      const { db, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.dismissProcess(db, redis, prefix, params.id);
      return result as any;
    },
    resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean } }) => {
      const { prefix } = resolveDb(dbOpts, query);
      const result = await handlers.resyncProcesses(redis, prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
  });

  app.get('/api/optio/instances', async (_request, reply) => {
    const instances = await discoverInstances(dbOpts);
    return reply.send({ instances });
  });

  app.register(s.plugin(routes));

  app.get('/api/processes/:id/tree/stream', async (request: any, reply: any) => {
    const { id } = request.params as { id: string };
    const query = request.query as { database?: string; prefix?: string; maxDepth?: string };
    const { db, prefix } = resolveDb(dbOpts, query);
    const maxDepthNum = query.maxDepth !== undefined ? parseInt(query.maxDepth, 10) : undefined;

    const col = db.collection(`${prefix}_processes`);
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
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: maxDepthNum,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/stream', async (request: any, reply: any) => {
    const query = request.query as { database?: string; prefix?: string };
    const { db, prefix } = resolveDb(dbOpts, query);

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
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });
}
```

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: May still have errors from other adapters. Verify fastify adapter itself compiles.

---

### Task 4: Update Express adapter

**Files:**
- Modify: `packages/optio-api/src/adapters/express.ts`

- [ ] **Step 1: Rewrite the Express adapter**

Apply the same pattern as the Fastify adapter: use `resolveDb` to extract `(db, prefix)` from query params, use `discoverInstances` for the discovery endpoint, update `OptioApiOptions` to the discriminated union type, change SSE stream routes to use query params instead of `:prefix` path params.

Key changes from current code:
- `OptioApiOptions` becomes the same discriminated union
- All handlers: replace `params.prefix` with `resolveDb(dbOpts, query)`
- Discovery endpoint: `GET /api/optio/instances` returns `{ instances }`
- SSE routes: `/api/processes/:id/tree/stream` and `/api/processes/stream` (no `:prefix`)
- SSE routes extract `database`/`prefix` from `req.query`

Follow the exact same structure as the Fastify adapter in Task 3, adapted for Express APIs (`req.query`, `res.json`, etc.).

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`

---

### Task 5: Update Next.js App Router adapter

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`

- [ ] **Step 1: Rewrite the Next.js App Router adapter**

Apply the same pattern: `OptioApiOptions` discriminated union, `resolveDb` for all handlers, `discoverInstances` for discovery, SSE routes use query params.

Key differences from Fastify/Express:
- Uses `URL` and `searchParams` for query parsing
- SSE uses `ReadableStream` API
- Discovery check: `url.pathname.endsWith('/api/optio/instances')`
- Tree stream regex changes from `/\/api\/processes\/([^/]+)\/([^/]+)\/tree\/stream$/` to `/\/api\/processes\/([^/]+)\/tree\/stream$/` (only `id`, no `prefix`)
- List stream regex changes from `/\/api\/processes\/([^/]+)\/stream$/` to exact match `/\/api\/processes\/stream$/`
- Extract `database`/`prefix` from `url.searchParams`

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`

---

### Task 6: Update Next.js Pages Router adapter

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`

- [ ] **Step 1: Rewrite the Next.js Pages Router adapter**

Apply the same pattern. Key differences:
- Discovery check: `req.url?.endsWith('/api/optio/instances')`
- Tree stream regex: `/^\/api\/processes\/([^/]+)\/tree\/stream$/` (one capture for `id` only)
- List stream: exact match `/^\/api\/processes\/stream$/`
- Extract `database`/`prefix` from `req.query`

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors across all adapters now.

---

### Task 7: Update Fastify integration tests

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Update existing tests for new URL structure**

All existing test URLs need to change:
- `/api/processes/optio?limit=10` → `/api/processes?limit=10` (prefix defaults to 'optio')
- `/api/processes/optio/${id}` → `/api/processes/${id}`
- `/api/processes/optio/${id}/launch` → `/api/processes/${id}/launch`
- `/api/processes/optio/${id}/cancel` → `/api/processes/${id}/cancel`
- `/api/processes/optio/${id}/dismiss` → `/api/processes/${id}/dismiss`
- `/api/processes/optio/resync` → `/api/processes/resync`

Auth tests also need the same URL updates.

- [ ] **Step 2: Update discovery tests**

Replace the three existing discovery tests with instance-based ones:

```typescript
  it('GET /api/optio/instances — returns empty when no collections', async () => {
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/instances',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([]);
  });

  it('GET /api/optio/instances — discovers instances from collections with optio schema', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/instances',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([
      { database: 'optio_test_fastify', prefix: 'optio' },
    ]);
  });

  it('GET /api/optio/instances — ignores collections without optio schema', async () => {
    await db.collection('fake_processes').insertOne({ unrelated: true });
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/instances',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([]);

    await db.collection('fake_processes').drop();
  });
```

- [ ] **Step 3: Add test with explicit prefix query param**

```typescript
  it('GET /api/processes?prefix=optio&limit=10 — lists with explicit prefix', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/processes?prefix=optio&limit=10',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items).toHaveLength(1);
  });
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests pass.

---

### Task 8: Update UI client and hooks

**Files:**
- Modify: `packages/optio-ui/src/client.ts`
- Rename: `packages/optio-ui/src/hooks/usePrefixDiscovery.ts` → `packages/optio-ui/src/hooks/useInstanceDiscovery.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessActions.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessStream.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessListStream.tsx`
- Modify: `packages/optio-ui/src/context/useOptioContext.ts`
- Modify: `packages/optio-ui/src/index.ts`

- [ ] **Step 1: Client stays the same**

The `client.ts` already imports both contracts. The contract names (`processesContract`, `discoveryContract`) didn't change, only their contents. No change needed here.

- [ ] **Step 2: Create useInstanceDiscovery.ts (replace usePrefixDiscovery.ts)**

Delete `packages/optio-ui/src/hooks/usePrefixDiscovery.ts` and create `packages/optio-ui/src/hooks/useInstanceDiscovery.ts`:

```typescript
import { useOptioClient } from '../context/useOptioContext.js';

export interface OptioInstance {
  database: string;
  prefix: string;
}

interface UseInstancesResult {
  instances: OptioInstance[];
  isLoading: boolean;
  error: unknown;
}

export function useInstances(): UseInstancesResult {
  const client = useOptioClient();
  const { data, isLoading, error } = client.discovery.instances.useQuery(
    ['optio-instances'],
    {},
  );
  return {
    instances: data?.body?.instances ?? [],
    isLoading,
    error,
  };
}

interface UseInstanceDiscoveryResult {
  instance: OptioInstance | null;
  instances: OptioInstance[];
  isLoading: boolean;
}

export function useInstanceDiscovery(): UseInstanceDiscoveryResult {
  const { instances, isLoading } = useInstances();
  const instance = instances.length === 1 ? instances[0] : null;
  return { instance, instances, isLoading };
}
```

- [ ] **Step 3: Add useOptioDatabase to context hooks**

In `packages/optio-ui/src/context/useOptioContext.ts`, add:

```typescript
export function useOptioDatabase(): string | undefined {
  return useContext(OptioContext).database;
}
```

- [ ] **Step 4: Update useProcessQueries.ts**

Replace all uses of `params: { prefix }` and `params: { prefix, id }` with query params. Import `useOptioDatabase` alongside `useOptioPrefix`. For each hook, add `database` to query and queryKey:

```typescript
import { useOptioPrefix, useOptioDatabase, useOptioClient } from '../context/useOptioContext.js';

export function useProcessList(options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.list.useQuery(
    ['processes', database, prefix],
    { query: { database, prefix, limit: 50 } },
    { queryKey: ['processes', database, prefix], refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    processes: data?.status === 200 ? data.body.items : [],
    totalCount: data?.status === 200 ? data.body.totalCount : 0,
    isLoading,
  };
}

export function useProcess(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.get.useQuery(
    ['process', database, prefix, id],
    { params: { id: id! }, query: { database, prefix } },
    { queryKey: ['process', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    process: data?.status === 200 ? data.body : null,
    isLoading,
  };
}

export function useProcessTree(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTree.useQuery(
    ['process-tree', database, prefix, id],
    { params: { id: id! }, query: { database, prefix } },
    { queryKey: ['process-tree', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body : null;
}

export function useProcessTreeLog(id: string | undefined, options?: { refetchInterval?: number | false; limit?: number }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTreeLog.useQuery(
    ['process-tree-log', database, prefix, id],
    { params: { id: id! }, query: { database, prefix, limit: options?.limit ?? 100 } },
    { queryKey: ['process-tree-log', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body.items : [];
}
```

- [ ] **Step 5: Update useProcessActions.ts**

Replace `params: { prefix, ... }` with `params: { ... }, query: { database, prefix }`:

```typescript
import { useQueryClient } from '@tanstack/react-query';
import { useOptioPrefix, useOptioDatabase, useOptioClient } from '../context/useOptioContext.js';

interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

export function useProcessActions(options?: ProcessActionsOptions) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const queryClient = useQueryClient();

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['processes'] });

  const launchMutation = api.processes.launch.useMutation({ onSuccess: invalidate });
  const cancelMutation = api.processes.cancel.useMutation({ onSuccess: invalidate });
  const dismissMutation = api.processes.dismiss.useMutation({ onSuccess: invalidate });
  const resyncMutation = api.processes.resync.useMutation({
    onSuccess: (_data: any, variables: any) => {
      options?.onResyncSuccess?.(variables.body?.clean ?? false);
      invalidate();
    },
  });

  return {
    launch: (processId: string) => launchMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    cancel: (processId: string) => cancelMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    resync: () => resyncMutation.mutate({ query: { database, prefix }, body: {} }),
    resyncClean: () => resyncMutation.mutate({ query: { database, prefix }, body: { clean: true } }),
    isResyncing: resyncMutation.isPending,
  };
}
```

- [ ] **Step 6: Update useProcessStream.ts**

Change the SSE URL from `${baseUrl}/api/processes/${prefix}/${processId}/tree/stream?maxDepth=${maxDepth}` to use query params:

```typescript
const url = `${baseUrl}/api/processes/${processId}/tree/stream?prefix=${encodeURIComponent(prefix)}&maxDepth=${maxDepth}${database ? `&database=${encodeURIComponent(database)}` : ''}`;
```

Import `useOptioDatabase` and add it to the hook's dependencies:

```typescript
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from '../context/useOptioContext.js';
```

In `useProcessStream`, add:
```typescript
const database = useOptioDatabase();
```

Update the `connect` callback's dependency array to include `database`.

- [ ] **Step 7: Update useProcessListStream.tsx**

Change the SSE URL from `${baseUrl}/api/processes/${prefix}/stream` to:
```typescript
const url = `${baseUrl}/api/processes/stream?prefix=${encodeURIComponent(prefix)}${database ? `&database=${encodeURIComponent(database)}` : ''}`;
```

Update the `connect` function signature to accept `database`:
```typescript
function connect(baseUrl: string, prefix: string, database?: string)
```

Update the key to include database:
```typescript
const key = `${baseUrl}|${database}|${prefix}`;
```

In `useProcessListStream`, get `database` from context and pass it to `connect`.

- [ ] **Step 8: Update index.ts exports**

In `packages/optio-ui/src/index.ts`, replace:
```typescript
export { usePrefixes, usePrefixDiscovery } from './hooks/usePrefixDiscovery.js';
```
With:
```typescript
export { useInstances, useInstanceDiscovery, type OptioInstance } from './hooks/useInstanceDiscovery.js';
```

- [ ] **Step 9: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-ui/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 9: Update OptioProvider

**Files:**
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`

- [ ] **Step 1: Rewrite OptioProvider**

Replace `packages/optio-ui/src/context/OptioProvider.tsx`:

```tsx
import { createContext, useMemo, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';

interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  baseUrl: string;
  client: OptioClient;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  database?: string;
  baseUrl?: string;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, explicitDatabase, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  explicitDatabase: string | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { instance: discoveredInstance } = useInstanceDiscovery();
  const prefix = explicitPrefix ?? discoveredInstance?.prefix ?? 'optio';
  const database = explicitDatabase ?? discoveredInstance?.database;

  return (
    <OptioContext.Provider value={{ prefix, database, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, database, baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', database, baseUrl, client }}>
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
```

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-ui/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 10: Update UI tests

**Files:**
- Delete: `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts`
- Create: `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts`
- Modify: `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`

- [ ] **Step 1: Create useInstanceDiscovery tests**

Delete `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts` and create `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';

vi.mock('../context/useOptioContext.js', () => ({
  useOptioClient: () => ({
    discovery: {
      instances: {
        useQuery: (_key: unknown, _args: unknown) => mockQueryResult,
      },
    },
  }),
}));

let mockQueryResult: { data: any; isLoading: boolean; error: unknown };

describe('useInstanceDiscovery', () => {
  it('returns null instance when loading', () => {
    mockQueryResult = { data: undefined, isLoading: true, error: null };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('returns the instance when exactly one is found', () => {
    mockQueryResult = {
      data: { body: { instances: [{ database: 'mydb', prefix: 'myapp' }] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toEqual({ database: 'mydb', prefix: 'myapp' });
    expect(result.current.instances).toHaveLength(1);
  });

  it('returns null instance when multiple are found', () => {
    mockQueryResult = {
      data: { body: { instances: [
        { database: 'db1', prefix: 'optio' },
        { database: 'db2', prefix: 'myapp' },
      ] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toHaveLength(2);
  });

  it('returns null instance when none are found', () => {
    mockQueryResult = {
      data: { body: { instances: [] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toEqual([]);
  });
});
```

- [ ] **Step 2: Update OptioProvider tests**

Replace `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';

let mockDiscoveryResult = {
  instance: null as { database: string; prefix: string } | null,
  instances: [] as { database: string; prefix: string }[],
  isLoading: false,
};

vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => mockDiscoveryResult,
}));

vi.mock('../client.js', () => ({
  createOptioClient: () => ({}),
}));

const { OptioProvider } = await import('../context/OptioProvider.js');

function ContextDisplay() {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  return (
    <>
      <div data-testid="prefix">{prefix}</div>
      <div data-testid="database">{database ?? 'undefined'}</div>
    </>
  );
}

function renderWithProvider(props: { prefix?: string; database?: string }) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <OptioProvider {...props}>
        <ContextDisplay />
      </OptioProvider>
    </QueryClientProvider>,
  );
}

describe('OptioProvider resolution', () => {
  it('uses explicit prefix and database when provided', () => {
    mockDiscoveryResult = {
      instance: { database: 'discovered-db', prefix: 'discovered' },
      instances: [{ database: 'discovered-db', prefix: 'discovered' }],
      isLoading: false,
    };
    renderWithProvider({ prefix: 'explicit', database: 'explicit-db' });
    expect(screen.getByTestId('prefix').textContent).toBe('explicit');
    expect(screen.getByTestId('database').textContent).toBe('explicit-db');
  });

  it('uses discovered instance when no explicit values given', () => {
    mockDiscoveryResult = {
      instance: { database: 'auto-db', prefix: 'auto' },
      instances: [{ database: 'auto-db', prefix: 'auto' }],
      isLoading: false,
    };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('auto');
    expect(screen.getByTestId('database').textContent).toBe('auto-db');
  });

  it('falls back to optio when no explicit values and discovery returns null', () => {
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('optio');
    expect(screen.getByTestId('database').textContent).toBe('undefined');
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd packages/optio-ui && npx vitest run src/__tests__/`
Expected: All tests pass.

---

### Task 11: Update dashboard

**Files:**
- Modify: `packages/optio-dashboard/src/server.ts`
- Modify: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Update server.ts to pass mongoClient**

In `packages/optio-dashboard/src/server.ts`, change the `registerOptioApi` call from passing `db` to passing `mongoClient`. The server still needs `db` for Better Auth, so keep the `db` variable but don't pass it to optio-api.

Replace:
```typescript
  await registerOptioApi(app, {
    db,
    redis,
    authenticate: async (request) => {
```

With:
```typescript
  await registerOptioApi(app, {
    mongoClient,
    redis,
    authenticate: async (request) => {
```

- [ ] **Step 2: Update App.tsx for instances**

Replace the instance-selection logic in `packages/optio-dashboard/src/app/App.tsx`. Change imports from `usePrefixes` to `useInstances`. Update `PrefixSelector` to `InstanceSelector` showing `"database/prefix"` format. Update `AppContent` to work with instances instead of prefixes. Pass both `database` and `prefix` to `OptioProvider`.

Replace imports:
```typescript
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  useInstances,
} from 'optio-ui';
```

Replace `PrefixSelector`:
```typescript
function InstanceSelector({ onSelect }: { onSelect: (instance: { database: string; prefix: string }) => void }) {
  const { instances, isLoading, error } = useInstances();

  if (isLoading) return null;
  if (error) return <Alert type="error" message="Failed to detect instances" />;
  if (instances.length === 0) {
    return <Alert type="info" message="No optio instance detected in the database" />;
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Text>Multiple optio instances detected. Select one:</Typography.Text>
      <Select
        style={{ width: '100%', marginTop: 8 }}
        placeholder="Select instance"
        options={instances.map((inst) => ({
          label: `${inst.database}/${inst.prefix}`,
          value: `${inst.database}/${inst.prefix}`,
        }))}
        onChange={(value) => {
          const [database, ...rest] = value.split('/');
          onSelect({ database, prefix: rest.join('/') });
        }}
      />
    </div>
  );
}
```

Replace `AppContent`:
```typescript
function AppContent() {
  const { instances, isLoading } = useInstances();
  const [manualInstance, setManualInstance] = useState<{ database: string; prefix: string } | null>(null);

  if (isLoading) return null;

  if (instances.length > 1 && !manualInstance) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <InstanceSelector onSelect={setManualInstance} />
      </Layout>
    );
  }

  if (instances.length === 0) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <Alert
          type="info"
          message="No optio instance detected in the database"
          style={{ margin: 24 }}
        />
      </Layout>
    );
  }

  const selected = manualInstance ?? instances[0];

  return (
    <OptioProvider prefix={selected.prefix} database={selected.database}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <Dashboard />
      </Layout>
    </OptioProvider>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-dashboard/tsconfig.json --noEmit`
Expected: No errors (aside from pre-existing optio-api/fastify module resolution issue).

---

### Task 12: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `pnpm test`
Expected: All tests pass across all packages.

- [ ] **Step 2: Fix any failures**

Address any test failures before finalizing.
