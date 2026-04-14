import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioHandler } from '../nextjs-pages.js';

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient('mongodb://localhost:27117');
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
  const handler = createOptioHandler({ db, redis, authenticate: () => 'operator' });
  const app = express();
  app.use(express.json());
  // ts-rest's createNextRouter uses req.query['ts-rest'] as path segments for routing.
  // Express 5 query is null-prototype and its keys are not directly assignable,
  // so we replace req.query entirely on the request object.
  app.use((req, _res, next) => {
    const pathname = req.path; // e.g. /api/processes/optio
    const segments = pathname.split('/').filter(Boolean); // ['api', 'processes', 'optio']
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
  it('throws synchronously when authenticate is not provided', () => {
    expect(() => createOptioHandler({ db, redis } as any)).toThrow(
      'authenticate option is required'
    );
  });

  it('GET /api/processes/optio?limit=10 — lists processes', async () => {
    await seedProcess();
    const app = createApp();

    const res = await request(app).get('/api/processes/optio?limit=10');

    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
  });

  it('GET /api/processes/optio/:id — returns single process', async () => {
    const doc = await seedProcess();
    const app = createApp();

    const res = await request(app).get(`/api/processes/optio/${doc._id.toString()}`);

    expect(res.status).toBe(200);
    expect(res.body._id).toBe(doc._id.toString());
    expect(res.body.name).toBe('Test Task');
  });

  it('GET /api/processes/optio/:id — returns 404 for nonexistent id', async () => {
    const app = createApp();
    const fakeId = new ObjectId().toString();

    const res = await request(app).get(`/api/processes/optio/${fakeId}`);

    expect(res.status).toBe(404);
  });

  it('POST /api/processes/optio/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();

    const res = await request(app).post(`/api/processes/optio/${doc._id.toString()}/launch`);

    expect(res.status).toBe(200);
  });

  it('POST /api/processes/optio/resync — triggers resync (200, body.message = "Resync requested")', async () => {
    const app = createApp();

    const res = await request(app).post('/api/processes/optio/resync').send({});

    expect(res.status).toBe(200);
    expect(res.body.message).toBe('Resync requested');
  });
});

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
