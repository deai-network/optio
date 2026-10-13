import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { getProcessConversation } from '../handlers.js';
import {
  CONVERSATION_ARCHIVE_CACHE_TTL_MS,
  CONVERSATION_ARCHIVE_MAX_BODY_BYTES,
  fetchConversation,
  resolveConversationArchive,
} from '../conversation-archive.js';
import type { OptioContext } from '../context.js';
import type { Access, AuthorizeInput } from '../auth.js';

// GET /processes/:id/conversation: optio-api proxies the engine's conversation
// archive listener (optio-core conversation_archive.py). A local HTTP server
// stands in for the listener; its address is published in MongoDB the way the
// engine publishes it.

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_conversation_archive';

let client: MongoClient;
let db: Db;

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

// A fresh prefix per test: the endpoint cache is keyed by (client, database,
// prefix), so tests never see each other's cached endpoint.
let prefixSeq = 0;
let prefix: string;
beforeEach(() => {
  prefixSeq += 1;
  prefix = `ca${prefixSeq}`;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const ctx = () => ({ dbOpts: { db } }) as unknown as OptioContext;
const query = () => ({ prefix });

interface FakeUpstream {
  url: string;
  requests: Array<{ method?: string; url?: string; authorization?: string }>;
  reply(status: number, body: unknown): void;
  hang(): void;
  close(): Promise<void>;
}

const upstreams: FakeUpstream[] = [];
afterEach(async () => {
  await Promise.all(upstreams.splice(0).map((u) => u.close()));
});

async function startUpstream(): Promise<FakeUpstream> {
  const state = { status: 200, body: JSON.stringify({ events: [], capturedAt: null }), hang: false };
  const requests: FakeUpstream['requests'] = [];
  const sockets = new Set<Socket>();
  const server = http.createServer((req, res) => {
    requests.push({ method: req.method, url: req.url, authorization: req.headers.authorization });
    if (state.hang) return;
    res.writeHead(state.status, { 'content-type': 'application/json' });
    res.end(state.body);
  });
  server.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const upstream: FakeUpstream = {
    url: `http://127.0.0.1:${port}/`,
    requests,
    reply(status, body) {
      state.status = status;
      state.body = typeof body === 'string' ? body : JSON.stringify(body);
    },
    hang() {
      state.hang = true;
    },
    async close() {
      for (const s of sockets) s.destroy();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
  upstreams.push(upstream);
  return upstream;
}

/** An address nothing listens on (connection refused). */
async function deadUrl(): Promise<string> {
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return `http://127.0.0.1:${port}/`;
}

async function publish(url: string, password = 'pw-1') {
  await db.collection(`${prefix}_engine_endpoints`).replaceOne(
    { _id: 'conversationArchive' as any },
    { url, password, updatedAt: new Date() },
    { upsert: true },
  );
}

async function seedSuspended(extra: Record<string, unknown> = {}) {
  const _id = new ObjectId();
  const doc = {
    _id, processId: `p-${_id.toHexString()}`, name: 'Agent', rootId: _id, parentId: null,
    depth: 0, order: 0, status: { state: 'cancelled' }, supportsResume: true, hasSavedState: true,
    progress: { percent: null }, cancellable: false, log: [], metadata: {},
    ...extra,
  };
  await db.collection(`${prefix}_processes`).insertOne(doc as any);
  return doc;
}

function restricted(
  authorize: (input: Omit<AuthorizeInput, 'role'>) => boolean,
  scope: Access['scope'] = null,
): Access & { authorize: ReturnType<typeof vi.fn> } {
  return { restricted: true, scope, visibility: null, authorize: vi.fn(async (input) => authorize(input)) };
}

const RECORD = { events: [[1, { type: 'user', text: 'hi' }], [2, { type: 'assistant', text: 'yo' }]], capturedAt: '2026-10-12T10:00:00+00:00' };
const basic = (password: string) => `Basic ${Buffer.from(`optio:${password}`).toString('base64')}`;

describe('getProcessConversation', () => {
  it("gates with 'widget' before any upstream call", async () => {
    const upstream = await startUpstream();
    await publish(upstream.url);
    const doc = await seedSuspended();
    const access = restricted(() => false);

    const res = await getProcessConversation(ctx(), query(), doc._id.toHexString(), access);

    expect(res.status).toBe(403);
    expect(res.body).toEqual({ reason: 'forbidden', message: 'Forbidden' });
    expect(res.headers['Cache-Control']).toBe('no-store');
    expect(access.authorize).toHaveBeenCalledWith({
      action: 'widget',
      process: expect.objectContaining({ _id: doc._id.toHexString(), processId: doc.processId }),
    });
    expect(upstream.requests).toHaveLength(0);
  });

  it('answers 404 not-found for a process outside the scope, without an upstream call', async () => {
    const upstream = await startUpstream();
    await publish(upstream.url);
    const doc = await seedSuspended({ metadata: { customerId: 2 } });

    const res = await getProcessConversation(
      ctx(), query(), doc._id.toHexString(), restricted(() => true, { customerId: 1 }),
    );

    expect(res.status).toBe(404);
    expect(res.body).toMatchObject({ reason: 'not-found' });
    expect(upstream.requests).toHaveLength(0);
  });

  it('answers 404 not-found for an unknown id, without an upstream call', async () => {
    const upstream = await startUpstream();
    await publish(upstream.url);

    for (const id of [new ObjectId().toHexString(), 'no-such-process-id']) {
      const res = await getProcessConversation(ctx(), query(), id);
      expect(res.status).toBe(404);
      expect(res.body).toMatchObject({ reason: 'not-found' });
      expect(res.headers['Cache-Control']).toBe('no-store');
    }
    expect(upstream.requests).toHaveLength(0);
  });

  it('503 engine-unavailable when no endpoint doc', async () => {
    const doc = await seedSuspended();

    const res = await getProcessConversation(ctx(), query(), doc._id.toHexString());

    expect(res.status).toBe(503);
    expect(res.body).toMatchObject({ reason: 'engine-unavailable' });
    expect(typeof (res.body as any).message).toBe('string');
    expect(res.headers['Cache-Control']).toBe('no-store');
  });

  it('503 on connection refused; next call re-resolves', async () => {
    const doc = await seedSuspended();
    await publish(await deadUrl(), 'old-pw');

    const first = await getProcessConversation(ctx(), query(), doc._id.toHexString());
    expect(first.status).toBe(503);
    expect(first.body).toMatchObject({ reason: 'engine-unavailable' });

    // The engine restarted on a new port, well within the 30 s cache window.
    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url, 'new-pw');

    const second = await getProcessConversation(ctx(), query(), doc._id.toHexString());
    expect(second.status).toBe(200);
    expect(second.body).toEqual(RECORD);
    expect(upstream.requests[0].authorization).toBe(basic('new-pw'));
  });

  it('503 when the engine refuses the credentials (a restart on the same port); next call re-resolves', async () => {
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    upstream.reply(401, { reason: 'unauthorized', message: 'Missing or wrong credentials.' });
    await publish(upstream.url, 'stale-pw');

    const first = await getProcessConversation(ctx(), query(), doc._id.toHexString());
    expect(first.status).toBe(503);
    expect(first.body).toMatchObject({ reason: 'engine-unavailable' });

    upstream.reply(200, RECORD);
    await publish(upstream.url, 'fresh-pw');

    const second = await getProcessConversation(ctx(), query(), doc._id.toHexString());
    expect(second.status).toBe(200);
    expect(upstream.requests.map((r) => r.authorization)).toEqual([basic('stale-pw'), basic('fresh-pw')]);
  });

  it.each([
    [200, RECORD],
    [404, { reason: 'no-conversation', message: 'No saved conversation record.' }],
    [404, { reason: 'no-conversation-support', message: 'This task does not keep a readable conversation record.' }],
    [404, { reason: 'not-found', message: 'No such process.' }],
    [409, { reason: 'not-suspended', message: 'The process is not suspended.' }],
    [500, { reason: 'read-failed', message: 'Reading the conversation record failed.' }],
  ])('passes status %i and body through and sends basic auth', async (status, body) => {
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    upstream.reply(status, body);
    await publish(upstream.url, 's3cret');

    const res = await getProcessConversation(ctx(), query(), doc._id.toHexString());

    expect(res.status).toBe(status);
    expect(res.body).toEqual(body);
    expect(upstream.requests).toEqual([
      { method: 'GET', url: `/conversation/${doc._id.toHexString()}`, authorization: basic('s3cret') },
    ]);
  });

  it('sets Cache-Control: no-store', async () => {
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    await publish(upstream.url);

    upstream.reply(200, RECORD);
    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).headers)
      .toEqual({ 'Cache-Control': 'no-store' });
    upstream.reply(409, { reason: 'not-suspended', message: 'The process is not suspended.' });
    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).headers)
      .toEqual({ 'Cache-Control': 'no-store' });
  });

  it('forwards the process document _id for a processId string (the newest document wins)', async () => {
    const older = await seedSuspended({ processId: 'agent__task_1' });
    const newer = await seedSuspended({ processId: 'agent__task_1' });
    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url);

    const res = await getProcessConversation(ctx(), query(), 'agent__task_1');

    expect(res.status).toBe(200);
    expect(upstream.requests[0].url).toBe(`/conversation/${newer._id.toHexString()}`);
    expect(older._id.equals(newer._id)).toBe(false);

    // Restricted access resolves through gateProcess, to the same document.
    const gated = await getProcessConversation(ctx(), query(), 'agent__task_1', restricted(() => true));
    expect(gated.status).toBe(200);
    expect(upstream.requests[1].url).toBe(`/conversation/${newer._id.toHexString()}`);
  });

  it('answers 503 for an answer that is not the archive\'s, and re-resolves next time', async () => {
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    await publish(upstream.url);

    for (const [status, body] of [
      [502, 'Bad Gateway'],
      [404, '404: Not Found'],
      [404, { detail: 'not ours' }],
      [200, { not: 'a record' }],
    ] as const) {
      upstream.reply(status, body);
      const res = await getProcessConversation(ctx(), query(), doc._id.toHexString());
      expect(res.status).toBe(503);
      expect(res.body).toMatchObject({ reason: 'engine-unavailable' });
    }

    // The cached address was dropped: the republished credentials are used at once.
    upstream.reply(200, RECORD);
    await publish(upstream.url, 'republished-pw');
    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).status).toBe(200);
    expect(upstream.requests.at(-1)?.authorization).toBe(basic('republished-pw'));
  });

  it('caches the endpoint for 30 s', async () => {
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url);
    const t0 = Date.now();
    const now = vi.spyOn(Date, 'now').mockReturnValue(t0);

    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).status).toBe(200);

    // A changed document is not read again inside the window...
    await publish(await deadUrl());
    now.mockReturnValue(t0 + CONVERSATION_ARCHIVE_CACHE_TTL_MS - 1);
    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).status).toBe(200);

    // ...and is after it.
    now.mockReturnValue(t0 + CONVERSATION_ARCHIVE_CACHE_TTL_MS + 1);
    expect((await getProcessConversation(ctx(), query(), doc._id.toHexString())).status).toBe(503);
    expect(CONVERSATION_ARCHIVE_CACHE_TTL_MS).toBe(30_000);
  });

  it('never logs the password or the record', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const)
      .map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    const doc = await seedSuspended();
    const upstream = await startUpstream();
    await publish(upstream.url, 'very-secret-pw');

    upstream.reply(200, RECORD);
    await getProcessConversation(ctx(), query(), doc._id.toHexString());
    upstream.reply(401, { reason: 'unauthorized', message: 'x' });
    await getProcessConversation(ctx(), query(), doc._id.toHexString());
    await publish(await deadUrl(), 'very-secret-pw');
    await getProcessConversation(ctx(), query(), doc._id.toHexString());

    const logged = JSON.stringify(spies.flatMap((s) => s.mock.calls));
    expect(logged).not.toContain('very-secret-pw');
    expect(logged).not.toContain('assistant');
  });
});

