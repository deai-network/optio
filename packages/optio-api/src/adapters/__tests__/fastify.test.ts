import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import Fastify from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { registerOptioApi } from '../fastify.js';
import { EngineClient } from '../../_generated/engine.js';

// Stub the engine RPC at the prototype level so handlers that now call
// engine.launch / engine.cancel / engine.dismiss (and later engine.resync)
// don't try to reach a real engine over the redis-mock.
vi.spyOn(EngineClient.prototype, 'launch').mockImplementation(async (params: any) => ({
  ok: true,
  process: {
    _id: new ObjectId(),
    processId: params.processId,
    name: 'Test Task',
    rootId: new ObjectId(),
    parentId: null,
    depth: 0,
    order: 0,
    status: { state: 'scheduled' },
    progress: { percent: 0, message: '' },
    cancellable: true,
    log: [],
  },
} as any));

vi.spyOn(EngineClient.prototype, 'cancel').mockImplementation(async (params: any) => ({
  ok: true,
  process: {
    _id: new ObjectId(),
    processId: params.processId,
    name: 'Test Task',
    rootId: new ObjectId(),
    parentId: null,
    depth: 0,
    order: 0,
    status: { state: 'cancelled' },
    progress: { percent: 0, message: '' },
    cancellable: true,
    log: [],
  },
} as any));

vi.spyOn(EngineClient.prototype, 'dismiss').mockImplementation(async (params: any) => ({
  ok: true,
  process: {
    _id: new ObjectId(),
    processId: params.processId,
    name: 'Test Task',
    rootId: new ObjectId(),
    parentId: null,
    depth: 0,
    order: 0,
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    cancellable: true,
    log: [],
  },
} as any));

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient(process.env.MONGO_URL ?? 'mongodb://localhost:27017');
  await mongoClient.connect();
  db = mongoClient.db('optio_test_fastify');
  redis = new Redis();
});

afterAll(async () => {
  await db.dropDatabase();
  await mongoClient.close();
});

beforeEach(async () => {
  await db.collection('optio_processes').deleteMany({});
});

async function seedProcess(overrides: Record<string, unknown> = {}) {
  const id = new ObjectId();
  const doc = {
    _id: id, processId: 'test-task', name: 'Test Task',
    status: { state: 'idle' }, progress: { percent: 0, message: '' },
    log: [], depth: 0, order: 0, rootId: id, cancellable: true, metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc);
  return doc;
}

function createApp() {
  const app = Fastify();
  registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
  return app;
}

