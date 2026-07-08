import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import * as http from 'node:http';
import { registerOptioApi } from '../fastify.js';

let mongoClient: MongoClient;
let db: Db;
let redis: any;

beforeAll(async () => {
  mongoClient = new MongoClient(process.env.MONGO_URL ?? 'mongodb://localhost:27017');
  await mongoClient.connect();
  db = mongoClient.db('optio_test_fastify_multi');
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
  const doc: Record<string, unknown> = {
    _id: id,
    processId: `pid-${id.toString()}`,
    name: 'Test Task',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0,
    order: 0,
    rootId: id,
    cancellable: true,
    metadata: {},
    ...overrides,
  };
  await db.collection('optio_processes').insertOne(doc as any);
  return doc;
}

/**
 * Start a fastify server on an ephemeral port. Returns the server and its base URL.
 */
async function startServer(): Promise<{ app: FastifyInstance; baseUrl: string }> {
  const app = Fastify();
  registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
  await app.listen({ port: 0, host: '127.0.0.1' });
  const addr = app.server.address() as { port: number };
  const baseUrl = `http://127.0.0.1:${addr.port}`;
  return { app, baseUrl };
}

/**
 * Collect SSE events from a URL. Aborts the request as soon as `shouldStop`
 * returns true after each event is parsed. Returns the collected events.
 */
function collectSseEvents(
  url: string,
  shouldStop: (events: unknown[]) => boolean,
  timeoutMs = 60000,
): Promise<unknown[]> {
  return new Promise((resolve, reject) => {
    const events: unknown[] = [];
    let body = '';
    const timer = setTimeout(() => {
      req.destroy(new Error(`SSE collect timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    const req = http.get(url, (res) => {
      res.on('data', (chunk: Buffer) => {
        body += chunk.toString();
        // Parse complete SSE messages (terminated by \n\n)
        const parts = body.split('\n\n');
        // Last part may be incomplete — keep it in buffer
        body = parts.pop() ?? '';
        for (const part of parts) {
          if (!part.trim()) continue;
          const dataLine = part.split('\n').find((l) => l.startsWith('data:'));
          if (!dataLine) continue;
          try {
            events.push(JSON.parse(dataLine.slice('data:'.length).trim()));
          } catch {
            // ignore parse errors
          }
        }
        if (shouldStop(events)) {
          clearTimeout(timer);
          req.destroy();
        }
      });
      res.on('end', () => {
        clearTimeout(timer);
        resolve(events);
      });
      res.on('error', (e) => {
        clearTimeout(timer);
        reject(e);
      });
    });
    req.on('error', (e) => {
      clearTimeout(timer);
      // ECONNRESET is expected when we destroy the request ourselves
      if ((e as any).code === 'ECONNRESET' || req.destroyed) {
        resolve(events);
      } else {
        reject(e);
      }
    });
    req.on('close', () => {
      clearTimeout(timer);
      resolve(events);
    });
  });
}

describe('GET /api/processes/tree/multi/stream', () => {
  it('emits resolution + update events for resolved tree + flat ids', async () => {
    await seedProcess({ processId: 'pid-tree-a' });
    await seedProcess({ processId: 'pid-flat-b' });
    const { app, baseUrl } = await startServer();
    try {
      const url = `${baseUrl}/api/processes/tree/multi/stream?treeIds=pid-tree-a&flatIds=pid-flat-b&prefix=optio&maxDepth=10`;
      // Stop once we have at least 2 events (resolution + first update)
      const events = await collectSseEvents(url, (evts) => evts.length >= 2);

      expect(events.length).toBeGreaterThanOrEqual(2);
      const firstEvent = events[0] as any;
      expect(firstEvent.type).toBe('resolution');
      expect(firstEvent.missing).toEqual([]);
      const secondEvent = events[1] as any;
      expect(secondEvent.type).toBe('update');
    } finally {
      await app.close();
    }
  });

  it('emits resolution event with missing ids when some do not resolve', async () => {
    await seedProcess({ processId: 'pid-tree-resolve' });
    const { app, baseUrl } = await startServer();
    try {
      const url = `${baseUrl}/api/processes/tree/multi/stream?treeIds=pid-tree-resolve,non-existent-pid&prefix=optio`;
      // Stop once we have the resolution event
      const events = await collectSseEvents(url, (evts) =>
        evts.some((e: any) => e.type === 'resolution'),
      );

      const resolutionEvent = events.find((e: any) => e.type === 'resolution') as any;
      expect(resolutionEvent).toBeDefined();
      expect(resolutionEvent.missing).toContain('non-existent-pid');
    } finally {
      await app.close();
    }
  });

  it('returns 400 when both treeIds and flatIds are empty', async () => {
    const app = Fastify();
    registerOptioApi(app, { db, redis, authenticate: () => 'operator' });
    const res = await app.inject({
      method: 'GET',
      url: '/api/processes/tree/multi/stream?prefix=optio',
    });
    expect(res.statusCode).toBe(400);
    await app.close();
  });
});
