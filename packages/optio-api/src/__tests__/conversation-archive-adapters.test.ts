import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import Fastify from 'fastify';
import express from 'express';
import request from 'supertest';
import { createMocks } from 'node-mocks-http';
import { NextRequest } from 'next/server';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { registerOptioApi as registerFastify } from '../adapters/fastify.js';
import { registerOptioApi as registerExpress } from '../adapters/express.js';
import { createOptioRouteHandlers } from '../adapters/nextjs-app.js';
import { createOptioHandler } from '../adapters/nextjs-pages.js';
import type { OptioContext } from '../context.js';
import type { AuthorizeInput } from '../auth.js';

// GET /api/processes/:id/conversation is wired into all four adapters: a
// request reaches getProcessConversation with the request's access, and the
// answer carries Cache-Control: no-store.

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_conversation_archive_adapters';
const PREFIX = 'cva';
const PASSWORD = 'adapter-pw';
const RECORD = { events: [[1, { type: 'user', text: 'hi' }]], capturedAt: null };

let client: MongoClient;
let db: Db;
let server: http.Server;
const sockets = new Set<Socket>();
const upstreamRequests: Array<{ url?: string; authorization?: string }> = [];

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
  server = http.createServer((req, res) => {
    upstreamRequests.push({ url: req.url, authorization: req.headers.authorization });
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(RECORD));
  });
  server.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  await db.collection(`${PREFIX}_engine_endpoints`).replaceOne(
    { _id: 'conversationArchive' as any },
    { url: `http://127.0.0.1:${port}/`, password: PASSWORD, updatedAt: new Date() },
    { upsert: true },
  );
});

afterAll(async () => {
  for (const s of sockets) s.destroy();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await db.dropDatabase();
  await client.close();
});

let openId: ObjectId;
let guardedId: ObjectId;

beforeEach(async () => {
  upstreamRequests.length = 0;
  const col = db.collection(`${PREFIX}_processes`);
  await col.deleteMany({});
  const mk = (metadata: Record<string, unknown>) => {
    const _id = new ObjectId();
    return {
      _id, processId: `p-${_id.toHexString()}`, name: 'Agent', rootId: _id, parentId: null,
      depth: 0, order: 0, status: { state: 'cancelled' }, supportsResume: true, hasSavedState: true,
      progress: { percent: null }, cancellable: false, log: [], metadata,
    };
  };
  const open = mk({});
  const guarded = mk({ guarded: true });
  await col.insertMany([open, guarded] as any[]);
  openId = open._id;
  guardedId = guarded._id;
});

function makeCtx(): OptioContext {
  return {
    dbOpts: { db },
    redis: {},
    transports: { get: () => ({}), closeAll: async () => {} },
    closeAll: async () => {},
  } as any;
}

const hooks = {
  authenticate: () => 'viewer' as const,
  authorize: (_req: any, input: AuthorizeInput) =>
    !(input.action === 'widget' && input.process?.metadata?.guarded),
};

type Call = (url: string) => Promise<{ status: number; json: any; cacheControl: string | undefined }>;

async function fastifyDriver(): Promise<Call> {
  const app = Fastify();
  registerFastify(app, { ctx: makeCtx(), ...hooks });
  await app.ready();
  return async (url) => {
    const r = await app.inject({ method: 'GET', url });
    return { status: r.statusCode, json: r.json(), cacheControl: r.headers['cache-control'] as string | undefined };
  };
}

async function expressDriver(): Promise<Call> {
  const app = express();
  app.use(express.json());
  registerExpress(app, { ctx: makeCtx(), ...hooks });
  return async (url) => {
    const r = await request(app).get(url);
    return { status: r.status, json: r.body, cacheControl: r.headers['cache-control'] };
  };
}

async function appRouterDriver(): Promise<Call> {
  const { GET } = createOptioRouteHandlers({ ctx: makeCtx(), ...hooks });
  return async (url) => {
    // ts-rest's Next handler reads `nextUrl`: hand it a NextRequest, as Next.js does.
    const res = await GET(new NextRequest(`http://localhost${url}`, { method: 'GET' }));
    const text = await res.text();
    return { status: res.status, json: text ? JSON.parse(text) : null, cacheControl: res.headers.get('cache-control') ?? undefined };
  };
}

async function pagesDriver(): Promise<Call> {
  const { handler } = createOptioHandler({ ctx: makeCtx(), ...hooks });
  return async (url) => {
    const u = new URL(`http://localhost${url}`);
    const query: Record<string, unknown> = Object.fromEntries(u.searchParams.entries());
    query['ts-rest'] = u.pathname.split('/').slice(1);
    const { req, res } = createMocks({ method: 'GET', url, query: query as any });
    await handler(req as any, res as any);
    const data = res._getData();
    return {
      status: res._getStatusCode(),
      json: typeof data === 'string' ? (data ? JSON.parse(data) : null) : data,
      cacheControl: res.getHeader('cache-control') as string | undefined,
    };
  };
}

const drivers: Array<[string, () => Promise<Call>]> = [
  ['fastify', fastifyDriver],
  ['express', expressDriver],
  ['nextjs-app', appRouterDriver],
  ['nextjs-pages', pagesDriver],
];

for (const [name, makeDriver] of drivers) {
  describe(`${name} adapter: GET /api/processes/:id/conversation`, () => {
    it('reaches the handler: proxies the record with no-store', async () => {
      const call = await makeDriver();
      const res = await call(`/api/processes/${openId}/conversation?prefix=${PREFIX}`);
      expect(res.status).toBe(200);
      expect(res.json).toEqual(RECORD);
      expect(res.cacheControl).toBe('no-store');
      expect(upstreamRequests).toEqual([{
        url: `/conversation/${openId}`,
        authorization: `Basic ${Buffer.from(`optio:${PASSWORD}`).toString('base64')}`,
      }]);
    });

    it("passes the request's access: 403 when authorize refuses 'widget'", async () => {
      const call = await makeDriver();
      const res = await call(`/api/processes/${guardedId}/conversation?prefix=${PREFIX}`);
      expect(res.status).toBe(403);
      expect(res.json).toEqual({ reason: 'forbidden', message: 'Forbidden' });
      expect(res.cacheControl).toBe('no-store');
      expect(upstreamRequests).toHaveLength(0);
    });
  });
}
