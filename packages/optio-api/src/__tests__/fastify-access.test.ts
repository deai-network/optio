import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { registerOptioApi } from '../adapters/fastify.js';
import type { OptioContext } from '../context.js';
import type { AuthorizeInput } from '../auth.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_fastify_access';
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
    launch: vi.fn(ok),
    cancel: vi.fn(ok),
    dismiss: vi.fn(ok),
    resync: vi.fn(async () => undefined),
    materializeUpload: vi.fn(async () => ({ ok: true, path: '/x' })),
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

// The caller's customer comes from a header; customer 0 users are admins.
function customerOf(req: any): number {
  return Number(req.headers['x-customer'] ?? '-1');
}

function policy(input: AuthorizeInput, customer: number): boolean {
  const admin = customer === 0;
  if (input.action === 'instances') return admin;
  const required = input.process?.metadata?.requiredRoles as string[] | undefined;
  if (required && !admin) return false;
  return true;
}

async function buildApp(engine: any, withHooks = true): Promise<FastifyInstance> {
  const app = Fastify();
  registerOptioApi(app, {
    ctx: makeCtx(engine),
    authenticate: () => 'operator',
    ...(withHooks
      ? {
          scope: (req: any) => (customerOf(req) === 0 ? null : { customerId: customerOf(req) }),
          authorize: (req: any, input: AuthorizeInput) => policy(input, customerOf(req)),
        }
      : {}),
  });
  await app.ready();
  return app;
}

let otherId: ObjectId;
let mineId: ObjectId;
let guardedId: ObjectId;

beforeEach(async () => {
  await col().deleteMany({});
  const mk = (customerId: number, extra: Record<string, unknown> = {}) => {
    const _id = new ObjectId();
    return {
      _id, processId: `p-${_id.toHexString()}`, name: 'P', rootId: _id, parentId: null, depth: 0, order: 0,
      status: { state: 'running' }, progress: { percent: null }, cancellable: true, log: [],
      metadata: { customerId, dataspace: customerId === 1 ? 'mine' : 'theirs' },
      widgetUpstream: { url: 'http://127.0.0.1:9', innerAuth: null },
      controlUpstream: { url: 'http://127.0.0.1:9', innerAuth: null },
      ...extra,
    };
  };
  const other = mk(2);
  const mine = mk(1);
  const guarded = mk(1, { metadata: { customerId: 1, requiredRoles: ['admin'] } });
  await col().insertMany([other, mine, guarded] as any[]);
  otherId = other._id;
  mineId = mine._id;
  guardedId = guarded._id;
});

const q = `prefix=${PREFIX}`;
const as1 = { 'x-customer': '1' };

describe('fastify adapter with scope + authorize', () => {
  it('lists only the caller\'s processes', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(res.statusCode).toBe(200);
    expect(res.json().items.map((p: any) => p._id).sort()).toEqual([mineId, guardedId].map(String).sort());
    await app.close();
  });

  it('404s single-process reads and the tree stream for other customers\' processes', async () => {
    const app = await buildApp(makeEngine());
    for (const path of ['', '/tree', '/log', '/tree/log', '/tree/stream']) {
      const res = await app.inject({ method: 'GET', url: `/api/processes/${otherId}${path}?${q}`, headers: as1 });
      expect(res.statusCode, path).toBe(404);
    }
    const res = await app.inject({ method: 'GET', url: `/api/processes/${mineId}?${q}`, headers: as1 });
    expect(res.statusCode).toBe(200);
    await app.close();
  });

  it('404s commands on other customers\' processes and 403s denied ones, without reaching the engine', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    for (const cmd of ['launch', 'cancel', 'dismiss']) {
      const out = await app.inject({ method: 'POST', url: `/api/processes/${otherId}/${cmd}?${q}`, headers: as1, payload: {} });
      expect(out.statusCode, cmd).toBe(404);
      const denied = await app.inject({ method: 'POST', url: `/api/processes/${guardedId}/${cmd}?${q}`, headers: as1, payload: {} });
      expect(denied.statusCode, cmd).toBe(403);
    }
    expect(engine.launch).not.toHaveBeenCalled();
    expect(engine.cancel).not.toHaveBeenCalled();
    expect(engine.dismiss).not.toHaveBeenCalled();
    const ok = await app.inject({ method: 'POST', url: `/api/processes/${mineId}/launch?${q}`, headers: as1, payload: {} });
    expect(ok.statusCode).toBe(200);
    expect(engine.launch).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('narrows resync to the caller\'s scope and refuses contradictions', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const res = await app.inject({
      method: 'POST', url: `/api/processes/resync?${q}`, headers: as1,
      payload: { metadataFilter: { dataspace: 'theirs' } },
    });
    expect(res.statusCode).toBe(202);
    expect(engine.resync).toHaveBeenCalledWith({ clean: false, metadataFilter: { dataspace: 'theirs', customerId: 1 } });
    const bad = await app.inject({
      method: 'POST', url: `/api/processes/resync?${q}`, headers: as1,
      payload: { metadataFilter: { customerId: 2 } },
    });
    expect(bad.statusCode).toBe(403);
    expect(engine.resync).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('403s instance discovery when authorize denies it', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances', headers: as1 });
    expect(res.statusCode).toBe(403);
    await app.close();
  });

  it('404s the widget proxy, widget-control and widget-upload for other customers\' processes', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const widget = await app.inject({ method: 'GET', url: `/api/widget/${DB_NAME}/${PREFIX}/${otherId}/`, headers: as1 });
    expect(widget.statusCode).toBe(404);
    const control = await app.inject({
      method: 'POST', url: `/api/widget-control/${DB_NAME}/${PREFIX}/${otherId}`, headers: as1,
      payload: { text: 'rm -rf /' },
    });
    expect(control.statusCode).toBe(404);
    const boundary = 'b0undary';
    const upload = await app.inject({
      method: 'POST', url: `/api/widget-upload/${DB_NAME}/${PREFIX}/${otherId}`,
      headers: { ...as1, 'content-type': `multipart/form-data; boundary=${boundary}` },
      payload: `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\nhi\r\n--${boundary}--\r\n`,
    });
    expect(upload.statusCode).toBe(404);
    expect(engine.materializeUpload).not.toHaveBeenCalled();
    await app.close();
  });

  it('403s widget routes that authorize denies', async () => {
    const app = await buildApp(makeEngine());
    const widget = await app.inject({ method: 'GET', url: `/api/widget/${DB_NAME}/${PREFIX}/${guardedId}/`, headers: as1 });
    expect(widget.statusCode).toBe(403);
    const control = await app.inject({
      method: 'POST', url: `/api/widget-control/${DB_NAME}/${PREFIX}/${guardedId}`, headers: as1,
      payload: { text: 'x' },
    });
    expect(control.statusCode).toBe(403);
    await app.close();
  });

  it('leaves an unrestricted caller (null scope) unrestricted', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: { 'x-customer': '0' } });
    expect(res.json().totalCount).toBe(3);
    await app.close();
  });
});

describe('fastify adapter without hooks', () => {
  it('behaves as before: every process visible, commands go to the engine', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine, false);
    const list = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(list.json().totalCount).toBe(3);
    const launch = await app.inject({ method: 'POST', url: `/api/processes/${otherId}/launch?${q}`, headers: as1, payload: {} });
    expect(launch.statusCode).toBe(200);
    expect(engine.launch).toHaveBeenCalledTimes(1);
    await app.close();
  });
});
