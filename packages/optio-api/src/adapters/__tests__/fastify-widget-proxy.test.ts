import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createServer, type Server } from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import type { IncomingMessage } from 'http';
import IORedisMock from 'ioredis-mock';
import { registerOptioApi } from '../fastify.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_widget_proxy_adapter';
const PREFIX = 'test';

function widgetUrl(oid: ObjectId | string, subpath: string): string {
  return `/api/widget/${encodeURIComponent(DB_NAME)}/${encodeURIComponent(PREFIX)}/${oid}${subpath}`;
}

describe('registerWidgetProxy — HTTP path', () => {
  let mongoClient: MongoClient;
  let db: Db;
  let upstream: Server;
  let upstreamPort: number;
  let upstreamRequests: Array<{ url: string; method: string; headers: any; body: string }>;
  let upstreamResponder: (req: any, res: any, body: string) => void;

  beforeAll(async () => {
    mongoClient = new MongoClient(MONGO_URL);
    await mongoClient.connect();
    db = mongoClient.db(DB_NAME);
  });

  afterAll(async () => {
    await db.dropDatabase();
    await mongoClient.close();
  });

  beforeEach(async () => {
    upstreamRequests = [];
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/plain');
      res.end('hi');
    };
    upstream = createServer((req, res) => {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        upstreamRequests.push({
          url: req.url!, method: req.method!,
          headers: { ...req.headers }, body,
        });
        upstreamResponder(req, res, body);
      });
    });
    await new Promise<void>((r) => upstream.listen(0, () => r()));
    upstreamPort = (upstream.address() as any).port;
    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  afterEach(async () => {
    if (upstream.listening) {
      await new Promise<void>((r) => upstream.close(() => r()));
    }
  });

  async function makeApp(authenticate: (req: any) => any = () => 'operator'): Promise<FastifyInstance> {
    const app = Fastify();
    registerOptioApi(app, { db, redis: new IORedisMock() as any, authenticate });
    await app.ready();
    return app;
  }

  async function insertProcess(upstreamConfig?: any): Promise<ObjectId> {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: upstreamConfig ?? null,
    });
    return oid;
  }

  it('returns 401 when authenticate returns null', async () => {
    const app = await makeApp(() => null);
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 403 on POST when authenticate returns viewer', async () => {
    const app = await makeApp(() => 'viewer');
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'POST', url: widgetUrl(oid, '/foo'), payload: '' });
    expect(res.statusCode).toBe(403);
    await app.close();
  });

  it('allows viewer on GET and forwards to upstream', async () => {
    const app = await makeApp(() => 'viewer');
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect(res.statusCode).toBe(200);
    expect(upstreamRequests[0].url).toBe('/foo');
    await app.close();
  });

  it('returns 404 when process is unknown', async () => {
    const app = await makeApp();
    const unknownOid = new ObjectId();
    const res = await app.inject({ method: 'GET', url: widgetUrl(unknownOid, '/foo') });
    expect(res.statusCode).toBe(404);
    await app.close();
  });

  it('returns 404 when widgetUpstream is null', async () => {
    const app = await makeApp();
    const oid = await insertProcess(null);
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/anything') });
    expect(res.statusCode).toBe(404);
    await app.close();
  });

  it('returns 404 when URL omits database/prefix segments (old scheme)', async () => {
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    expect(res.statusCode).toBe(404);
    await app.close();
  });

  it('injects BasicAuth as Authorization header', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'basic', username: 'u', password: 'p' },
    });
    await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    const expected = 'Basic ' + Buffer.from('u:p').toString('base64');
    expect(upstreamRequests[0].headers.authorization).toBe(expected);
    await app.close();
  });

  it('injects HeaderAuth as named header', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'header', name: 'X-Opencode-Token', value: 'secret' },
    });
    await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect(upstreamRequests[0].headers['x-opencode-token']).toBe('secret');
    await app.close();
  });

  it('injects QueryAuth into URL', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'query', name: 'auth_token', value: 'secret' },
    });
    await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo?x=1') });
    const forwarded = upstreamRequests[0].url;
    expect(forwarded).toContain('auth_token=secret');
    expect(forwarded).toContain('x=1');
    await app.close();
  });

  it('passes upstream 502 when upstream is down', async () => {
    await new Promise<void>((r) => upstream.close(() => r()));
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect([502, 503]).toContain(res.statusCode);
    await app.close();
  });

  it('strips X-Frame-Options from upstream response so the iframe can embed', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html');
      res.setHeader('x-frame-options', 'DENY');
      res.end('<html></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);
    expect(res.headers['x-frame-options']).toBeUndefined();
    await app.close();
  });

  it('strips frame-ancestors from CSP but keeps other directives', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html');
      res.setHeader(
        'content-security-policy',
        "default-src 'self'; frame-ancestors 'none'; script-src 'self' 'unsafe-inline'",
      );
      res.end('<html></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);
    const csp = res.headers['content-security-policy'];
    expect(csp).toBeDefined();
    expect(String(csp).toLowerCase()).not.toContain('frame-ancestors');
    expect(String(csp)).toContain("default-src 'self'");
    expect(String(csp)).toContain("script-src 'self' 'unsafe-inline'");
    await app.close();
  });

  it('removes CSP entirely when frame-ancestors was its only directive', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html');
      res.setHeader('content-security-policy', "frame-ancestors 'none'");
      res.end('<html></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);
    expect(res.headers['content-security-policy']).toBeUndefined();
    await app.close();
  });

  async function makeAppWithLog(verbose: boolean | undefined): Promise<{ app: FastifyInstance; logs: any[] }> {
    const logs: any[] = [];
    const stream = { write(line: string) { try { logs.push(JSON.parse(line)); } catch { logs.push(line); } } };
    const app = Fastify({ logger: { level: 'info', stream } });
    registerOptioApi(app, {
      db,
      redis: new IORedisMock() as any,
      authenticate: () => 'operator',
      ...(verbose === undefined ? {} : { verbose }),
    });
    await app.ready();
    return { app, logs };
  }

  it('does not emit reply-from "fetching from remote server" at INFO by default (quiet)', async () => {
    const { app, logs } = await makeAppWithLog(undefined);
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect(res.statusCode).toBe(200);
    const messages = logs.map((l) => typeof l === 'string' ? l : l?.msg).filter(Boolean);
    expect(messages.some((m) => String(m).includes('fetching from remote server'))).toBe(false);
    expect(messages.some((m) => String(m).includes('response received'))).toBe(false);
    await app.close();
  });

  it('emits reply-from "fetching from remote server" at INFO when verbose is true', async () => {
    const { app, logs } = await makeAppWithLog(true);
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo') });
    expect(res.statusCode).toBe(200);
    const messages = logs.map((l) => typeof l === 'string' ? l : l?.msg).filter(Boolean);
    expect(messages.some((m) => String(m).includes('fetching from remote server'))).toBe(true);
    expect(messages.some((m) => String(m).includes('response received'))).toBe(true);
    await app.close();
  });
});

