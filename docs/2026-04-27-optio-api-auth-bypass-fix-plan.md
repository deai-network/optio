# optio-api Auth Bypass — Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore enforcement of the `authenticate` callback across all four `optio-api` adapters (REST, SSE, instance discovery, widget proxy), with regression-alarm tests in place per adapter.

**Architecture:** One global enforcement point per adapter, registered before any route handler. Calls the existing `checkAuth(req, authenticate, isWriteMethod(req.method))` from `packages/optio-api/src/auth.ts` + `widget-proxy-core.ts`. The widget-proxy `preHandler`'s inline `checkAuth` is left in place (defense in depth; correct, isolated code, not in scope to remove).

**Tech Stack:** TypeScript, Fastify, Express, Next.js (Pages + App Router), `@ts-rest/*`, vitest, MongoDB (Docker), ioredis-mock.

---

## Reference: Spec

See `docs/2026-04-27-optio-api-auth-bypass-fix-design.md` for the design rationale, origin trace, and out-of-scope items.

## Reference: Existing patterns

The fastify widget-proxy `preHandler` at `packages/optio-api/src/adapters/fastify.ts:182` is the existing working example of `checkAuth` invocation:

```typescript
const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
if (authResult) {
  reply.code(authResult.status).send(authResult.body);
  return;
}
```

`isWriteMethod` is exported from `packages/optio-api/src/widget-proxy-core.ts`:

```typescript
export function isWriteMethod(method: string): boolean {
  const m = method.toUpperCase();
  return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS';
}
```

`checkAuth` returns either `null` (allowed) or `{ status: 401 | 403, body: { message: string } }` (denied). Re-use these types — do not reinvent.

## Reference: Test fixtures

Each adapter test file already has Mongo + ioredis-mock setup and a `seedProcess()` helper. Look at the `Fastify adapter integration tests` describe block (currently the only describe block in `fastify.test.ts`) to see how `createApp()` wires the operator-fixture authenticate. New auth describe blocks add their own per-test `createAppWithAuth(role)` factory.

## Test command

From the monorepo root:

```bash
pnpm --filter optio-api test
```

This runs vitest once. MongoDB must be reachable at `mongodb://localhost:27017` (per `MONGO_URL`). If running locally, ensure a MongoDB container is up:

```bash
docker ps | grep -E 'mongo|27017' || echo "Mongo not running"
```

If not running, start one (per project policy, MongoDB is only used via Docker, not local mongod):

```bash
docker run --rm -d --name optio-mongo -p 27017:27017 mongo:7
```

## TypeScript compile check

After each adapter change, verify the package still compiles:

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

(Direct `tsc` per project policy; do not use `npx`.)

---

## Task 0: Set up feature branch

**Files:** none (repo state).

- [ ] **Step 1: Verify clean working tree on `main`**

```bash
git -C /home/csillag/deai/optio status
git -C /home/csillag/deai/optio rev-parse --abbrev-ref HEAD
```

Expected: clean tree, current branch `main`.

- [ ] **Step 2: Create feature branch in-place**

```bash
git -C /home/csillag/deai/optio checkout -b feat/optio-api-auth-bypass-fix
```

Expected: switched to a new branch.

- [ ] **Step 3: Verify branch HEAD**

```bash
git -C /home/csillag/deai/optio log --oneline -3
```

