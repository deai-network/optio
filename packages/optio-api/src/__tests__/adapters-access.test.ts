import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import express from 'express';
import request from 'supertest';
import { createMocks } from 'node-mocks-http';
import { NextRequest } from 'next/server';
import { GridFSBucket, MongoClient, ObjectId, type Db } from 'mongodb';
import { registerOptioApi as registerExpress } from '../adapters/express.js';
import { createOptioRouteHandlers } from '../adapters/nextjs-app.js';
import { createOptioHandler } from '../adapters/nextjs-pages.js';
import type { OptioContext } from '../context.js';
import type { AuthorizeInput } from '../auth.js';
import { or, not, eq } from '../index.js';

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
  return {
    launch: vi.fn(ok), cancel: vi.fn(ok), dismiss: vi.fn(ok), resurrect: vi.fn(ok),
    resync: vi.fn(async () => undefined),
  };
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
    visibility: (req: any) => (customerOf(req) === 0 ? null : or(not(eq('kind', 'data-chat')), eq('ownerId', String(header(req, 'x-user') ?? '')))),
  };
}

function mk(metadata: Record<string, unknown>) {
  const _id = new ObjectId();
  return {
    _id, processId: `p-${_id.toHexString()}`, name: 'P', rootId: _id, parentId: null, depth: 0, order: 0,
    status: { state: 'idle' }, progress: { percent: null }, cancellable: true, log: [], metadata,
  };
}

// A data-chat hidden from every request in this file: no driver sends an
// `x-user` header, so `eq('ownerId', ...)` never matches it.
async function insertHiddenChat(): Promise<ObjectId> {
  const chat = mk({ customerId: 1, kind: 'data-chat', ownerId: 'other' });
  await col().insertOne(chat as any);
  return chat._id;
}

// Not valid UTF-8: the result route must pass the bytes through untouched.
const PDF = Buffer.from([0x25, 0x50, 0x44, 0x46, 0x2d, 0x00, 0xff, 0xfe, 0x0a, 0x80]);

/** What optio-core's store_result leaves: the GridFS file, and the record's `result`. */
async function storeResult(
  processOid: ObjectId, bytes: Buffer, filename = 'a.pdf', contentType = 'application/pdf',
): Promise<void> {
  const upload = new GridFSBucket(db).openUploadStream(filename, {
    metadata: { processId: processOid.toHexString(), prefix: PREFIX, name: filename, kind: 'result' },
  });
  await pipeline(Readable.from([bytes]), upload);
  await col().updateOne(
    { _id: processOid },
    { $set: { result: { fileId: upload.id, filename, contentType, size: bytes.length } } },
  );
}

let otherId: ObjectId;
let mineId: ObjectId;
let guardedId: ObjectId;

beforeEach(async () => {
  await col().deleteMany({});
  const other = mk({ customerId: 2 });
  const mine = mk({ customerId: 1 });
  const guarded = mk({ customerId: 1, requiredRoles: ['admin'] });
  await col().insertMany([other, mine, guarded] as any[]);
  otherId = other._id;
  mineId = mine._id;
  guardedId = guarded._id;
});

const q = `prefix=${PREFIX}`;

// A uniform driver per adapter: (method, path-with-query, body?) ->
// { status, json, bytes, header }. `bytes` is the raw body (the result
// route's file); `json` its parse when the answer is JSON.
interface Reply {
  status: number;
  json: any;
  bytes: Buffer;
  header: (name: string) => string | undefined;
}
type Call = (method: 'GET' | 'POST', url: string, body?: unknown) => Promise<Reply>;

const jsonOf = (bytes: Buffer, contentType: string | null | undefined) =>
  contentType?.includes('json') && bytes.length > 0 ? JSON.parse(bytes.toString('utf8')) : null;

// superagent buffers only text and JSON by itself; collect every body as bytes.
function collectBytes(res: any, done: (err: Error | null, body: Buffer) => void) {
  const chunks: Buffer[] = [];
  res.on('data', (chunk: Buffer) => chunks.push(chunk));
  res.on('end', () => done(null, Buffer.concat(chunks)));
}