describe('Fastify adapter integration tests', () => {
  it('GET /api/processes?limit=10 — lists processes', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/processes?limit=10',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns single process', async () => {
    const doc = await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/${doc._id.toString()}`,
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body._id).toBe(doc._id.toString());
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();

    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/${fakeId}`,
    });

    expect(res.statusCode).toBe(404);
  });

  it('POST /api/processes/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:id/launch — returns 409 for running process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });

    expect(res.statusCode).toBe(409);
    expect(JSON.parse(res.body)).toEqual({
      reason: 'not-launchable',
      message: 'Process is not in a launchable state',
    });
  });

  it('POST /api/processes/:id/launch — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${fakeId}/launch`,
    });
    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body)).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/:id/cancel — cancels running cancellable process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/cancel`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:id/cancel — returns 409 for non-cancellable process', async () => {
    const doc = await seedProcess({ status: { state: 'idle' }, cancellable: true });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/cancel`,
    });
    expect(res.statusCode).toBe(409);
    expect(JSON.parse(res.body)).toEqual({
      reason: 'not-cancellable',
      message: 'Process is not cancellable in its current state',
    });
  });

  it('POST /api/processes/:id/cancel — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${fakeId}/cancel`,
    });
    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body)).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/dismiss`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/:id/dismiss — returns 409 for non-terminal process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/dismiss`,
    });
    expect(res.statusCode).toBe(409);
    expect(JSON.parse(res.body)).toEqual({
      reason: 'not-dismissable',
      message: 'Process is not in a dismissable state',
    });
  });

  it('POST /api/processes/:id/dismiss — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${fakeId}/dismiss`,
    });
    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body)).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/resync — triggers resync (200)', async () => {
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: '/api/processes/resync',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({}),
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.message).toBe('Resync requested');
  });

  it('POST /api/processes/resync — forwards metadataFilter to Redis', async () => {
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: '/api/processes/resync',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ metadataFilter: { group: 'ingest' } }),
    });

    expect(res.statusCode).toBe(200);

    // Inspect redis mock for the published payload.
    const entries = await (redis as any).xrange(
      'optio_test_fastify/optio:commands', '-', '+',
    );
    const [, fields] = entries[entries.length - 1];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toEqual({ group: 'ingest' });
  });

  it('GET /api/processes?prefix=optio&limit=10 — lists with explicit prefix', async () => {
    await seedProcess();
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes?prefix=optio&limit=10' });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items).toHaveLength(1);
  });

  it('GET /api/optio/instances — returns empty when no collections', async () => {
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances' });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([]);
  });

  it('GET /api/optio/instances — discovers instances from collections with optio schema', async () => {
    await seedProcess();
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances' });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([{ database: 'optio_test_fastify', prefix: 'optio', live: false }]);
  });

  it('GET /api/optio/instances — reports live: true when heartbeat key exists', async () => {
    await seedProcess();
    await redis.set('optio_test_fastify/optio:heartbeat', '1', 'EX', 15);
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/instances',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([
      { database: 'optio_test_fastify', prefix: 'optio', live: true },
    ]);

    await redis.del('optio_test_fastify/optio:heartbeat');
  });

  it('GET /api/optio/instances — ignores collections without optio schema', async () => {
    await db.collection('fake_processes').insertOne({ unrelated: true });
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances' });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([]);
    await db.collection('fake_processes').drop();
  });
});

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

describe('list metadataFilter (fastify)', () => {
  it('REST list returns all when no filter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10' });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items.length).toBe(2);
  });

  it('REST list returns scoped result with valid metadataFilter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const filter = encodeURIComponent(JSON.stringify({ project: 'x' }));
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: `/api/processes?limit=10&metadataFilter=${filter}` });
    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items.length).toBe(1);
    expect(body.items[0].metadata.project).toBe('x');
  });

  it('REST list returns 400 with explicit message for legacy metadata.* params', async () => {
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10&metadata.project=x' });
    expect(res.statusCode).toBe(400);
    const body = JSON.parse(res.body);
    expect(body.message).toContain("Legacy 'metadata.*'");
    expect(body.message).toContain('metadata.project');
  });

  it('REST list returns 400 for malformed metadataFilter JSON', async () => {
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes?limit=10&metadataFilter=not-json' });
    expect(res.statusCode).toBe(400);
  });

  it('SSE list returns 400 for legacy metadata.* params', async () => {
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes/stream?metadata.project=x' });
    expect(res.statusCode).toBe(400);
    const body = JSON.parse(res.body);
    expect(body.message).toContain("Legacy 'metadata.*'");
  });

  it('SSE list returns 400 for malformed metadataFilter', async () => {
    const app = createApp();
    const res = await app.inject({ method: 'GET', url: '/api/processes/stream?metadataFilter=not-json' });
    expect(res.statusCode).toBe(400);
  });
});

describe('registerOptioApi return shape', () => {
  it('single-db mode returns { engine, closeAll }', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(result.engine).toBeInstanceOf(EngineClient);
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).getEngine).toBeUndefined();
    await app.close();
  });

  it('multi-db mode returns { getEngine, closeAll }', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { mongoClient, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(typeof result.getEngine).toBe('function');
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).engine).toBeUndefined();
    // Cache reuse:
    const a = result.getEngine!('db1', 'optio');
    const b = result.getEngine!('db1', 'optio');
    expect(a).toBe(b);
    await app.close();
  });

  it('closeAll called twice succeeds', async () => {
    const app = Fastify();
    const result = registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
    await result.closeAll!();
    await expect(result.closeAll!()).resolves.toBeUndefined();
    await app.close();
  });
});