Expected: top commit is `docs: optio-api auth bypass fix design` (commit `91dcdfa`), parent is `c022013 test(optio-opencode): simplify supports_resume=False test to use monkeypatch` (the spec's recorded base revision). If the spec commit is missing, abort and confirm the spec was committed before starting this plan.

---

## Task 1: Fastify — add failing auth tests

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts` (append new describe block)

- [ ] **Step 1: Append the auth describe block**

Append this block to the end of `packages/optio-api/src/adapters/__tests__/fastify.test.ts`, **after** the closing `});` of `describe('Fastify adapter integration tests', …)`:

```typescript
describe('Fastify adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const app = Fastify();
    registerOptioApi(app, { db, redis, authenticate });
    return app;
  }

  it('null role → 401 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10' });
    expect(res.statusCode).toBe(401);
  });

  it('null role → 401 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(401);
  });

  it('null role → 401 on SSE list stream', async () => {
    const app = createAppWithAuth(() => null);
    const res = await app.inject({ method: 'GET', url: '/api/processes/stream' });
    expect(res.statusCode).toBe(401);
  });

  it('null role → 401 on SSE tree stream', async () => {
    const doc = await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/${doc._id.toString()}/tree/stream`,
    });
    expect(res.statusCode).toBe(401);
  });

  it('null role → 401 on /api/optio/instances', async () => {
    const app = createAppWithAuth(() => null);
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances' });
    expect(res.statusCode).toBe(401);
  });

  it('viewer → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10' });
    expect(res.statusCode).toBe(200);
  });

  it('viewer → 403 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(403);
  });

  it('operator → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'operator');
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10' });
    expect(res.statusCode).toBe(200);
  });

  it('operator → 200 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(200);
  });

  it('async authenticate works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'operator');
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10' });
    expect(res.statusCode).toBe(200);
  });
});
```

- [ ] **Step 2: Run only this describe block to confirm RED**

```bash
pnpm --filter optio-api test -- -t 'Fastify adapter auth'
```

Expected: the five "401" cases fail (currently return 200 because no auth is enforced). The "viewer → 403 on REST POST" case fails (returns 200). The 200-success cases pass. The "async authenticate" case passes. Net: 6 failures, 4 passes.

---

## Task 2: Fastify — wire onRequest hook to make tests pass

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts` (inside `registerOptioApi`)

- [ ] **Step 1: Add the global onRequest hook**

In `packages/optio-api/src/adapters/fastify.ts`, locate `registerOptioApi`. Currently the body starts (around line 344-346):

```typescript
export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  // Widget reverse-proxy lives under /api/widget/<database>/<prefix>/<processId>/…
```

Insert the hook **between** the `dbOpts` line and the widget-proxy comment:

```typescript
export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  // Global auth enforcement. Runs before route handlers (and before the
  // widget-proxy plugin's preHandler), so REST, SSE, discovery, and widget
  // routes all pass through checkAuth here. The widget-proxy preHandler's
  // own checkAuth call is left in place as defense in depth.
  app.addHook('onRequest', async (req, reply) => {
    const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
    if (authResult) {
      reply.code(authResult.status).send(authResult.body);
    }
  });

  // Widget reverse-proxy lives under /api/widget/<database>/<prefix>/<processId>/…
```

`checkAuth` and `isWriteMethod` are already imported at the top of this file (lines 17-25). No new imports needed.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

Expected: zero errors. (`@ts-nocheck` at the top of `fastify.ts` will mask any local type drift; check stays useful for the rest of the package.)

- [ ] **Step 3: Run the auth describe block — expect GREEN**

```bash
pnpm --filter optio-api test -- -t 'Fastify adapter auth'
```

Expected: all 10 cases pass.

- [ ] **Step 4: Run the entire optio-api test suite to check nothing else regressed**

```bash
pnpm --filter optio-api test
```

Expected: all tests pass. The pre-existing `Fastify adapter integration tests` describe block uses `authenticate: () => 'operator'` so its assertions still hold.

- [ ] **Step 5: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/__tests__/fastify.test.ts
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
fix(optio-api): enforce authenticate on all Fastify routes

Restore the global onRequest hook that calls checkAuth before any
route handler. Was dropped in the multi-database refactor merge
(4b76363) without anyone noticing because the test describe block
covering the auth cases was lost in the same merge.

Adds back a Fastify adapter auth describe block with 10 cases
covering REST GET/POST, SSE list and tree streams, /api/optio/
instances, and viewer/operator role assertions.

Widget-proxy preHandler retains its own checkAuth as defense in
depth (already-correct code, not in scope to touch).
EOF
)"
```

---

## Task 3: Express — add failing auth tests

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts` (append new describe block)

- [ ] **Step 1: Append the auth describe block**

Append to the end of `packages/optio-api/src/adapters/__tests__/express.test.ts`:

```typescript
describe('Express adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const app = express();
    app.use(express.json());
    registerOptioApi(app, { db, redis, authenticate });
    return app;
  }

  it('null role → 401 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(401);
  });

  it('null role → 401 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE list stream', async () => {
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/processes/stream');
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE tree stream', async () => {
    const doc = await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await request(app).get(`/api/processes/${doc._id.toString()}/tree/stream`);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on /api/optio/instances', async () => {
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/optio/instances');
    expect(res.status).toBe(401);
  });

  it('viewer → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });

  it('viewer → 403 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(403);
  });

  it('operator → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'operator');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });

  it('operator → 200 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(200);
  });

  it('async authenticate works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'operator');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run the auth block — expect RED**

```bash
pnpm --filter optio-api test -- -t 'Express adapter auth'
```

Expected: the six "401" / "403" cases fail (currently return 200). The four success cases pass. Net: 6 failures, 4 passes.

---

## Task 4: Express — wire app.use middleware

**Files:**
- Modify: `packages/optio-api/src/adapters/express.ts` (imports + body of `registerOptioApi`)

- [ ] **Step 1: Add re-imports for `checkAuth` and `isWriteMethod`**

At the top of `packages/optio-api/src/adapters/express.ts` (after the existing `import { resolveDb, type DbOptions } from '../resolve-db.js';` line), add:

```typescript
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { isWriteMethod } from '../widget-proxy-core.js';
```

(`AuthCallback` is the type symbol used in the existing `OptioApiOptions` declaration just below.)

- [ ] **Step 2: Add the global middleware in `registerOptioApi`**

In `packages/optio-api/src/adapters/express.ts`, locate `registerOptioApi`. Currently the body starts:

```typescript
export function registerOptioApi(app: Express, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  createExpressEndpoints(apiContract.processes, {
```

Insert the middleware **between** the `dbOpts` line and the `createExpressEndpoints` call:

```typescript
export function registerOptioApi(app: Express, opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  // Global auth enforcement. Runs on every /api/* request before any
  // ts-rest, SSE, or discovery handler.
  app.use('/api', async (req, res, next) => {
    const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
    if (authResult) {
      res.status(authResult.status).json(authResult.body);
      return;
    }
    next();
  });

  createExpressEndpoints(apiContract.processes, {
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 4: Run the auth describe block — expect GREEN**

```bash
pnpm --filter optio-api test -- -t 'Express adapter auth'
```

Expected: all 10 cases pass.

- [ ] **Step 5: Run the entire optio-api test suite to check nothing else regressed**

```bash
pnpm --filter optio-api test
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-api/src/adapters/express.ts packages/optio-api/src/adapters/__tests__/express.test.ts
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
fix(optio-api): enforce authenticate on all Express routes

Restore the /api app.use middleware that calls checkAuth before any
ts-rest, SSE, or discovery handler. Same regression cause as the
Fastify fix: the multi-database refactor merge silently dropped the
auth wiring from the adapter rewrite.

Adds back an Express adapter auth describe block with 10 cases.
EOF
)"
```

---

## Task 5: Next.js App Router — add failing auth tests

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` (append new describe block)

- [ ] **Step 1: Append the auth describe block**

Append to the end of `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`:

```typescript
describe('Next.js App Router adapter auth', () => {
  function makeHandlers(authenticate: (req: any) => any) {
    return createOptioRouteHandlers({ db, redis, authenticate });
  }

  it('null role → 401 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => null);
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE list stream', async () => {
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/processes/stream');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE tree stream', async () => {
    const doc = await seedProcess();
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest(
      `http://localhost/api/processes/${doc._id.toString()}/tree/stream`,
    );
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on /api/optio/instances', async () => {
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/optio/instances');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('viewer → 200 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => 'viewer');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('viewer → 403 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => 'viewer');
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(403);
  });

  it('operator → 200 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => 'operator');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('operator → 200 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => 'operator');
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
  });

  it('async authenticate works', async () => {
    await seedProcess();
    const { GET } = makeHandlers(async () => 'operator');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run the auth block — expect RED**

```bash
pnpm --filter optio-api test -- -t 'Next.js App Router adapter auth'
```

Expected: 6 failures, 4 passes.

---

## Task 6: Next.js App Router — wire authGate

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts` (imports + `createOptioRouteHandlers` body)

- [ ] **Step 1: Add re-imports for `checkAuth` and `isWriteMethod`**

At the top of `packages/optio-api/src/adapters/nextjs-app.ts`, after the existing `import { resolveDb, type DbOptions } from '../resolve-db.js';` line, add:

```typescript
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { isWriteMethod } from '../widget-proxy-core.js';
```

- [ ] **Step 2: Add the `authGate` helper inside `createOptioRouteHandlers`**

In `packages/optio-api/src/adapters/nextjs-app.ts`, locate `createOptioRouteHandlers`. Currently the body is roughly:

```typescript
export function createOptioRouteHandlers(opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  const tsRestHandlers = createNextHandler(
    apiContract.processes,
    { … },
    { handlerType: 'app-router' },
  );

  async function GET(request: Request): Promise<Response> { … }
  async function POST(request: Request): Promise<Response> { … }

  return { GET, POST };
}
```

Insert the helper **between** the `dbOpts` line and the `createNextHandler` call:

```typescript
  const dbOpts: DbOptions = …;

  async function authGate(request: Request): Promise<Response | null> {
    const authResult = await checkAuth(request, opts.authenticate, isWriteMethod(request.method));
    if (!authResult) return null;
    return new Response(JSON.stringify(authResult.body), {
      status: authResult.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const tsRestHandlers = createNextHandler(
```

- [ ] **Step 3: Call `authGate` at the top of `GET` and `POST`**

Modify the existing `GET` and `POST` functions to call `authGate` first. The current `GET` starts:

```typescript
async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const { pathname } = url;
  …
}
```

Change to:

```typescript
async function GET(request: Request): Promise<Response> {
  const denied = await authGate(request);
  if (denied) return denied;
  const url = new URL(request.url);
  const { pathname } = url;
  …
}
```

Current `POST` starts:

```typescript
async function POST(request: Request): Promise<Response> {
  return tsRestHandlers(request);
}
```

Change to:

```typescript
async function POST(request: Request): Promise<Response> {
  const denied = await authGate(request);
  if (denied) return denied;
  return tsRestHandlers(request);
}
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Run the auth describe block — expect GREEN**

```bash
pnpm --filter optio-api test -- -t 'Next.js App Router adapter auth'
```

Expected: all 10 cases pass.

- [ ] **Step 6: Run the entire optio-api test suite to check nothing else regressed**

```bash
pnpm --filter optio-api test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-api/src/adapters/nextjs-app.ts packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
fix(optio-api): enforce authenticate on Next.js App Router routes

Add authGate helper called at the top of GET and POST. Same
regression cause as the Fastify and Express fixes.

Adds a Next.js App Router adapter auth describe block with 10 cases.
EOF
)"
```

---

## Task 7: Next.js Pages Router — add failing auth tests

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` (append new describe block)

- [ ] **Step 1: Append the auth describe block**

Append to the end of `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`. Reuse the existing `createApp(handler?)` pattern:

```typescript
describe('Next.js Pages Router adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const handler = createOptioHandler({ db, redis, authenticate });
    const app = express();
    app.use(express.json());
    app.use((req, _res, next) => {
      const pathname = req.path;
      const segments = pathname.split('/').filter(Boolean);
      const existingQuery = Object.fromEntries(Object.entries(req.query as Record<string, unknown>));
      Object.defineProperty(req, 'query', {
        value: { ...existingQuery, 'ts-rest': segments },
        writable: true,
        configurable: true,
      });
      next();
    });
    app.use((req, res) => handler(req as any, res as any));
    return app;
  }

  it('null role → 401 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(401);
  });

  it('null role → 401 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE list stream', async () => {
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/processes/stream');
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE tree stream', async () => {
    const doc = await seedProcess();
    const app = createAppWithAuth(() => null);
    const res = await request(app).get(`/api/processes/${doc._id.toString()}/tree/stream`);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on /api/optio/instances', async () => {
    const app = createAppWithAuth(() => null);
    const res = await request(app).get('/api/optio/instances');
    expect(res.status).toBe(401);
  });

  it('viewer → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });

  it('viewer → 403 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(403);
  });

  it('operator → 200 on REST GET', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'operator');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });

  it('operator → 200 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(200);
  });

  it('async authenticate works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'operator');
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run the auth block — expect RED**

```bash
pnpm --filter optio-api test -- -t 'Next.js Pages Router adapter auth'
```

Expected: 6 failures, 4 passes.

---

## Task 8: Next.js Pages Router — wire authGate + make `authenticate` mandatory

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts` (imports + `OptioApiOptions` type + `createOptioHandler` body)

- [ ] **Step 1: Add re-imports for `checkAuth` and `isWriteMethod`**

At the top of `packages/optio-api/src/adapters/nextjs-pages.ts`, after the existing `import { resolveDb, type DbOptions } from '../resolve-db.js';` line, add:

```typescript
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { isWriteMethod } from '../widget-proxy-core.js';
```

- [ ] **Step 2: Make `authenticate` mandatory in the type union**

Change the `OptioApiOptions` declaration. Current:

```typescript
export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<NextApiRequest>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);
```

Drop the `?` on `authenticate`:

```typescript
export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<NextApiRequest>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);
```

This brings nextjs-pages in line with the other three adapters and matches the post-`c871dfc` mandatory-auth contract.

- [ ] **Step 3: Add `authGate` helper and call it at the top of the returned handler**

In `createOptioHandler`, the current returned handler starts:

```typescript
return async (req: NextApiRequest, res: NextApiResponse) => {
  const url = req.url ?? '';
  const method = req.method ?? '';

  // Discovery endpoint: /api/optio/instances
  …
};
```

Wrap it with auth-gate:

```typescript
return async (req: NextApiRequest, res: NextApiResponse) => {
  const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method ?? 'GET'));
  if (authResult) {
    res.status(authResult.status).json(authResult.body);
    return;
  }

  const url = req.url ?? '';
  const method = req.method ?? '';

  // Discovery endpoint: /api/optio/instances
  …
};
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Run the auth describe block — expect GREEN**

