import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import Fastify, { type FastifyInstance } from 'fastify';
import { GridFSBucket, MongoClient, ObjectId, type Db } from 'mongodb';
import { registerOptioApi } from '../adapters/fastify.js';
import type { OptioContext } from '../context.js';
import type { AuthorizeInput } from '../auth.js';
import { or, not, eq } from '../index.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_fastify_access';
const PREFIX = 'test';

let client: MongoClient;
let db: Db;
const col = () => db.collection(`${PREFIX}_processes`);

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

function makeEngine() {
  const ok = (params: any) => ({
    ok: true,
    process: { _id: new ObjectId(), processId: params.processId, rootId: new ObjectId(), name: 'P' },
  });
  return {
    launch: vi.fn(ok),
    cancel: vi.fn(ok),
    dismiss: vi.fn(ok),
    resurrect: vi.fn(ok),
    resync: vi.fn(async () => undefined),
    materializeUpload: vi.fn(async () => ({ ok: true, path: '/x' })),
  };
}

function makeCtx(engine: any): OptioContext {
  const fakeRpc: any = {
    async call(_service: string, method: string, params: any) { return engine[method](params); },
    async notify(_service: string, method: string, params: any) { return engine[method](params); },
  };
  return {
    dbOpts: { db },
    redis: {},
    transports: { get: () => fakeRpc, closeAll: async () => {} },
    closeAll: async () => {},
  } as any;
}

// The caller's customer comes from a header; customer 0 users are admins.
function customerOf(req: any): number {
  return Number(req.headers['x-customer'] ?? '-1');
}

function header(req: any, name: string): string | undefined {
  return req.headers[name];
}

function policy(input: AuthorizeInput, customer: number): boolean {
  const admin = customer === 0;
  if (input.action === 'instances') return admin;
  const required = input.process?.metadata?.requiredRoles as string[] | undefined;
  if (required && !admin) return false;
  return true;
}

async function buildApp(engine: any, withHooks = true): Promise<FastifyInstance> {
  const app = Fastify();
  registerOptioApi(app, {
    ctx: makeCtx(engine),
    authenticate: () => 'operator',
    ...(withHooks
      ? {
          scope: (req: any) => (customerOf(req) === 0 ? null : { customerId: customerOf(req) }),
          authorize: (req: any, input: AuthorizeInput) => policy(input, customerOf(req)),
          visibility: (req: any) => (customerOf(req) === 0 ? null : or(not(eq('kind', 'data-chat')), eq('ownerId', String(header(req, 'x-user') ?? '')))),
        }
      : {}),
  });
  await app.ready();
  return app;
}

function mk(customerId: number, extra: Record<string, unknown> = {}) {
  const _id = new ObjectId();
  return {
    _id, processId: `p-${_id.toHexString()}`, name: 'P', rootId: _id, parentId: null, depth: 0, order: 0,
    status: { state: 'running' }, progress: { percent: null }, cancellable: true, log: [],
    metadata: { customerId, dataspace: customerId === 1 ? 'mine' : 'theirs' },
    widgetUpstream: { url: 'http://127.0.0.1:9', innerAuth: null },
    controlUpstream: { url: 'http://127.0.0.1:9', innerAuth: null },
    ...extra,
  };
}

// A data-chat hidden from every request in this file: no request sends
// `x-user: other`, so `eq('ownerId', ...)` never matches it.
async function insertHiddenChat(): Promise<ObjectId> {
  const chat = mk(1, { metadata: { customerId: 1, kind: 'data-chat', ownerId: 'other' } });
  await col().insertOne(chat as any);
  return chat._id;
}

// Not valid UTF-8: the result route must pass the bytes through untouched.
const PDF = Buffer.from([0x25, 0x50, 0x44, 0x46, 0x2d, 0x00, 0xff, 0xfe, 0x0a, 0x80]);