describe('registerWidgetProxy — WebSocket path', () => {
  let mongoClient: MongoClient;
  let db: Db;
  let upstream: Server;
  let upstreamPort: number;
  let wss: WebSocketServer;
  let upstreamHandshakes: IncomingMessage[];

  beforeAll(async () => {
    mongoClient = new MongoClient(MONGO_URL);
    await mongoClient.connect();
    db = mongoClient.db(DB_NAME);
  });

  afterAll(async () => {
    await mongoClient.close();
  });

  beforeEach(async () => {
    // Start a plain HTTP server; the WSS will attach to it
    upstream = createServer();
    await new Promise<void>((r) => upstream.listen(0, () => r()));
    upstreamPort = (upstream.address() as any).port;

    upstreamHandshakes = [];
    wss = new WebSocketServer({ server: upstream });
    wss.on('connection', (ws, req) => {
      upstreamHandshakes.push(req);
      ws.on('message', (m) => ws.send(m.toString()));
    });

    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  afterEach(async () => {
    await new Promise<void>((r) => wss.close(() => r()));
    if (upstream.listening) {
      await new Promise<void>((r) => upstream.close(() => r()));
    }
  });

  async function insertProcess(upstreamConfig?: any): Promise<ObjectId> {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: upstreamConfig ?? null,
    });
    return oid;
  }

  async function makeListening(authenticate: (req: any) => any = () => 'operator'): Promise<{ app: FastifyInstance; port: number }> {
    const app = Fastify();
    registerOptioApi(app, { db, redis: new IORedisMock() as any, authenticate });
    await app.listen({ port: 0 });
    const port = (app.server.address() as any).port;
    return { app, port };
  }

  it('WS upgrade is rejected when authenticate returns null', async () => {
    const { app, port } = await makeListening(() => null);
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });

    const ws = new WebSocket(`ws://127.0.0.1:${port}${widgetUrl(oid, '/ws')}`);
    const result = await new Promise<string>((resolve) => {
      ws.once('error', () => resolve('error'));
      ws.once('open', () => resolve('open'));
      setTimeout(() => resolve('timeout'), 2000);
    });
    ws.terminate();
    await app.close();
    expect(result).toBe('error');
  });

  it('WS upgrade is rejected when processId is unknown', async () => {
    const { app, port } = await makeListening();
    const unknownOid = new ObjectId();

    const ws = new WebSocket(`ws://127.0.0.1:${port}${widgetUrl(unknownOid, '/ws')}`);
    const result = await new Promise<string>((resolve) => {
      ws.once('error', () => resolve('error'));
      ws.once('open', () => resolve('open'));
      setTimeout(() => resolve('timeout'), 2000);
    });
    ws.terminate();
    await app.close();
    expect(result).toBe('error');
  });

  it('WS upgrade succeeds with viewer role and echoes messages', async () => {
    const { app, port } = await makeListening(() => 'viewer');
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });

    const ws = new WebSocket(`ws://127.0.0.1:${port}${widgetUrl(oid, '/ws')}`);
    await new Promise<void>((resolve, reject) => {
      ws.once('open', () => resolve());
      ws.once('error', reject);
      setTimeout(() => reject(new Error('timeout waiting for open')), 2000);
    });

    const echo = await new Promise<string>((resolve, reject) => {
      ws.once('message', (data) => resolve(data.toString()));
      ws.send('hello');
      setTimeout(() => reject(new Error('timeout waiting for echo')), 2000);
    });

    ws.close();
    await app.close();
    expect(echo).toBe('hello');
  });

  it('WS upgrade injects HeaderAuth on upstream handshake', async () => {
    const { app, port } = await makeListening();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'header', name: 'X-Opencode-Token', value: 'secret' },
    });

    const ws = new WebSocket(`ws://127.0.0.1:${port}${widgetUrl(oid, '/ws')}`);
    await new Promise<void>((resolve, reject) => {
      ws.once('open', () => resolve());
      ws.once('error', reject);
      setTimeout(() => reject(new Error('timeout waiting for open')), 2000);
    });

    ws.close();
    await app.close();

    expect(upstreamHandshakes.length).toBe(1);
    expect(upstreamHandshakes[0].headers['x-opencode-token']).toBe('secret');
  });
});
