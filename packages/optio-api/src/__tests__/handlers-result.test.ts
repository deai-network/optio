import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { GridFSBucket, MongoClient, ObjectId, type Db } from 'mongodb';
import { getProcessResult } from '../handlers.js';
import {
  contentDisposition, GRIDFS_RESULT_DOWNLOAD, resultQueryOf, type ResultDownloadDeps,
} from '../result-download.js';
import type { OptioContext } from '../context.js';
import type { Access } from '../auth.js';
import { eq, not } from 'optio-contracts';

// GET /processes/:id/result: a task's result file (optio-core's
// store_result), streamed from GridFS.
// Spec: docs/2026-10-25-factory-results-design.md §1 ("Serving it")

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_handlers_result';
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

beforeEach(async () => {
  await col().deleteMany({});
  await db.collection('fs.files').deleteMany({});
  await db.collection('fs.chunks').deleteMany({});
});

const ctx = () => ({ dbOpts: { db } }) as unknown as OptioContext;

// Not valid UTF-8: the bytes must come back untouched.
const PDF = Buffer.from([0x25, 0x50, 0x44, 0x46, 0x2d, 0x00, 0xff, 0xfe, 0x0a, 0x80]);

async function insertProcess(metadata: Record<string, unknown> = {}): Promise<ObjectId> {
  const _id = new ObjectId();
  await col().insertOne({
    _id, processId: `p-${_id.toHexString()}`, name: 'P', rootId: _id, parentId: null,
    depth: 0, order: 0, status: { state: 'done' }, progress: { percent: 100 },
    cancellable: false, log: [], metadata,
  });
  return _id;
}

/** What optio-core's store_result leaves: the GridFS file, and the record's `result`. */
async function storeResult(
  processOid: ObjectId, bytes: Buffer, filename = 'a.pdf', contentType = 'application/pdf',
): Promise<ObjectId> {
  const upload = new GridFSBucket(db).openUploadStream(filename, {
    metadata: { processId: processOid.toHexString(), prefix: PREFIX, name: filename, kind: 'result' },
  });
  await pipeline(Readable.from([bytes]), upload);
  const fileId = upload.id as ObjectId;
  await col().updateOne(
    { _id: processOid },
    { $set: { result: { fileId, filename, contentType, size: bytes.length } } },
  );
  return fileId;
}

async function readAll(stream: Readable): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

/** The real GridFS reads, watched: whether the handler opened the bucket at all. */
function watchedDeps() {
  return { bucket: vi.fn((d: Db) => GRIDFS_RESULT_DOWNLOAD.bucket(d)) } satisfies ResultDownloadDeps;
}

function restricted(overrides: Partial<Access> = {}): Access {
  return {
    restricted: true, scope: null, visibility: null, authorize: vi.fn(async () => true), ...overrides,
  };
}

