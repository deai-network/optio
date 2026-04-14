import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioRouteHandlers } from '../nextjs-app.js';

/**
 * createNextHandler from @ts-rest/serverless expects a NextRequest with a
 * `nextUrl` property (a URL object). In tests we use plain Web API Requests,
 * so we attach `nextUrl` ourselves to satisfy the adapter.
 */
function makeNextRequest(url: string, init?: RequestInit): Request & { nextUrl: URL } {
  const req = new Request(url, init) as Request & { nextUrl: URL };
  req.nextUrl = new URL(url);
  return req;
}

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient('mongodb://localhost:27117');
  await mongoClient.connect();
  db = mongoClient.db('optio_test_nextjs_app');
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

describe('Next.js App Router adapter integration tests', () => {
  it('GET /api/processes?limit=10 — lists processes', async () => {
    await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns single process', async () => {
    const doc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}`);
    const res = await GET(req);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body._id).toBe(doc._id.toString());
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns 404 for nonexistent id', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis });
    const fakeId = new ObjectId().toString();

    const req = makeNextRequest(`http://localhost/api/processes/${fakeId}`);
    const res = await GET(req);

    expect(res.status).toBe(404);
  });

  it('POST /api/processes/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);

    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/launch — returns 409 for running process', async () => {
    const doc = await seedProcess({ status: { state: 'running' } });
    const { POST } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);

    expect(res.status).toBe(409);
  });

  it('POST /api/processes/resync — triggers resync (200)', async () => {
    const { POST } = createOptioRouteHandlers({ db, redis });

    const req = makeNextRequest('http://localhost/api/processes/resync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const res = await POST(req);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.message).toBe('Resync requested');
  });

  it('GET /api/processes/:id/tree/stream — returns event stream', async () => {
    const doc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis });

    const controller = new AbortController();
    const req = makeNextRequest(
      `http://localhost/api/processes/${doc._id.toString()}/tree/stream`,
      { signal: controller.signal },
    );

    const res = await GET(req);

    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Type')).toBe('text/event-stream');

    // Abort immediately to clean up the stream
    controller.abort();
  });

  it('GET /api/processes/:id/tree/stream — returns 404 for nonexistent id', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis });
    const fakeId = new ObjectId().toString();

    const req = makeNextRequest(`http://localhost/api/processes/${fakeId}/tree/stream`);
    const res = await GET(req);

    expect(res.status).toBe(404);
  });
});