```bash
pnpm --filter optio-api test -- -t 'Next.js Pages Router adapter auth'
```

Expected: all 10 cases pass.

- [ ] **Step 6: Run the entire optio-api test suite**

```bash
pnpm --filter optio-api test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-api/src/adapters/nextjs-pages.ts packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
fix(optio-api): enforce authenticate on Next.js Pages Router routes

Wrap the returned handler in an authGate. Same regression cause as
the other three adapter fixes.

Also drops the optional `?` on OptioApiOptions.authenticate so the
contract is consistent with the other three adapters (mandatory
since c871dfc).

Adds a Next.js Pages Router adapter auth describe block with 10 cases.
EOF
)"
```

---

## Task 9: Update AGENTS.md to reflect auth invariant

**Files:**
- Modify: `AGENTS.md` (root, around the OptioApiOptions interface declaration)
- Modify: `packages/optio-api/AGENTS.md` (around the OptioApiOptions interface declaration)

- [ ] **Step 1: Update root AGENTS.md OptioApiOptions**

In `AGENTS.md` (root), the section `## TypeScript: optio-api` → `### OptioApiOptions` shows:

```typescript
interface OptioApiOptions {
  db: Db;       // mongodb Db instance
  redis: Redis; // ioredis Redis instance
  prefix: string;
}
```

