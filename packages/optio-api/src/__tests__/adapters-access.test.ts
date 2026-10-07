import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import express from 'express';
import request from 'supertest';
import { createMocks } from 'node-mocks-http';
import { NextRequest } from 'next/server';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { registerOptioApi as registerExpress } from '../adapters/express.js';
import { createOptioRouteHandlers } from '../adapters/nextjs-app.js';
import { createOptioHandler } from '../adapters/nextjs-pages.js';
import type { OptioContext } from '../context.js';
import type { AuthorizeInput } from '../auth.js';

// The same scope/authorize scenario as fastify-access.test.ts, run through
// the Express, Next.js App Router and Next.js Pages Router adapters.

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_adapters_access';
const PREFIX = 'test';

let client: MongoClient;
let db: Db;
const col = () => db.collection(`${PREFIX}_processes`);

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

function makeEngine() {
  const ok = (params: any) => ({
    ok: true,
    process: { _id: new ObjectId(), processId: params.processId, rootId: new ObjectId(), name: 'P' },
  });
  return { launch: vi.fn(ok), cancel: vi.fn(ok), dismiss: vi.fn(ok), resync: vi.fn(async () => undefined) };
}

function makeCtx(engine: any): OptioContext {
  const fakeRpc: any = {
    async call(_service: string, method: string, params: any) { return engine[method](params); },
    async notify(_service: string, method: string, params: any) { return engine[method](params); },
  };
  return {
    dbOpts: { db },
    redis: {},
    transports: { get: () => fakeRpc, closeAll: async () => {} },
    closeAll: async () => {},
  } as any;
}

function header(req: any, name: string): string | undefined {
  if (typeof req.headers?.get === 'function') return req.headers.get(name) ?? undefined;
  return req.headers?.[name];
}

function hooks() {
  const customerOf = (req: any) => Number(header(req, 'x-customer') ?? '-1');
  return {
    authenticate: () => 'operator' as const,
    scope: (req: any) => (customerOf(req) === 0 ? null : { customerId: customerOf(req) }),
    authorize: (req: any, input: AuthorizeInput) => {
      const admin = customerOf(req) === 0;
      if (input.action === 'instances') return admin;
      if (input.process?.metadata?.requiredRoles && !admin) return false;
      return true;
    },
  };
}

let otherId: ObjectId;
let mineId: ObjectId;
let guardedId: ObjectId;

beforeEach(async () => {
  await col().deleteMany({});
  const mk = (metadata: Record<string, unknown>) => {
    const _id = new ObjectId();
    return {
      _id, processId: `p-${_id.toHexString()}`, name: 'P', rootId: _id, parentId: null, depth: 0, order: 0,
      status: { state: 'idle' }, progress: { percent: null }, cancellable: true, log: [], metadata,
    };
  };
  const other = mk({ customerId: 2 });
  const mine = mk({ customerId: 1 });
  const guarded = mk({ customerId: 1, requiredRoles: ['admin'] });
  await col().insertMany([other, mine, guarded] as any[]);
  otherId = other._id;
  mineId = mine._id;
  guardedId = guarded._id;
});

const q = `prefix=${PREFIX}`;

// A uniform driver per adapter: (method, path-with-query, body?) -> { status, json }.
type Call = (method: 'GET' | 'POST', url: string, body?: unknown) => Promise<{ status: number; json: any }>;

function expressDriver(engine: any): Call {
  const app = express();
  app.use(express.json());
  registerExpress(app, { ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const r = method === 'GET'
      ? await request(app).get(url).set('x-customer', '1')
      : await request(app).post(url).set('x-customer', '1').send((body ?? {}) as object);
    return { status: r.status, json: r.body };
  };
}

function appRouterDriver(engine: any): Call {
  const { GET, POST } = createOptioRouteHandlers({ ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const init: RequestInit = { method, headers: { 'x-customer': '1', 'content-type': 'application/json' } };
    if (method === 'POST') init.body = JSON.stringify(body ?? {});
    // ts-rest's Next handler reads `nextUrl`: hand it a NextRequest, as Next.js does.
    const res = await (method === 'GET' ? GET : POST)(new NextRequest(`http://localhost${url}`, init as any));
    const text = await res.text();
    return { status: res.status, json: text ? JSON.parse(text) : null };
  };
}

function pagesDriver(engine: any): Call {
  const { handler } = createOptioHandler({ ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const u = new URL(`http://localhost${url}`);
    const query: Record<string, unknown> = Object.fromEntries(u.searchParams.entries());
    query['ts-rest'] = u.pathname.split('/').slice(1);
    const { req, res } = createMocks({
      method, url, query: query as any, headers: { 'x-customer': '1' }, body: (body ?? {}) as any,
    });
    await handler(req as any, res as any);
    const data = res._getData();
    return {
      status: res._getStatusCode(),
      json: typeof data === 'string' ? (data ? JSON.parse(data) : null) : data,
    };
  };
}

const drivers: Array<[string, (engine: any) => Call]> = [
  ['express', expressDriver],
  ['nextjs-app', appRouterDriver],
  ['nextjs-pages', pagesDriver],
];

for (const [name, makeDriver] of drivers) {
  describe(`${name} adapter with scope + authorize`, () => {
    it('lists only the caller\'s processes', async () => {
      const call = makeDriver(makeEngine());
      const res = await call('GET', `/api/processes?${q}&limit=50`);
      expect(res.status).toBe(200);
      expect(res.json.items.map((p: any) => p._id).sort()).toEqual([mineId, guardedId].map(String).sort());
    });

    it('404s reads and the tree stream of other customers\' processes', async () => {
      const call = makeDriver(makeEngine());
      expect((await call('GET', `/api/processes/${otherId}?${q}`)).status).toBe(404);
      expect((await call('GET', `/api/processes/${otherId}/tree/stream?${q}`)).status).toBe(404);
      expect((await call('GET', `/api/processes/${mineId}?${q}`)).status).toBe(200);
    });

    it('404s and 403s commands without reaching the engine', async () => {
      const engine = makeEngine();
      const call = makeDriver(engine);
      expect((await call('POST', `/api/processes/${otherId}/launch?${q}`)).status).toBe(404);
      expect((await call('POST', `/api/processes/${guardedId}/cancel?${q}`)).status).toBe(403);
      expect((await call('POST', `/api/processes/${otherId}/dismiss?${q}`)).status).toBe(404);
      expect(engine.launch).not.toHaveBeenCalled();
      expect(engine.cancel).not.toHaveBeenCalled();
      expect(engine.dismiss).not.toHaveBeenCalled();
      expect((await call('POST', `/api/processes/${mineId}/launch?${q}`)).status).toBe(200);
      expect(engine.launch).toHaveBeenCalledTimes(1);
    });

    it('narrows resync to the caller\'s scope', async () => {
      const engine = makeEngine();
      const call = makeDriver(engine);
      const res = await call('POST', `/api/processes/resync?${q}`, { metadataFilter: { dataspace: 'x' } });
      expect(res.status).toBe(202);
      expect(engine.resync).toHaveBeenCalledWith({ clean: false, metadataFilter: { dataspace: 'x', customerId: 1 } });
    });

    it('403s instance discovery', async () => {
      const call = makeDriver(makeEngine());
      expect((await call('GET', '/api/optio/instances')).status).toBe(403);
    });
  });
}
