import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import Fastify from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { registerOptioApi } from '../fastify.js';

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient('mongodb://localhost:27117');
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
  registerOptioApi(app, { db, redis });
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

  it('POST /api/processes/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/dismiss`,
    });

    expect(res.statusCode).toBe(200);
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