describe('getProcessResult', () => {
  it('streams the result file with its Content-Type, Content-Length and Content-Disposition', async () => {
    const id = await insertProcess();
    await storeResult(id, PDF);
    const res: any = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString());
    expect(res.status).toBe(200);
    // Exactly these: a PDF gets nosniff and no Content-Security-Policy.
    expect(res.headers).toEqual({
      'Content-Type': 'application/pdf',
      'Content-Length': String(PDF.length),
      'Content-Disposition': `inline; filename="a.pdf"; filename*=UTF-8''a.pdf`,
      'X-Content-Type-Options': 'nosniff',
    });
    expect(res.headers).not.toHaveProperty('Content-Security-Policy');
    expect((await readAll(res.body)).equals(PDF)).toBe(true);
  });

  // The file renders inline in the host app's origin: an active type must not run as the app.
  it('sandboxes an HTML result and forbids sniffing', async () => {
    const id = await insertProcess();
    const html = Buffer.from('<script>alert(document.cookie)</script>');
    await storeResult(id, html, 'a.html', 'text/html; charset=utf-8');
    const res: any = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString());
    expect(res.status).toBe(200);
    expect(res.headers['Content-Type']).toBe('text/html; charset=utf-8');
    expect(res.headers['X-Content-Type-Options']).toBe('nosniff');
    expect(res.headers['Content-Security-Policy']).toBe('sandbox');
    res.body.destroy();
  });

  it('judges PDF by the media type alone: case and parameters do not matter', async () => {
    const pdf = await insertProcess();
    await storeResult(pdf, PDF, 'a.pdf', 'Application/PDF; name=a.pdf');
    const pdfRes: any = await getProcessResult(ctx(), { prefix: PREFIX }, pdf.toHexString());
    expect(pdfRes.headers['X-Content-Type-Options']).toBe('nosniff');
    expect(pdfRes.headers).not.toHaveProperty('Content-Security-Policy');
    pdfRes.body.destroy();

    const svg = await insertProcess();
    await storeResult(svg, Buffer.from('<svg/>'), 'a.svg', 'image/svg+xml');
    const svgRes: any = await getProcessResult(ctx(), { prefix: PREFIX }, svg.toHexString());
    expect(svgRes.headers['Content-Security-Policy']).toBe('sandbox');
    svgRes.body.destroy();
  });

  it('finds the process by its processId too', async () => {
    const id = await insertProcess();
    await storeResult(id, PDF);
    const res: any = await getProcessResult(ctx(), { prefix: PREFIX }, `p-${id.toHexString()}`);
    expect(res.status).toBe(200);
    expect((await readAll(res.body)).equals(PDF)).toBe(true);
  });

  it('answers 404 no-result for a process without a result', async () => {
    const id = await insertProcess();
    const res = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString());
    expect(res).toEqual({ status: 404, body: { reason: 'no-result', message: expect.any(String) } });
  });

  it('answers 404 no-result when the result\'s file is gone, not 500', async () => {
    const id = await insertProcess();
    const fileId = await storeResult(id, PDF);
    await new GridFSBucket(db).delete(fileId);
    const res = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString());
    expect(res).toEqual({ status: 404, body: { reason: 'no-result', message: expect.any(String) } });
  });

  it('answers 404 not-found for an unknown id, in either form', async () => {
    for (const id of [new ObjectId().toHexString(), 'no-such-process']) {
      const res = await getProcessResult(ctx(), { prefix: PREFIX }, id);
      expect(res, id).toEqual({ status: 404, body: { reason: 'not-found', message: expect.any(String) } });
    }
  });

  it('answers 404 not-found for a removed record, never its leftover file', async () => {
    const id = await insertProcess();
    await storeResult(id, PDF);
    await col().deleteOne({ _id: id });
    const deps = watchedDeps();
    const res = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString(), undefined, deps);
    expect(res).toEqual({ status: 404, body: { reason: 'not-found', message: expect.any(String) } });
    expect(deps.bucket).not.toHaveBeenCalled();
  });

  it('answers 404 not-found for a process hidden by visibility, without asking authorize or reading GridFS', async () => {
    const id = await insertProcess({ kind: 'data-chat' });
    await storeResult(id, PDF);
    const access = restricted({ visibility: not(eq('kind', 'data-chat')) });
    const deps = watchedDeps();
    const res = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString(), access, deps);
    expect(res).toEqual({ status: 404, body: { reason: 'not-found', message: expect.any(String) } });
    expect(access.authorize).not.toHaveBeenCalled();
    expect(deps.bucket).not.toHaveBeenCalled();
  });

  it('answers 404 not-found for a process outside the scope', async () => {
    const id = await insertProcess({ customerId: 2 });
    await storeResult(id, PDF);
    const res = await getProcessResult(
      ctx(), { prefix: PREFIX }, id.toHexString(), restricted({ scope: { customerId: 1 } }),
    );
    expect(res).toEqual({ status: 404, body: { reason: 'not-found', message: expect.any(String) } });
  });

  it('asks authorize with action result and the process; 403 when it refuses, without reading GridFS', async () => {
    const id = await insertProcess();
    await storeResult(id, PDF);
    const allowed = restricted();
    const res: any = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString(), allowed);
    expect(res.status).toBe(200);
    expect((await readAll(res.body)).equals(PDF)).toBe(true);
    expect(allowed.authorize).toHaveBeenCalledWith({
      action: 'result',
      process: expect.objectContaining({ _id: id.toHexString(), processId: `p-${id.toHexString()}` }),
    });

    const deps = watchedDeps();
    const refused = restricted({ authorize: vi.fn(async () => false) });
    const denied = await getProcessResult(ctx(), { prefix: PREFIX }, id.toHexString(), refused, deps);
    expect(denied).toEqual({ status: 403, body: { message: 'Forbidden' } });
    expect(deps.bucket).not.toHaveBeenCalled();
  });
});

describe('contentDisposition', () => {
  it('gives a plain ASCII name as both forms', () => {
    expect(contentDisposition('a.pdf')).toBe(`inline; filename="a.pdf"; filename*=UTF-8''a.pdf`);
  });

  it('stays one line: the ASCII fallback replaces quotes, ;, newlines and non-ASCII, filename* percent-encodes them', () => {
    const value = contentDisposition('Feladat "1"; ő\n.pdf');
    expect(value).toBe(
      `inline; filename="Feladat _1__ __.pdf"; filename*=UTF-8''Feladat%20%221%22%3B%20%C5%91%0A.pdf`,
    );
    expect(value).not.toMatch(/[\r\n]/);
  });

  it('replaces a backslash and % in the fallback, and encodes what RFC 5987 leaves out of attr-char', () => {
    expect(contentDisposition(`it's 100% (1)*\\.pdf`)).toBe(
      `inline; filename="it's 100_ (1)*_.pdf"; filename*=UTF-8''it%27s%20100%25%20%281%29%2A%5C.pdf`,
    );
  });
});

// The adapters serve the route outside ts-rest; this is the query check
// ts-rest would have made, with the contract's own schema.
describe('resultQueryOf', () => {
  it('takes database and prefix, and drops other keys (a Next catch-all segment)', () => {
    expect(resultQueryOf({ database: 'd', prefix: 'p', 'ts-rest': ['api'] }))
      .toEqual({ ok: true, query: { database: 'd', prefix: 'p' } });
    expect(resultQueryOf({})).toEqual({ ok: true, query: {} });
  });

  it('refuses a repeated or non-string value with a 400', () => {
    expect(resultQueryOf({ prefix: ['a', 'b'] }))
      .toEqual({ ok: false, status: 400, body: { message: expect.any(String) } });
  });
});
