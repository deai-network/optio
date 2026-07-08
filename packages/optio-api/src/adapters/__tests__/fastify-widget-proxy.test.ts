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

  async function insertProcess(upstreamConfig?: any, widgetData?: any): Promise<ObjectId> {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: upstreamConfig ?? null,
      widgetData: widgetData ?? null,
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

  it('injects <base href> into text/html responses pointing at the widget-proxy prefix', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html>\n<html><head><title>t</title></head><body></body></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/some/deep/path') });
    expect(res.statusCode).toBe(200);
    const expectedBase = `<base href="/api/widget/${encodeURIComponent(DB_NAME)}/${encodeURIComponent(PREFIX)}/${oid}/">`;
    expect(res.body).toContain(expectedBase);
    // Injection is right after <head>.
    expect(res.body).toMatch(new RegExp(`<head[^>]*>${expectedBase.replace(/[\\^$.*+?()|[\]{}]/g, '\\$&')}`));
    // Content-length matches the transformed body length.
    const lenHeader = res.headers['content-length'];
    if (lenHeader !== undefined) {
      expect(Number(lenHeader)).toBe(Buffer.byteLength(res.body, 'utf-8'));
    }
    await app.close();
  });

  it('does NOT inject the prefix-strip script by default (ttyd contract: location.pathname must be left intact so ttyd builds token/ws under the proxy)', async () => {
    // ttyd (claudecode/grok/antigravity) derives its /token and /ws endpoints
    // from window.location.pathname. If the proxy strips the prefix to '/', ttyd
    // requests them at the origin root and escapes the proxy. So with no
    // stripProxyPrefix flag, the <base href> is injected but the replaceState
    // strip script MUST be absent.
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html>\n<html><head></head><body></body></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);
    const expectedBase = `<base href="/api/widget/${encodeURIComponent(DB_NAME)}/${encodeURIComponent(PREFIX)}/${oid}/">`;
    expect(res.body).toContain(expectedBase);       // base still injected
    expect(res.body).not.toContain('history.replaceState');  // strip shim absent
    await app.close();
  });

  it('injects the prefix-strip script only when widgetData.stripProxyPrefix is set (SPA contract: opencode/kimicode client routers)', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html>\n<html><head></head><body></body></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess(
      { url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null },
      { stripProxyPrefix: true },
    );
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo/bar') });
    expect(res.statusCode).toBe(200);
    // The injected script references the proxy prefix (without trailing slash)
    // inside a JSON-encoded string literal for a RegExp.
    const literalPrefix = `/api/widget/${encodeURIComponent(DB_NAME)}/${encodeURIComponent(PREFIX)}/${oid}`;
    expect(res.body).toContain('history.replaceState');
    expect(res.body).toContain(JSON.stringify(literalPrefix));
    // Script lives inside the <head>.
    expect(res.body).toMatch(/<head[^>]*><base[^>]*><script>[\s\S]*?history\.replaceState[\s\S]*?<\/script>/);
    await app.close();
  });

  it('strip script collapses a stray leading // so replaceState never gets a protocol-relative (cross-origin) URL', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html>\n<html><head></head><body></body></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess(
      { url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null },
      { stripProxyPrefix: true },
    );
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/foo/bar') });
    const scriptBody = res.body.match(/<script>([\s\S]*?)<\/script>/)![1];
    const prefix = `/api/widget/${encodeURIComponent(DB_NAME)}/${encodeURIComponent(PREFIX)}/${oid}`;
    const vm = require('node:vm');

    // Run the injected IIFE against a fake window for each pathname shape and
    // capture the URL handed to history.replaceState.
    const run = (pathname: string, hash = ''): string | null => {
      let captured: string | null = null;
      vm.runInNewContext(scriptBody, {
        RegExp,
        location: { pathname, search: '', hash },
        history: { replaceState: (_s: unknown, _t: string, url: string) => (captured = url) },
      });
      return captured;
    };

    // The bug: '<prefix>//sessions/<id>' would yield a leading '//sessions/...'.
    // The fix collapses it to a single leading slash (same-origin, absolute path).
    expect(run(`${prefix}//sessions/session_abc`, '#token=t')).toBe('/sessions/session_abc#token=t');
    // Ordinary single-slash subpath is untouched.
    expect(run(`${prefix}/sessions/session_abc`)).toBe('/sessions/session_abc');
    // Bare prefix (no subpath) still strips to root.
    expect(run(prefix)).toBe('/');
    await app.close();
  });

  it('appends SHA-256 hash of the inline script to script-src so CSPs that forbid inline scripts still allow it', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.setHeader(
        'content-security-policy',
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'",
      );
      res.end('<!doctype html>\n<html><head></head><body></body></html>');
    };
    const app = await makeApp();
    // CSP allowlisting only applies when the inline strip script is injected —
    // i.e. a stripProxyPrefix (SPA) widget.
    const oid = await insertProcess(
      { url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null },
      { stripProxyPrefix: true },
    );
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);

    const csp = String(res.headers['content-security-policy']);
    // Extract the base64 hash value following 'sha256-' and before the closing
    // single-quote within script-src.
    const m = csp.match(/script-src [^;]*'sha256-([A-Za-z0-9+/=]+)'/);
    expect(m).not.toBeNull();
    const cspHash = m![1];

    // Recompute the hash of the <script> body found in the response and
    // confirm it matches what got appended to the CSP.
    const scriptMatch = res.body.match(/<script>([\s\S]*?)<\/script>/);
    expect(scriptMatch).not.toBeNull();
    const actualHash = require('node:crypto').createHash('sha256').update(scriptMatch![1], 'utf-8').digest('base64');
    expect(cspHash).toBe(actualHash);

    // Original directives preserved.
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("'wasm-unsafe-eval'");
    await app.close();
  });

  it('does not touch CSP when upstream sends none', async () => {
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html>\n<html><head></head><body></body></html>');
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/') });
    expect(res.statusCode).toBe(200);
    expect(res.headers['content-security-policy']).toBeUndefined();
    await app.close();
  });

  it('does NOT inject <base href> into non-HTML responses (e.g. JS assets)', async () => {
    const jsSource = 'export const x = "/assets/foo.png"; // literal path string';
    upstreamResponder = (_req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'application/javascript');
      res.end(jsSource);
    };
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: widgetUrl(oid, '/assets/x.js') });
    expect(res.statusCode).toBe(200);
    expect(res.body).toBe(jsSource);
    expect(res.body).not.toContain('<base');
    await app.close();
  });

  it('strips Accept-Encoding from the outgoing upstream request', async () => {
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    await app.inject({
      method: 'GET',
      url: widgetUrl(oid, '/'),
      headers: { 'accept-encoding': 'gzip, deflate, br' },
    });
    expect(upstreamRequests.length).toBeGreaterThan(0);
    expect(upstreamRequests[0].headers['accept-encoding']).toBeUndefined();
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

// Generous hang-ceiling for WS events. The handshake through the fastify proxy
// to the upstream WSS can take well over 2s when the whole workspace tests in
// parallel (`pnpm -r`) and this worker's event loop is starved — the old fixed
// 2000ms budget then fired before a legitimately-slow-but-succeeding handshake
// completed ("timeout waiting for open/echo"), the flake seen only under load.
// The timeout only bounds a true hang; the real signal is the WS event, which
// is awaited directly. Every timer below is cleared on settle so it cannot
// dangle as a live handle and fire during a later test.
const WS_WAIT_MS = 15_000;

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

  async function insertProcess(upstreamConfig?: any, widgetData?: any): Promise<ObjectId> {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: upstreamConfig ?? null,
      widgetData: widgetData ?? null,
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
      const timer = setTimeout(() => resolve('timeout'), WS_WAIT_MS);
      const done = (v: string) => { clearTimeout(timer); resolve(v); };
      ws.once('error', () => done('error'));
      ws.once('open', () => done('open'));
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
      const timer = setTimeout(() => resolve('timeout'), WS_WAIT_MS);
      const done = (v: string) => { clearTimeout(timer); resolve(v); };
      ws.once('error', () => done('error'));
      ws.once('open', () => done('open'));
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
      const timer = setTimeout(() => reject(new Error('timeout waiting for open')), WS_WAIT_MS);
      ws.once('open', () => { clearTimeout(timer); resolve(); });
      ws.once('error', (e) => { clearTimeout(timer); reject(e); });
    });

    const echo = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('timeout waiting for echo')), WS_WAIT_MS);
      ws.once('message', (data) => { clearTimeout(timer); resolve(data.toString()); });
      ws.once('error', (e) => { clearTimeout(timer); reject(e); });
      ws.send('hello');
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
      const timer = setTimeout(() => reject(new Error('timeout waiting for open')), WS_WAIT_MS);
      ws.once('open', () => { clearTimeout(timer); resolve(); });
      ws.once('error', (e) => { clearTimeout(timer); reject(e); });
    });

    ws.close();
    await app.close();

    expect(upstreamHandshakes.length).toBe(1);
    expect(upstreamHandshakes[0].headers['x-opencode-token']).toBe('secret');
  });
});