describe('resolveConversationArchive', () => {
  it('reads the published endpoint, and null without one or with a malformed one', async () => {
    expect(await resolveConversationArchive(db, prefix)).toBeNull();
    await db.collection(`${prefix}_engine_endpoints`).insertOne({ _id: 'conversationArchive' as any, url: 42 });
    expect(await resolveConversationArchive(db, prefix)).toBeNull();
    await publish('http://10.0.0.5:4321/', 'pw');
    expect(await resolveConversationArchive(db, prefix)).toEqual({ url: 'http://10.0.0.5:4321/', password: 'pw' });
  });

  it('accepts only http: and https: addresses; anything else is no endpoint (503)', async () => {
    for (const url of ['file:///etc/passwd', 'ftp://10.0.0.5/', 'data:text/plain,x', 'javascript:alert(1)', 'not a url', '//10.0.0.5:4321/']) {
      prefixSeq += 1;
      prefix = `ca${prefixSeq}`;
      await publish(url, 'pw');
      expect(await resolveConversationArchive(db, prefix), url).toBeNull();
      const doc = await seedSuspended();
      const res = await getProcessConversation(ctx(), query(), doc._id.toHexString());
      expect(res.status, url).toBe(503);
      expect(res.body).toMatchObject({ reason: 'engine-unavailable' });
    }
    prefixSeq += 1;
    prefix = `ca${prefixSeq}`;
    await publish('https://archive.internal:8443/', 'pw');
    expect(await resolveConversationArchive(db, prefix)).toEqual({ url: 'https://archive.internal:8443/', password: 'pw' });
  });
});

