# optio-api Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional auth callback to optio-api that integrates with host applications' existing auth systems, supporting viewer/operator role-based access control.

**Architecture:** A shared `auth.ts` module exports types (`OptioRole`, `AuthCallback`) and a `checkAuth` helper that returns `null` (authorized) or an error response object (`{ status: 401|403, body }`). Each adapter adds an optional `authenticate` callback to its `OptioApiOptions` and calls `checkAuth` before processing requests — Fastify and Express use framework middleware; Next.js adapters check at the top of their handler functions. All read endpoints (GET) require at minimum `viewer` role; all write endpoints (POST) require `operator`.

**Tech Stack:** TypeScript, vitest, Fastify, Express, Next.js (Pages + App Router), ts-rest, ioredis-mock, MongoDB, supertest

---

### File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `packages/optio-api/src/auth.ts` | Shared types (`OptioRole`, `AuthCallback`, `AuthResult`) and `checkAuth` helper |
| Create | `packages/optio-api/src/auth.test.ts` | Unit tests for `checkAuth` |
| Modify | `packages/optio-api/src/adapters/fastify.ts` | Add `authenticate?` option, add auth middleware |
| Modify | `packages/optio-api/src/adapters/express.ts` | Add `authenticate?` option, add auth middleware |
| Modify | `packages/optio-api/src/adapters/nextjs-pages.ts` | Add `authenticate?` option, add auth check at handler top |
| Modify | `packages/optio-api/src/adapters/nextjs-app.ts` | Add `authenticate?` option, add auth check in GET/POST |
| Modify | `packages/optio-api/src/adapters/__tests__/fastify.test.ts` | Auth integration tests |
| Modify | `packages/optio-api/src/adapters/__tests__/express.test.ts` | Auth integration tests |
| Modify | `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` | Auth integration tests |
| Modify | `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` | Auth integration tests |
| Modify | `packages/optio-api/src/index.ts` | Export auth types |

---

### Task 1: Shared Auth Module

**Files:**
- Create: `packages/optio-api/src/auth.ts`
- Create: `packages/optio-api/src/auth.test.ts`

- [ ] **Step 1: Write failing tests for checkAuth**

```ts
// packages/optio-api/src/auth.test.ts
import { describe, it, expect } from 'vitest';
import { checkAuth } from './auth.js';

describe('checkAuth', () => {
  it('returns null when no authenticate callback is provided', async () => {
    const result = await checkAuth({}, undefined, false);
    expect(result).toBeNull();
  });

  it('returns null when no authenticate callback is provided (write)', async () => {
    const result = await checkAuth({}, undefined, true);
    expect(result).toBeNull();
  });

  it('returns 401 when callback returns null', async () => {
    const result = await checkAuth({}, () => null, false);
    expect(result).toEqual({ status: 401, body: { message: 'Unauthorized' } });
  });

  it('returns null for viewer on read endpoint', async () => {
    const result = await checkAuth({}, () => 'viewer', false);
    expect(result).toBeNull();
  });

  it('returns 403 for viewer on write endpoint', async () => {
    const result = await checkAuth({}, () => 'viewer', true);
    expect(result).toEqual({ status: 403, body: { message: 'Forbidden' } });
  });

  it('returns null for operator on read endpoint', async () => {
    const result = await checkAuth({}, () => 'operator', false);
    expect(result).toBeNull();
  });

  it('returns null for operator on write endpoint', async () => {
    const result = await checkAuth({}, () => 'operator', true);
    expect(result).toBeNull();
  });

  it('supports async callbacks', async () => {
    const result = await checkAuth({}, async () => 'operator', true);
    expect(result).toBeNull();
  });

  it('passes the request to the callback', async () => {
    const fakeReq = { headers: { authorization: 'Bearer xyz' } };
    let receivedReq: unknown;
    await checkAuth(fakeReq, (req) => { receivedReq = req; return 'operator'; }, false);
    expect(receivedReq).toBe(fakeReq);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-api && npx vitest run src/auth.test.ts`
Expected: FAIL — `./auth.js` does not exist

- [ ] **Step 3: Implement auth module**

```ts
// packages/optio-api/src/auth.ts
export type OptioRole = 'viewer' | 'operator';

export type AuthCallback<TRequest> =
  (req: TRequest) => Promise<OptioRole | null> | OptioRole | null;

export interface AuthResult {
  status: 401 | 403;
  body: { message: string };
}

export async function checkAuth<TRequest>(
  req: TRequest,
  authenticate: AuthCallback<TRequest> | undefined,
  isWrite: boolean,
): Promise<AuthResult | null> {
  if (!authenticate) return null;
  const role = await authenticate(req);
  if (role === null) return { status: 401, body: { message: 'Unauthorized' } };
  if (isWrite && role === 'viewer') return { status: 403, body: { message: 'Forbidden' } };
  return null;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && npx vitest run src/auth.test.ts`
Expected: All 9 tests PASS