Replace with:

```typescript
import type { AuthCallback, OptioRole } from 'optio-api';

interface OptioApiOptions {
  db: Db;       // mongodb Db instance
  redis: Redis; // ioredis Redis instance
  prefix?: string;                            // optional; default 'optio'
  authenticate: AuthCallback<TRequest>;       // TRequest depends on adapter
}

// AuthCallback returns 'viewer' (read-only) | 'operator' (read+write) | null (deny).
// Enforced on every request to every route across all four adapters: REST,
// SSE streams, /api/optio/instances discovery, and the /api/widget/* proxy.
// Reads (GET/HEAD/OPTIONS) require viewer or operator; writes require operator.
```

(`prefix` was already optional in code; the doc was stale on this too. Same edit fixes both.)

- [ ] **Step 2: Update optio-api package AGENTS.md OptioApiOptions**

In `packages/optio-api/AGENTS.md`, the `## OptioApiOptions` block shows:

```typescript
interface OptioApiOptions {
  db: Db;         // MongoDB Db instance
  redis: Redis;   // ioredis Redis instance
  prefix: string; // Collection prefix; reads/writes `{prefix}_processes`
}
```

Replace with:

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

Then add a new subsection `## Authentication` immediately after the `## OptioApiOptions` section (before `## Fastify Adapter`):