describe('fetchConversation', () => {
  it('gives up after the timeout with 503 engine-unavailable, and re-resolves next time', async () => {
    const doc = await seedSuspended();
    const slow = await startUpstream();
    slow.hang();
    await publish(slow.url);

    const started = Date.now();
    const res = await fetchConversation(db, prefix, doc._id.toHexString(), { timeoutMs: 200 });
    expect(Date.now() - started).toBeLessThan(5000);
    expect(res.status).toBe(503);
    expect(res.body).toMatchObject({ reason: 'engine-unavailable', message: expect.stringMatching(/in time/) });

    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url);
    expect((await fetchConversation(db, prefix, doc._id.toHexString())).status).toBe(200);
  });
});

describe('fetchConversation guards', () => {
  it('rejects a processObjectId that is not 24 hex characters, without reading MongoDB or calling upstream', async () => {
    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url);
    const findOne = vi.spyOn(db.collection(`${prefix}_engine_endpoints`).constructor.prototype, 'findOne');

    for (const bad of ['', 'abc', `${'a'.repeat(24)}/../x`, '../conversation/x', 'g'.repeat(24), `${'a'.repeat(23)}%0A`, 'a'.repeat(25)]) {
      const res = await fetchConversation(db, prefix, bad);
      expect(res, bad).toEqual({ status: 404, body: { reason: 'not-found', message: 'No such process.' } });
    }
    expect(findOne).not.toHaveBeenCalled();
    expect(upstream.requests).toEqual([]);

    const id = new ObjectId().toHexString();
    expect((await fetchConversation(db, prefix, id.toUpperCase())).status).toBe(200);
    expect(upstream.requests.map((r) => r.url)).toEqual([`/conversation/${id.toUpperCase()}`]);
  });
});