---

### Task 2: Fastify Adapter Auth

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Add auth tests to the Fastify test file**

Append the following `describe` block after the existing one in `packages/optio-api/src/adapters/__tests__/fastify.test.ts`:

```ts
describe('Fastify adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const app = Fastify();
    registerOptioApi(app, { db, redis, authenticate });
    return app;
  }

  it('no auth callback — all endpoints open', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({ method: 'GET', url: '/api/processes/optio?limit=10' });
    expect(res.statusCode).toBe(200);
  });

  it('auth returns null — 401 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);

    const res = await app.inject({ method: 'GET', url: '/api/processes/optio?limit=10' });
    expect(res.statusCode).toBe(401);
  });

  it('auth returns null — 401 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(401);
  });

  it('viewer — 200 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');

    const res = await app.inject({ method: 'GET', url: '/api/processes/optio?limit=10' });
    expect(res.statusCode).toBe(200);
  });

  it('viewer — 403 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(403);
  });

  it('operator — 200 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'operator');

    const res = await app.inject({ method: 'GET', url: '/api/processes/optio?limit=10' });
    expect(res.statusCode).toBe(200);
  });

  it('operator — 200 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/launch`,
    });
    expect(res.statusCode).toBe(200);
  });

  it('async auth callback works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'viewer');

    const res = await app.inject({ method: 'GET', url: '/api/processes/optio?limit=10' });
    expect(res.statusCode).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify auth tests fail**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: New auth tests FAIL (auth callback is ignored, all return 200)

- [ ] **Step 3: Add auth to the Fastify adapter**

Modify `packages/optio-api/src/adapters/fastify.ts`:

1. Add import at the top:
```ts
import { checkAuth, type AuthCallback } from '../auth.js';
```

2. Update the `OptioApiOptions` interface:
```ts
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<import('fastify').FastifyRequest>;
}
```

3. Add an `onRequest` hook inside `registerOptioApi`, after extracting `opts` but before registering routes:
```ts
  const { db, redis, authenticate } = opts;

  if (authenticate) {
    app.addHook('onRequest', async (request, reply) => {
      const isWrite = request.method === 'POST';
      const authError = await checkAuth(request, authenticate, isWrite);
      if (authError) {
        reply.code(authError.status).send(authError.body);
      }
    });
  }
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests PASS (existing + new auth tests)

---

### Task 3: Express Adapter Auth

**Files:**
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`

- [ ] **Step 1: Add auth tests to the Express test file**

Append the following `describe` block after the existing one in `packages/optio-api/src/adapters/__tests__/express.test.ts`:

```ts
describe('Express adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const app = express();
    app.use(express.json());
    registerOptioApi(app, { db, redis, authenticate });
    return app;
  }

  it('no auth callback — all endpoints open', async () => {
    await seedProcess();
    const app = createApp();

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });

  it('auth returns null — 401 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(401);
  });

  it('auth returns null — 401 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(401);
  });

  it('viewer — 200 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });

  it('viewer — 403 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(403);
  });

  it('operator — 200 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'operator');

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });

  it('operator — 200 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(200);
  });

  it('async auth callback works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'viewer');

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify auth tests fail**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/express.test.ts`
Expected: New auth tests FAIL (auth callback is ignored)

- [ ] **Step 3: Add auth to the Express adapter**

Modify `packages/optio-api/src/adapters/express.ts`:

1. Add import at the top:
```ts
import { checkAuth, type AuthCallback } from '../auth.js';
```

2. Update the `OptioApiOptions` interface:
```ts
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<import('express').Request>;
}
```

3. Add auth middleware inside `registerOptioApi`, after extracting `opts` but before `createExpressEndpoints`:
```ts
  const { db, redis, authenticate } = opts;

  if (authenticate) {
    app.use('/api', async (req, res, next) => {
      const isWrite = req.method === 'POST';
      const authError = await checkAuth(req, authenticate, isWrite);
      if (authError) {
        res.status(authError.status).json(authError.body);
        return;
      }
      next();
    });
  }
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/express.test.ts`
Expected: All tests PASS (existing + new auth tests)

---

### Task 4: Next.js Pages Adapter Auth

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

- [ ] **Step 1: Add auth tests to the Next.js Pages test file**

Add a `createAppWithAuth` helper and a new `describe` block after the existing one in `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`:

```ts
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

describe('Next.js Pages adapter auth', () => {
  it('no auth callback — all endpoints open', async () => {
    await seedProcess();
    const app = createApp();

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });

  it('auth returns null — 401 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => null);

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(401);
  });

  it('auth returns null — 401 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => null);

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(401);
  });

  it('viewer — 200 on read', async () => {
    await seedProcess();
    const app = createAppWithAuth(() => 'viewer');

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });

  it('viewer — 403 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'viewer');

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(403);
  });

  it('operator — 200 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createAppWithAuth(() => 'operator');

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);
    expect(res.status).toBe(200);
  });

  it('async auth callback works', async () => {
    await seedProcess();
    const app = createAppWithAuth(async () => 'viewer');

    const res = await request(app).get('/api/processes/optio?limit=10');
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify auth tests fail**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-pages.test.ts`
Expected: New auth tests FAIL (auth callback is ignored)

- [ ] **Step 3: Add auth to the Next.js Pages adapter**

Modify `packages/optio-api/src/adapters/nextjs-pages.ts`:

1. Add import at the top:
```ts
import { checkAuth, type AuthCallback } from '../auth.js';
```

2. Update the `OptioApiOptions` interface:
```ts
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<NextApiRequest>;
}
```

3. Add auth check at the top of the returned handler function, before the prefixes endpoint check:
```ts
  return async (req: NextApiRequest, res: NextApiResponse) => {
    const isWrite = req.method === 'POST';
    const authError = await checkAuth(req, authenticate, isWrite);
    if (authError) {
      res.status(authError.status).json(authError.body);
      return;
    }

    const url = req.url ?? '';
    // ... rest of handler unchanged
```

Also destructure `authenticate` from opts:
```ts
  const { db, redis, authenticate } = opts;
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-pages.test.ts`
Expected: All tests PASS (existing + new auth tests)

---

### Task 5: Next.js App Router Adapter Auth

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`

- [ ] **Step 1: Add auth tests to the Next.js App test file**

Append a new `describe` block after the existing one in `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`:

```ts
describe('Next.js App Router adapter auth', () => {
  it('no auth callback — all endpoints open', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest('http://localhost/api/processes/optio?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('auth returns null — 401 on read', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => null });

    const req = makeNextRequest('http://localhost/api/processes/optio?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('auth returns null — 401 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => null });

    const req = makeNextRequest(`http://localhost/api/processes/optio/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(401);
  });

  it('viewer — 200 on read', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'viewer' });

    const req = makeNextRequest('http://localhost/api/processes/optio?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('viewer — 403 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'viewer' });

    const req = makeNextRequest(`http://localhost/api/processes/optio/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(403);
  });

  it('operator — 200 on write', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest(`http://localhost/api/processes/optio/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
  });

  it('async auth callback works', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: async () => 'viewer' });

    const req = makeNextRequest('http://localhost/api/processes/optio?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify auth tests fail**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-app.test.ts`
Expected: New auth tests FAIL (auth callback is ignored)

- [ ] **Step 3: Add auth to the Next.js App adapter**

Modify `packages/optio-api/src/adapters/nextjs-app.ts`:

1. Add import at the top:
```ts
import { checkAuth, type AuthCallback } from '../auth.js';
```

2. Update the `OptioApiOptions` interface:
```ts
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<Request>;
}
```

3. Destructure `authenticate` from opts:
```ts
  const { db, redis, authenticate } = opts;
```

4. Add a helper inside `createOptioRouteHandlers` for building error responses:
```ts
  function authErrorResponse(authError: { status: number; body: { message: string } }): Response {
    return new Response(JSON.stringify(authError.body), {
      status: authError.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
```

5. Add auth check at the top of `GET`:
```ts
  async function GET(request: Request): Promise<Response> {
    const authError = await checkAuth(request, authenticate, false);
    if (authError) return authErrorResponse(authError);

    const url = new URL(request.url);
    // ... rest unchanged
```

6. Add auth check at the top of `POST`:
```ts
  async function POST(request: Request): Promise<Response> {
    const authError = await checkAuth(request, authenticate, true);
    if (authError) return authErrorResponse(authError);

    return tsRestHandlers(request);
  }
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/nextjs-app.test.ts`
Expected: All tests PASS (existing + new auth tests)

---

### Task 6: Export Types and Commit

**Files:**
- Modify: `packages/optio-api/src/index.ts`

- [ ] **Step 1: Add auth type exports to index.ts**

Add the following line to `packages/optio-api/src/index.ts`:

```ts
// Auth
export { type OptioRole, type AuthCallback } from './auth.js';
```

- [ ] **Step 2: Run all optio-api tests**

Run: `cd packages/optio-api && npx vitest run`
Expected: All tests PASS

- [ ] **Step 3: Type-check**

Run: `cd packages/optio-api && node_modules/.bin/tsc --noEmit`
Expected: No errors (note: files use `@ts-nocheck`, so adapter type errors are suppressed)

- [ ] **Step 4: Commit**

```bash
git add packages/optio-api/src/auth.ts packages/optio-api/src/auth.test.ts packages/optio-api/src/index.ts packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/express.ts packages/optio-api/src/adapters/nextjs-pages.ts packages/optio-api/src/adapters/nextjs-app.ts packages/optio-api/src/adapters/__tests__/fastify.test.ts packages/optio-api/src/adapters/__tests__/express.test.ts packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts
git commit -m "Add optional auth callback to optio-api adapters"
```