/** What optio-core's store_result leaves: the GridFS file, and the record's `result`. */
async function storeResult(
  processOid: ObjectId, bytes: Buffer, filename = 'a.pdf', contentType = 'application/pdf',
): Promise<void> {
  const upload = new GridFSBucket(db).openUploadStream(filename, {
    metadata: { processId: processOid.toHexString(), prefix: PREFIX, name: filename, kind: 'result' },
  });
  await pipeline(Readable.from([bytes]), upload);
  await col().updateOne(
    { _id: processOid },
    { $set: { result: { fileId: upload.id, filename, contentType, size: bytes.length } } },
  );
}

let otherId: ObjectId;
let mineId: ObjectId;
let guardedId: ObjectId;

beforeEach(async () => {
  await col().deleteMany({});
  const other = mk(2);
  const mine = mk(1);
  const guarded = mk(1, { metadata: { customerId: 1, requiredRoles: ['admin'] } });
  await col().insertMany([other, mine, guarded] as any[]);
  otherId = other._id;
  mineId = mine._id;
  guardedId = guarded._id;
});

const q = `prefix=${PREFIX}`;
const as1 = { 'x-customer': '1' };

describe('fastify adapter with scope + authorize', () => {
  it('lists only the caller\'s processes', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(res.statusCode).toBe(200);
    expect(res.json().items.map((p: any) => p._id).sort()).toEqual([mineId, guardedId].map(String).sort());
    await app.close();
  });

  it('404s single-process reads and the tree stream for other customers\' processes', async () => {
    const app = await buildApp(makeEngine());
    for (const path of ['', '/tree', '/log', '/tree/log', '/tree/stream']) {
      const res = await app.inject({ method: 'GET', url: `/api/processes/${otherId}${path}?${q}`, headers: as1 });
      expect(res.statusCode, path).toBe(404);
    }
    const res = await app.inject({ method: 'GET', url: `/api/processes/${mineId}?${q}`, headers: as1 });
    expect(res.statusCode).toBe(200);
    await app.close();
  });

  it('404s commands on other customers\' processes and 403s denied ones, without reaching the engine', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    for (const cmd of ['launch', 'cancel', 'dismiss', 'resurrect']) {
      const out = await app.inject({ method: 'POST', url: `/api/processes/${otherId}/${cmd}?${q}`, headers: as1, payload: {} });
      expect(out.statusCode, cmd).toBe(404);
      const denied = await app.inject({ method: 'POST', url: `/api/processes/${guardedId}/${cmd}?${q}`, headers: as1, payload: {} });
      expect(denied.statusCode, cmd).toBe(403);
    }
    expect(engine.launch).not.toHaveBeenCalled();
    expect(engine.cancel).not.toHaveBeenCalled();
    expect(engine.dismiss).not.toHaveBeenCalled();
    expect(engine.resurrect).not.toHaveBeenCalled();
    const ok = await app.inject({ method: 'POST', url: `/api/processes/${mineId}/launch?${q}`, headers: as1, payload: {} });
    expect(ok.statusCode).toBe(200);
    expect(engine.launch).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('forwards resurrect with the body\'s session id', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const res = await app.inject({
      method: 'POST', url: `/api/processes/${mineId}/resurrect?${q}`, headers: as1, payload: { sessionId: 's1' },
    });
    expect(res.statusCode).toBe(200);
    expect(engine.resurrect).toHaveBeenCalledWith({ processId: String(mineId), sessionId: 's1' });
    const bare = await app.inject({ method: 'POST', url: `/api/processes/${mineId}/resurrect?${q}`, headers: as1, payload: {} });
    expect(bare.statusCode).toBe(200);
    expect(engine.resurrect).toHaveBeenLastCalledWith({ processId: String(mineId), sessionId: null });
    await app.close();
  });

  it('narrows resync to the caller\'s scope and refuses contradictions', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const res = await app.inject({
      method: 'POST', url: `/api/processes/resync?${q}`, headers: as1,
      payload: { metadataFilter: { dataspace: 'theirs' } },
    });
    expect(res.statusCode).toBe(202);
    expect(engine.resync).toHaveBeenCalledWith({ clean: false, metadataFilter: { dataspace: 'theirs', customerId: 1 } });
    const bad = await app.inject({
      method: 'POST', url: `/api/processes/resync?${q}`, headers: as1,
      payload: { metadataFilter: { customerId: 2 } },
    });
    expect(bad.statusCode).toBe(403);
    expect(engine.resync).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('403s instance discovery when authorize denies it', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances', headers: as1 });
    expect(res.statusCode).toBe(403);
    await app.close();
  });

  it('404s the widget proxy, widget-control and widget-upload for other customers\' processes', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const widget = await app.inject({ method: 'GET', url: `/api/widget/${DB_NAME}/${PREFIX}/${otherId}/`, headers: as1 });
    expect(widget.statusCode).toBe(404);
    const control = await app.inject({
      method: 'POST', url: `/api/widget-control/${DB_NAME}/${PREFIX}/${otherId}`, headers: as1,
      payload: { text: 'rm -rf /' },
    });
    expect(control.statusCode).toBe(404);
    const boundary = 'b0undary';
    const upload = await app.inject({
      method: 'POST', url: `/api/widget-upload/${DB_NAME}/${PREFIX}/${otherId}`,
      headers: { ...as1, 'content-type': `multipart/form-data; boundary=${boundary}` },
      payload: `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\nhi\r\n--${boundary}--\r\n`,
    });
    expect(upload.statusCode).toBe(404);
    expect(engine.materializeUpload).not.toHaveBeenCalled();
    await app.close();
  });

  it('403s widget routes that authorize denies', async () => {
    const app = await buildApp(makeEngine());
    const widget = await app.inject({ method: 'GET', url: `/api/widget/${DB_NAME}/${PREFIX}/${guardedId}/`, headers: as1 });
    expect(widget.statusCode).toBe(403);
    const control = await app.inject({
      method: 'POST', url: `/api/widget-control/${DB_NAME}/${PREFIX}/${guardedId}`, headers: as1,
      payload: { text: 'x' },
    });
    expect(control.statusCode).toBe(403);
    await app.close();
  });

  it('leaves an unrestricted caller (null scope) unrestricted', async () => {
    const app = await buildApp(makeEngine());
    const res = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: { 'x-customer': '0' } });
    expect(res.json().totalCount).toBe(3);
    await app.close();
  });

  it('hides a visibility-blocked chat from the list, single reads and the tree stream, and 404s commands without reaching the engine', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const hiddenChatId = await insertHiddenChat();

    const list = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(list.json().items.map((p: any) => p._id)).not.toContain(String(hiddenChatId));

    const get = await app.inject({ method: 'GET', url: `/api/processes/${hiddenChatId}?${q}`, headers: as1 });
    expect(get.statusCode).toBe(404);

    const tree = await app.inject({ method: 'GET', url: `/api/processes/${hiddenChatId}/tree/stream?${q}`, headers: as1 });
    expect(tree.statusCode).toBe(404);

    const cancel = await app.inject({ method: 'POST', url: `/api/processes/${hiddenChatId}/cancel?${q}`, headers: as1, payload: {} });
    expect(cancel.statusCode).toBe(404);
    expect(engine.cancel).not.toHaveBeenCalled();

    await app.close();
  });

  it('404s the widget proxy, widget-control and widget-upload for a visibility-blocked chat', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine);
    const hiddenChatId = await insertHiddenChat();

    const widget = await app.inject({ method: 'GET', url: `/api/widget/${DB_NAME}/${PREFIX}/${hiddenChatId}/`, headers: as1 });
    expect(widget.statusCode).toBe(404);
    const control = await app.inject({
      method: 'POST', url: `/api/widget-control/${DB_NAME}/${PREFIX}/${hiddenChatId}`, headers: as1,
      payload: { text: 'x' },
    });
    expect(control.statusCode).toBe(404);
    const boundary = 'b0undary';
    const upload = await app.inject({
      method: 'POST', url: `/api/widget-upload/${DB_NAME}/${PREFIX}/${hiddenChatId}`,
      headers: { ...as1, 'content-type': `multipart/form-data; boundary=${boundary}` },
      payload: `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\nhi\r\n--${boundary}--\r\n`,
    });
    expect(upload.statusCode).toBe(404);
    expect(engine.materializeUpload).not.toHaveBeenCalled();
    await app.close();
  });

  it('serves a result file with its own headers; 404s with the reason; 403s what authorize denies', async () => {
    const app = await buildApp(makeEngine());
    const url = (id: ObjectId) => `/api/processes/${id}/result?${q}`;

    const none = await app.inject({ method: 'GET', url: url(mineId), headers: as1 });
    expect(none.statusCode).toBe(404);
    expect(none.json()).toEqual({ reason: 'no-result', message: expect.any(String) });

    await storeResult(mineId, PDF);
    const ok = await app.inject({ method: 'GET', url: url(mineId), headers: as1 });
    expect(ok.statusCode).toBe(200);
    expect(ok.rawPayload.equals(PDF)).toBe(true);
    expect(ok.headers['content-type']).toBe('application/pdf');
    expect(ok.headers['content-length']).toBe(String(PDF.length));
    expect(ok.headers['content-disposition']).toBe(`inline; filename="a.pdf"; filename*=UTF-8''a.pdf`);
    expect(ok.headers['x-content-type-options']).toBe('nosniff');
    expect(ok.headers['content-security-policy']).toBeUndefined();

    // An active type renders sandboxed.
    await storeResult(mineId, Buffer.from('<script>1</script>'), 'a.html', 'text/html; charset=utf-8');
    const html = await app.inject({ method: 'GET', url: url(mineId), headers: as1 });
    expect(html.statusCode).toBe(200);
    expect(html.headers['content-type']).toBe('text/html; charset=utf-8');
    expect(html.headers['x-content-type-options']).toBe('nosniff');
    expect(html.headers['content-security-policy']).toBe('sandbox');

    await storeResult(otherId, PDF);
    const other = await app.inject({ method: 'GET', url: url(otherId), headers: as1 });
    expect(other.statusCode).toBe(404);
    expect(other.json()).toEqual({ reason: 'not-found', message: expect.any(String) });

    const hiddenChatId = await insertHiddenChat();
    await storeResult(hiddenChatId, PDF);
    const hidden = await app.inject({ method: 'GET', url: url(hiddenChatId), headers: as1 });
    expect(hidden.statusCode).toBe(404);
    expect(hidden.json()).toEqual({ reason: 'not-found', message: expect.any(String) });

    await storeResult(guardedId, PDF);
    const denied = await app.inject({ method: 'GET', url: url(guardedId), headers: as1 });
    expect(denied.statusCode).toBe(403);
    await app.close();
  });

  it('500s when the visibility hook returns an invalid predicate', async () => {
    const app = Fastify();
    registerOptioApi(app, {
      ctx: makeCtx(makeEngine()),
      authenticate: () => 'operator',
      scope: (req: any) => (customerOf(req) === 0 ? null : { customerId: customerOf(req) }),
      authorize: (req: any, input: AuthorizeInput) => policy(input, customerOf(req)),
      visibility: () => ({ AND: [] }) as any,
    });
    await app.ready();
    const res = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(res.statusCode).toBe(500);
    await app.close();
  });
});

describe('fastify adapter without hooks', () => {
  it('behaves as before: every process visible, commands go to the engine', async () => {
    const engine = makeEngine();
    const app = await buildApp(engine, false);
    const list = await app.inject({ method: 'GET', url: `/api/processes?${q}&limit=50`, headers: as1 });
    expect(list.json().totalCount).toBe(3);
    const launch = await app.inject({ method: 'POST', url: `/api/processes/${otherId}/launch?${q}`, headers: as1, payload: {} });
    expect(launch.statusCode).toBe(200);
    expect(engine.launch).toHaveBeenCalledTimes(1);
    await app.close();
  });
});