describe('fetchConversation body cap', () => {
  /** A raw upstream: `handler` writes the whole response itself. */
  async function rawUpstream(handler: http.RequestListener): Promise<FakeUpstream & { requests: unknown[] }> {
    const requests: FakeUpstream['requests'] = [];
    const sockets = new Set<Socket>();
    const server = http.createServer((req, res) => {
      requests.push({ method: req.method, url: req.url, authorization: req.headers.authorization });
      handler(req, res);
    });
    server.on('connection', (s) => {
      sockets.add(s);
      s.on('close', () => sockets.delete(s));
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as AddressInfo;
    const upstream: FakeUpstream = {
      url: `http://127.0.0.1:${port}/`,
      requests,
      reply() { throw new Error('raw upstream'); },
      hang() { throw new Error('raw upstream'); },
      async close() {
        for (const s of sockets) s.destroy();
        await new Promise<void>((resolve) => server.close(() => resolve()));
      },
    };
    upstreams.push(upstream);
    return upstream;
  }

  /** A record whose JSON is exactly `size` bytes. */
  function recordOfSize(size: number): string {
    const skeleton = JSON.stringify({ events: [[1, { t: '' }]], capturedAt: null });
    const json = JSON.stringify({ events: [[1, { t: 'x'.repeat(size - skeleton.length) }]], capturedAt: null });
    expect(Buffer.byteLength(json)).toBe(size);
    return json;
  }

  it('is 64 MiB', () => {
    expect(CONVERSATION_ARCHIVE_MAX_BODY_BYTES).toBe(64 * 1024 * 1024);
  });

  it('a declared content-length over the cap is 503 without waiting for the body, and drops the cache', async () => {
    const doc = await seedSuspended();
    const big = await rawUpstream((_req, res) => {
      // Headers only: the body never comes. Only the content-length check can
      // answer before the timeout.
      res.writeHead(200, { 'content-type': 'application/json', 'content-length': '1001' });
      res.flushHeaders();
    });
    await publish(big.url, 'old-pw');

    const res = await fetchConversation(db, prefix, doc._id.toHexString(), { maxBodyBytes: 1000, timeoutMs: 60_000 });

    expect(res.status).toBe(503);
    expect(res.body).toMatchObject({ reason: 'engine-unavailable', message: expect.stringMatching(/too large/) });

    // The cached address was dropped: the republished one is used at once.
    const upstream = await startUpstream();
    upstream.reply(200, RECORD);
    await publish(upstream.url, 'new-pw');
    expect((await fetchConversation(db, prefix, doc._id.toHexString(), { maxBodyBytes: 1000 })).status).toBe(200);
    expect(upstream.requests[0].authorization).toBe(basic('new-pw'));
  });

  it('a body without content-length is counted while read: over the cap is 503 and drops the cache; at the cap passes', async () => {
    const doc = await seedSuspended();
    let size = 1001;
    const chunked = await rawUpstream((_req, res) => {
      // No content-length: chunked, two chunks.
      res.writeHead(200, { 'content-type': 'application/json' });
      const body = recordOfSize(size);
      res.write(body.slice(0, 500));
      res.end(body.slice(500));
    });
    await publish(chunked.url, 'pw-a');

    const over = await fetchConversation(db, prefix, doc._id.toHexString(), { maxBodyBytes: 1000 });
    expect(over.status).toBe(503);
    expect(over.body).toMatchObject({ reason: 'engine-unavailable', message: expect.stringMatching(/too large/) });

    // Dropped from the cache: the republished password is used at once.
    size = 1000;
    await publish(chunked.url, 'pw-b');
    const at = await fetchConversation(db, prefix, doc._id.toHexString(), { maxBodyBytes: 1000 });
    expect(at.status).toBe(200);
    expect(chunked.requests.map((r: any) => r.authorization)).toEqual([basic('pw-a'), basic('pw-b')]);
  });
});
