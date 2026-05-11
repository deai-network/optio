import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import { createOptioRouteHandlers } from '../nextjs-app.js';
import { OptioEngineClient } from '../../_generated/optio-engine.js';

// Stub the engine RPC at the prototype level so handlers that now call
// engine.launch / engine.cancel / engine.dismiss / engine.resync don't
// try to reach a real engine over the redis-mock.
vi.spyOn(OptioEngineClient.prototype, 'resync').mockResolvedValue(undefined);

vi.spyOn(OptioEngineClient.prototype, 'launch').mockImplementation(async (params: any) => ({
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

vi.spyOn(OptioEngineClient.prototype, 'cancel').mockImplementation(async (params: any) => ({
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

vi.spyOn(OptioEngineClient.prototype, 'dismiss').mockImplementation(async (params: any) => ({
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
  mongoClient = new MongoClient(process.env.MONGO_URL ?? 'mongodb://localhost:27017');
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
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns single process', async () => {
    const doc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}`);
    const res = await GET(req);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body._id).toBe(doc._id.toString());
    expect(body.name).toBe('Test Task');
  });

  it('GET /api/processes/:id — returns 404 for nonexistent id', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const fakeId = new ObjectId().toString();

    const req = makeNextRequest(`http://localhost/api/processes/${fakeId}`);
    const res = await GET(req);

    expect(res.status).toBe(404);
  });

  it('POST /api/processes/:id/launch — launches idle process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);

    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/launch — propagates engine failure (404 reason=not-found)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    vi.spyOn(OptioEngineClient.prototype, 'launch').mockImplementationOnce(async () => ({
      ok: false,
      reason: 'not-found',
    } as any));

    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ reason: 'not-found', message: 'Process not found' });
  });

  it('POST /api/processes/:id/cancel — cancels running cancellable process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'running' }, cancellable: true });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/cancel`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/:id/dismiss — dismisses done process (200)', async () => {
    const doc = await seedProcess({ status: { state: 'done' } });
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/dismiss`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
  });

  it('POST /api/processes/resync — triggers resync (202)', async () => {
    const resyncSpy = vi.spyOn(OptioEngineClient.prototype, 'resync').mockResolvedValue(undefined);
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest('http://localhost/api/processes/resync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const res = await POST(req);

    expect(res.status).toBe(202);
    const body = await res.json();
    expect(body.message).toBe('Resync requested');
    expect(resyncSpy).toHaveBeenCalledWith({ clean: false, metadataFilter: undefined });
  });

  it('POST /api/processes/resync — forwards metadataFilter to engine.resync', async () => {
    const resyncSpy = vi.spyOn(OptioEngineClient.prototype, 'resync').mockResolvedValue(undefined);
    const { POST } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

    const req = makeNextRequest('http://localhost/api/processes/resync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ metadataFilter: { group: 'ingest' } }),
    });
    const res = await POST(req);

    expect(res.status).toBe(202);
    expect(resyncSpy).toHaveBeenCalledWith({ clean: false, metadataFilter: { group: 'ingest' } });
  });

  it('GET /api/processes/:id/tree/stream — returns event stream', async () => {
    const doc = await seedProcess();
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });

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
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const fakeId = new ObjectId().toString();

    const req = makeNextRequest(`http://localhost/api/processes/${fakeId}/tree/stream`);
    const res = await GET(req);

    expect(res.status).toBe(404);
  });
});

describe('Next.js App Router adapter auth', () => {
  function makeHandlers(authenticate: (req: any) => any) {
    return createOptioRouteHandlers({ db, redis, authenticate });
  }

  it('null role → 401 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => null);
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE list stream', async () => {
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/processes/stream');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on SSE tree stream', async () => {
    const doc = await seedProcess();
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest(
      `http://localhost/api/processes/${doc._id.toString()}/tree/stream`,
    );
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('null role → 401 on /api/optio/instances', async () => {
    const { GET } = makeHandlers(() => null);
    const req = makeNextRequest('http://localhost/api/optio/instances');
    const res = await GET(req);
    expect(res.status).toBe(401);
  });

  it('viewer → 200 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => 'viewer');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('viewer → 403 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => 'viewer');
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(403);
  });

  it('operator → 200 on REST GET', async () => {
    await seedProcess();
    const { GET } = makeHandlers(() => 'operator');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });

  it('operator → 200 on REST POST', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const { POST } = makeHandlers(() => 'operator');
    const req = makeNextRequest(`http://localhost/api/processes/${doc._id.toString()}/launch`, {
      method: 'POST',
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
  });

  it('async authenticate works', async () => {
    await seedProcess();
    const { GET } = makeHandlers(async () => 'operator');
    const req = makeNextRequest('http://localhost/api/processes?limit=10');
    const res = await GET(req);
    expect(res.status).toBe(200);
  });
});

describe('list metadataFilter (nextjs-app)', () => {
  it('REST list returns all when no filter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const res = await GET(makeNextRequest('http://localhost/api/processes?limit=10'));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items.length).toBe(2);
  });

  it('REST list returns scoped result with valid metadataFilter', async () => {
    await seedProcess({ metadata: { project: 'x' } });
    await seedProcess({ metadata: { project: 'y' } });
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const filter = encodeURIComponent(JSON.stringify({ project: 'x' }));
    const res = await GET(makeNextRequest(`http://localhost/api/processes?limit=10&metadataFilter=${filter}`));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.items.length).toBe(1);
    expect(body.items[0].metadata.project).toBe('x');
  });

  it('REST list returns 400 with explicit message for legacy metadata.* params', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const res = await GET(makeNextRequest('http://localhost/api/processes?limit=10&metadata.project=x'));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.message).toContain("Legacy 'metadata.*'");
    expect(body.message).toContain('metadata.project');
  });

  it('REST list returns 400 for malformed metadataFilter JSON', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const res = await GET(makeNextRequest('http://localhost/api/processes?limit=10&metadataFilter=not-json'));
    expect(res.status).toBe(400);
  });

  it('SSE list returns 400 for legacy metadata.* params', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const res = await GET(makeNextRequest('http://localhost/api/processes/stream?metadata.project=x'));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.message).toContain("Legacy 'metadata.*'");
  });

  it('SSE list returns 400 for malformed metadataFilter', async () => {
    const { GET } = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    const res = await GET(makeNextRequest('http://localhost/api/processes/stream?metadataFilter=not-json'));
    expect(res.status).toBe(400);
  });
});

describe('createOptioRouteHandlers return shape', () => {
  it('single-db mode returns { engine, closeAll }', async () => {
    const result = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
    expect(result.engine).toBeInstanceOf(OptioEngineClient);
    expect(typeof result.closeAll).toBe('function');
    expect((result as any).getEngine).toBeUndefined();
    await result.closeAll();
  });

  it('multi-db mode returns { getEngine, closeAll }', async () => {
    const result = createOptioRouteHandlers({ mongoClient, redis, authenticate: () => 'operator' });
    expect(result).toBeDefined();
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
    const result = createOptioRouteHandlers({ db, redis, authenticate: () => 'operator' });
    await result.closeAll!();
    await expect(result.closeAll!()).resolves.toBeUndefined();
  });
});
