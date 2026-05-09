import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import express from 'express';
import request from 'supertest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioHandler } from '../nextjs-pages.js';
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
  db = mongoClient.db('optio_test_nextjs_pages');
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
  const { handler } = createOptioHandler({ db, redis, authenticate: () => 'operator' });
  const app = express();
  app.use(express.json());
  // ts-rest's createNextRouter uses req.query['ts-rest'] as path segments for routing.
  // Express 5 query is null-prototype and its keys are not directly assignable,
  // so we replace req.query entirely on the request object.
  app.use((req, _res, next) => {
    const pathname = req.path; // e.g. /api/processes
    const segments = pathname.split('/').filter(Boolean); // ['api', 'processes']
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

describe('Next.js Pages Router adapter integration tests', () => {
  it('GET /api/processes?limit=10 — lists processes', async () => {
    await seedProcess();
    const app = createApp();

    const res = await request(app).get('/api/processes?limit=10');

    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
  });

  it('GET /api/processes/:id — returns single process', async () => {
    const doc = await seedProcess();
    const app = createApp();

    const res = await request(app).get(`/api/processes/${doc._id.toString()}`);

    expect(res.status).toBe(200);
    expect(res.body._id).toBe(doc._id.toString());
    expect(res.body.name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();

    const res = await request(app).get(`/api/processes/${fakeId}`);

    expect(res.status).toBe(404);
  });

  it('POST /api/processes/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();

    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);

    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/launch — returns 409 for running process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/launch`);
    expect(res.status).toBe(409);
    expect(res.body).toEqual({
      reason: 'not-launchable',
      message: 'Process is not in a launchable state',
    });
  });

  it('POST /api/processes/:id/launch — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await request(app).post(`/api/processes/${fakeId}/launch`);
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/:id/cancel — cancels running cancellable process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/cancel`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/cancel — returns 409 for non-cancellable process', async () => {
    const doc = await seedProcess({ status: { state: 'idle' }, cancellable: true });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/cancel`);
    expect(res.status).toBe(409);
    expect(res.body).toEqual({
      reason: 'not-cancellable',
      message: 'Process is not cancellable in its current state',
    });
  });

  it('POST /api/processes/:id/cancel — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await request(app).post(`/api/processes/${fakeId}/cancel`);
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/dismiss`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/dismiss — returns 409 for non-terminal process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/dismiss`);
    expect(res.status).toBe(409);
    expect(res.body).toEqual({
      reason: 'not-dismissable',
      message: 'Process is not in a dismissable state',
    });
  });

  it('POST /api/processes/:id/dismiss — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();
    const res = await request(app).post(`/api/processes/${fakeId}/dismiss`);
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/resync — triggers resync (200, body.message = "Resync requested")', async () => {
    const app = createApp();

    const res = await request(app).post('/api/processes/resync').send({});

    expect(res.status).toBe(200);
    expect(res.body.message).toBe('Resync requested');
  });

  it('POST /api/processes/resync — forwards metadataFilter to Redis', async () => {
    const app = createApp();

    const res = await request(app)
      .post('/api/processes/resync')
      .send({ metadataFilter: { group: 'ingest' } });

    expect(res.status).toBe(200);

    // Inspect redis mock for the published payload.
    const entries = await (redis as any).xrange(
      'optio_test_nextjs_pages/optio:commands', '-', '+',
    );
    const [, fields] = entries[entries.length - 1];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toEqual({ group: 'ingest' });
  });
});

describe('Next.js Pages Router adapter auth', () => {
  function createAppWithAuth(authenticate: (req: any) => any) {
    const { handler } = createOptioHandler({ db, redis, authenticate });
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

describe('list metadataFilter (nextjs-pages)', () => {
  it('REST list returns all when no filter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const app = createApp();
    const res = await request(app).get('/api/processes?limit=10');
    expect(res.status).toBe(200);
    expect(res.body.items.length).toBe(2);
  });

  it('REST list returns scoped result with valid metadataFilter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const filter = encodeURIComponent(JSON.stringify({ project: 'x' }));
    const app = createApp();
    const res = await request(app).get(`/api/processes?limit=10&metadataFilter=${filter}`);
    expect(res.status).toBe(200);
    expect(res.body.items.length).toBe(1);
    expect(res.body.items[0].metadata.project).toBe('x');
  });

  it('REST list returns 400 with explicit message for legacy metadata.* params', async () => {
    const app = createApp();
    const res = await request(app).get('/api/processes?limit=10&metadata.project=x');
    expect(res.status).toBe(400);
    expect(res.body.message).toContain("Legacy 'metadata.*'");
    expect(res.body.message).toContain('metadata.project');
  });

  it('REST list returns 400 for malformed metadataFilter JSON', async () => {
    const app = createApp();
    const res = await request(app).get('/api/processes?limit=10&metadataFilter=not-json');
    expect(res.status).toBe(400);
  });

  it('SSE list returns 400 for legacy metadata.* params', async () => {
    const app = createApp();
    const res = await request(app).get('/api/processes/stream?metadata.project=x');
    expect(res.status).toBe(400);
    expect(res.body.message).toContain("Legacy 'metadata.*'");
  });

  it('SSE list returns 400 for malformed metadataFilter', async () => {
    const app = createApp();
    const res = await request(app).get('/api/processes/stream?metadataFilter=not-json');
    expect(res.status).toBe(400);
  });
});

describe('createOptioHandler return shape', () => {
  it('single-db mode returns { handler, engine, closeAll }', async () => {
    const result = createOptioHandler({ db, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(typeof result.handler).toBe('function');
    expect(result.engine).toBeInstanceOf(EngineClient);
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).getEngine).toBeUndefined();
    await result.closeAll();
  });

  it('multi-db mode returns { handler, getEngine, closeAll }', async () => {
    const result = createOptioHandler({ mongoClient, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(typeof result.handler).toBe('function');
    expect(typeof result.getEngine).toBe('function');
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).engine).toBeUndefined();
    // Cache reuse:
    const a = result.getEngine!('db1', 'optio');
    const b = result.getEngine!('db1', 'optio');
    expect(a).toBe(b);
    await result.closeAll();
  });

  it('closeAll called twice succeeds', async () => {
    const result = createOptioHandler({ db, redis, authenticate: () => 'operator' });
    await result.closeAll!();
    await expect(result.closeAll!()).resolves.toBeUndefined();
  });
});
