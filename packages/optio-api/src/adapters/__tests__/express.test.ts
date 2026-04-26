import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { registerOptioApi } from '../express.js';

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient(process.env.MONGO_URL ?? 'mongodb://localhost:27017');
  await mongoClient.connect();
  db = mongoClient.db('optio_test_express');
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
  const app = express();
  app.use(express.json());
  registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
  return app;
}

describe('Express adapter integration tests', () => {
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
    const nonexistentId = new ObjectId().toString();
    const res = await request(app).get(`/api/processes/${nonexistentId}`);
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
  });

  it('POST /api/processes/:id/cancel — cancels running cancellable process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/cancel`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const app = createApp();
    const res = await request(app).post(`/api/processes/${doc._id.toString()}/dismiss`);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/resync — triggers resync (200)', async () => {
    const app = createApp();
    const res = await request(app).post('/api/processes/resync').send({});
    expect(res.status).toBe(200);
    expect(res.body.message).toBe('Resync requested');
  });
});