function expressDriver(engine: any): Call {
  const app = express();
  app.use(express.json());
  registerExpress(app, { ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const pending = method === 'GET'
      ? request(app).get(url).set('x-customer', '1')
      : request(app).post(url).set('x-customer', '1').send((body ?? {}) as object);
    const r = await pending.buffer(true).parse(collectBytes);
    const bytes = r.body as Buffer;
    return {
      status: r.status, bytes, json: jsonOf(bytes, r.headers['content-type']),
      header: (name) => r.headers[name.toLowerCase()],
    };
  };
}

function appRouterDriver(engine: any): Call {
  const { GET, POST } = createOptioRouteHandlers({ ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const init: RequestInit = { method, headers: { 'x-customer': '1', 'content-type': 'application/json' } };
    if (method === 'POST') init.body = JSON.stringify(body ?? {});
    // ts-rest's Next handler reads `nextUrl`: hand it a NextRequest, as Next.js does.
    const res = await (method === 'GET' ? GET : POST)(new NextRequest(`http://localhost${url}`, init as any));
    const bytes = Buffer.from(await res.arrayBuffer());
    return {
      status: res.status, bytes, json: jsonOf(bytes, res.headers.get('content-type')),
      header: (name) => res.headers.get(name) ?? undefined,
    };
  };
}

function pagesDriver(engine: any): Call {
  const { handler } = createOptioHandler({ ctx: makeCtx(engine), ...hooks() });
  return async (method, url, body) => {
    const u = new URL(`http://localhost${url}`);
    const query: Record<string, unknown> = Object.fromEntries(u.searchParams.entries());
    query['ts-rest'] = u.pathname.split('/').slice(1);
    // A real EventEmitter: a piped body (the result file) ends on the
    // response's `finish`, which the default mock emitter never delivers.
    const { req, res } = createMocks(
      { method, url, query: query as any, headers: { 'x-customer': '1' }, body: (body ?? {}) as any },
      { eventEmitter: EventEmitter },
    );
    await handler(req as any, res as any);
    const data = res._getData();
    return {
      status: res._getStatusCode(),
      json: typeof data === 'string' ? (data ? JSON.parse(data) : null) : data,
      bytes: res._getBuffer(),
      header: (name) => res.getHeader(name) as string | undefined,
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
      expect((await call('POST', `/api/processes/${otherId}/resurrect?${q}`)).status).toBe(404);
      expect((await call('POST', `/api/processes/${guardedId}/resurrect?${q}`)).status).toBe(403);
      expect(engine.launch).not.toHaveBeenCalled();
      expect(engine.cancel).not.toHaveBeenCalled();
      expect(engine.dismiss).not.toHaveBeenCalled();
      expect(engine.resurrect).not.toHaveBeenCalled();
      expect((await call('POST', `/api/processes/${mineId}/launch?${q}`)).status).toBe(200);
      expect(engine.launch).toHaveBeenCalledTimes(1);
    });

    it('forwards resurrect with the body\'s session id', async () => {
      const engine = makeEngine();
      const call = makeDriver(engine);
      const res = await call('POST', `/api/processes/${mineId}/resurrect?${q}`, { sessionId: 's1' });
      expect(res.status).toBe(200);
      expect(engine.resurrect).toHaveBeenCalledWith({ processId: String(mineId), sessionId: 's1' });
      expect((await call('POST', `/api/processes/${mineId}/resurrect?${q}`)).status).toBe(200);
      expect(engine.resurrect).toHaveBeenLastCalledWith({ processId: String(mineId), sessionId: null });
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

    it('hides a visibility-blocked chat from the list, single reads and the tree stream, and 404s a command without reaching the engine', async () => {
      const engine = makeEngine();
      const call = makeDriver(engine);
      const hiddenChatId = await insertHiddenChat();

      const list = await call('GET', `/api/processes?${q}&limit=50`);
      expect(list.json.items.map((p: any) => p._id)).not.toContain(String(hiddenChatId));

      expect((await call('GET', `/api/processes/${hiddenChatId}?${q}`)).status).toBe(404);
      expect((await call('GET', `/api/processes/${hiddenChatId}/tree/stream?${q}`)).status).toBe(404);

      expect((await call('POST', `/api/processes/${hiddenChatId}/cancel?${q}`)).status).toBe(404);
      expect(engine.cancel).not.toHaveBeenCalled();
    });

    it('serves a result file with its own headers; 404s with the reason; 403s what authorize denies', async () => {
      const call = makeDriver(makeEngine());
      const url = (id: ObjectId) => `/api/processes/${id}/result?${q}`;

      const none = await call('GET', url(mineId));
      expect(none.status).toBe(404);
      expect(none.json).toEqual({ reason: 'no-result', message: expect.any(String) });

      await storeResult(mineId, PDF);
      const ok = await call('GET', url(mineId));
      expect(ok.status).toBe(200);
      expect(ok.bytes.equals(PDF)).toBe(true);
      expect(ok.header('content-type')).toBe('application/pdf');
      expect(ok.header('content-length')).toBe(String(PDF.length));
      expect(ok.header('content-disposition')).toBe(`inline; filename="a.pdf"; filename*=UTF-8''a.pdf`);
      expect(ok.header('x-content-type-options')).toBe('nosniff');
      expect(ok.header('content-security-policy')).toBeUndefined();

      // An active type renders sandboxed.
      await storeResult(mineId, Buffer.from('<script>1</script>'), 'a.html', 'text/html; charset=utf-8');
      const html = await call('GET', url(mineId));
      expect(html.status).toBe(200);
      expect(html.header('content-type')).toBe('text/html; charset=utf-8');
      expect(html.header('x-content-type-options')).toBe('nosniff');
      expect(html.header('content-security-policy')).toBe('sandbox');

      await storeResult(otherId, PDF);
      const other = await call('GET', url(otherId));
      expect(other.status).toBe(404);
      expect(other.json).toEqual({ reason: 'not-found', message: expect.any(String) });

      const hiddenChatId = await insertHiddenChat();
      await storeResult(hiddenChatId, PDF);
      const hidden = await call('GET', url(hiddenChatId));
      expect(hidden.status).toBe(404);
      expect(hidden.json).toEqual({ reason: 'not-found', message: expect.any(String) });

      await storeResult(guardedId, PDF);
      expect((await call('GET', url(guardedId))).status).toBe(403);
    });
  });
}

describe('visibility: an invalid predicate fails the request', () => {
  it('express: 500', async () => {
    const app = express();
    app.use(express.json());
    registerExpress(app, {
      ctx: makeCtx(makeEngine()),
      authenticate: () => 'operator' as const,
      visibility: () => ({ AND: [] }) as any,
    });
    const res = await request(app).get(`/api/processes?${q}&limit=50`).set('x-customer', '1');
    expect(res.status).toBe(500);
  });

  // nextjs-app and nextjs-pages call `accessFor` with no try/catch around it
  // already, so a hook that throws today (e.g. a bad `scope` callback)
  // rejects the handler's promise rather than answering 500. An invalid
  // predicate goes through the same unguarded `accessFor` call, so it
  // rejects the same way.
  it('nextjs-app: the handler call rejects', async () => {
    const { GET } = createOptioRouteHandlers({
      ctx: makeCtx(makeEngine()),
      authenticate: () => 'operator' as const,
      visibility: () => ({ AND: [] }) as any,
    });
    await expect(
      GET(new NextRequest(`http://localhost/api/processes?${q}&limit=50`, { headers: { 'x-customer': '1' } } as any)),
    ).rejects.toThrow();
  });

  it('nextjs-pages: the handler call rejects', async () => {
    const { handler } = createOptioHandler({
      ctx: makeCtx(makeEngine()),
      authenticate: () => 'operator' as const,
      visibility: () => ({ AND: [] }) as any,
    });
    const { req, res } = createMocks({
      method: 'GET',
      url: `/api/processes?${q}&limit=50`,
      query: { prefix: PREFIX, limit: '50', 'ts-rest': ['api', 'processes'] } as any,
      headers: { 'x-customer': '1' },
    });
    await expect(handler(req as any, res as any)).rejects.toThrow();
  });
});