```markdown
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
```

- [ ] **Step 3: Commit AGENTS.md changes**

```bash
git -C /home/csillag/deai/optio add AGENTS.md packages/optio-api/AGENTS.md
git -C /home/csillag/deai/optio commit -m "$(cat <<'EOF'
docs(optio-api): document authenticate enforcement on every route

Update both root and package-level AGENTS.md to show the
`authenticate` field on OptioApiOptions and add an Authentication
subsection asserting the callback is invoked on every request to
every route across all four adapters.
EOF
)"
```

---

## Task 10: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full optio-api suite one more time**

```bash
pnpm --filter optio-api test
```

Expected: every test passes, including the four new auth describe blocks (40 new cases total) and the pre-existing widget-proxy auth test.

- [ ] **Step 2: TypeScript check across the package**

```bash
pnpm --filter optio-api exec node_modules/.bin/tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 3: Confirm git log**

```bash
git -C /home/csillag/deai/optio log --oneline main..HEAD
```

Expected output: five commits ahead of `main`, in this order from oldest to newest:

```
fix(optio-api): enforce authenticate on all Fastify routes
fix(optio-api): enforce authenticate on all Express routes
fix(optio-api): enforce authenticate on Next.js App Router routes
fix(optio-api): enforce authenticate on Next.js Pages Router routes
docs(optio-api): document authenticate enforcement on every route
```

(If the design-spec commit `91dcdfa` was on the branch when it was created, it will appear at the bottom — that's fine.)

- [ ] **Step 4: Stop here. Do not merge or open a PR.**

Hand control back to the user for review and merge decision.

---

## Notes for the implementer

- **Do not remove `@ts-nocheck`** from any adapter file. Out of scope.
- **Do not touch the widget-proxy `preHandler`'s inline `checkAuth`.** Defense in depth.
- **Do not change the `OptioRole` enum or the `checkAuth` signature.** Out of scope; the existing model is what we're plugging holes in.
- **Do not add a CSRF layer.** The host app supplies that; out of scope.
- **Do not refactor `OptioApiOptions` to a single shared type.** The four adapters have different `TRequest` parameterizations — keep their per-adapter declarations.
- **Each adapter task's auth describe block is independent.** A failure in one does not affect another. Run only the relevant describe block during RED/GREEN cycles to keep iterations fast; full suite only at task close-out.
- **TDD discipline:** write the test, see it fail, then implement. Do not modify the production code mid-RED. Each adapter is a complete RED→GREEN cycle before moving to the next.
