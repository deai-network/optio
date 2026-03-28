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
  it('GET /api/processes/optio?limit=10 — lists processes', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/processes/optio?limit=10',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/optio/:id — returns single process', async () => {
    const doc = await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/optio/${doc._id.toString()}`,
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body._id).toBe(doc._id.toString());
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/optio/:id — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();

    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/optio/${fakeId}`,
    });

    expect(res.statusCode).toBe(404);
  });

  it('POST /api/processes/optio/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/launch`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/optio/:id/launch — returns 409 for running process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/launch`,
    });

    expect(res.statusCode).toBe(409);
  });

  it('POST /api/processes/optio/:id/cancel — cancels running cancellable process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/cancel`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/optio/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/optio/${doc._id.toString()}/dismiss`,
    });

    expect(res.statusCode).toBe(200);
  });

  it('POST /api/processes/optio/resync — triggers resync (200)', async () => {
    const app = createApp();

    const res = await app.inject({
      method: 'POST',
      url: '/api/processes/optio/resync',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({}),
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.message).toBe('Resync requested');
  });
});
