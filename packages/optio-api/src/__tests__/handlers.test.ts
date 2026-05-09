import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Redis from 'ioredis-mock';
import {
  getProcess, getProcessTree, getProcessLog, getProcessTreeLog,
  listProcesses, launchProcess, cancelProcess, dismissProcess,
} from '../handlers.js';
import { createOptioContext } from '../context.js';
import type { OptioContext } from '../context.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_handlers';
const PREFIX = 'test';

const fakeRedis: any = { duplicate: () => fakeRedis };

function makeCtx(database: Db): OptioContext {
  return createOptioContext({ dbOpts: { db: database }, redis: fakeRedis });
}

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

beforeEach(async () => {
  await db.collection(`${PREFIX}_processes`).deleteMany({});
});

describe('widgetUpstream stripping', () => {
  async function insertProcessWithUpstream(extra: Record<string, unknown> = {}) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      widgetUpstream: {
        url: 'http://127.0.0.1:9000',
        innerAuth: { kind: 'basic', username: 'admin', password: 'secret' },
      },
      ...extra,
    });
    return oid.toString();
  }

  it('getProcess does not return widgetUpstream', async () => {
    const id = await insertProcessWithUpstream();
    const result = await getProcess(makeCtx(db), { prefix: PREFIX }, id);
    expect(result).not.toBeNull();
    expect(result).not.toHaveProperty('widgetUpstream');
  });

  it('getProcessTree does not return widgetUpstream', async () => {
    const id = await insertProcessWithUpstream();
    const result = await getProcessTree(makeCtx(db), { prefix: PREFIX }, id);
    expect(result).not.toBeNull();
    expect(result).not.toHaveProperty('widgetUpstream');
  });

  it('listProcesses does not return widgetUpstream in any item', async () => {
    await insertProcessWithUpstream();
    await insertProcessWithUpstream({ processId: 'q', name: 'Q' });
    const result = await listProcesses(makeCtx(db), { limit: 10, prefix: PREFIX });
    expect(result.items.length).toBeGreaterThan(0);
    for (const item of result.items) {
      expect(item).not.toHaveProperty('widgetUpstream');
    }
  });
});

describe('launchProcess — resume validation', () => {
  let redis: any;

  beforeEach(async () => {
    redis = new Redis();
    await redis.flushall();
  });

  async function insertLaunchable(extra: Record<string, unknown> = {}) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      supportsResume: false,
      hasSavedState: false,
      ...extra,
    });
    return oid.toString();
  }

  it('rejects resume=true when task does not support resume', async () => {
    const id = await insertLaunchable({ supportsResume: false });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id, true);
    expect(result.status).toBe(409);
  });

  it('accepts resume=true when task supports resume (regardless of hasSavedState)', async () => {
    const id = await insertLaunchable({
      processId: 'q', supportsResume: true, hasSavedState: false,
    });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id, true);
    expect(result.status).toBe(200);

    const entries = await redis.xrange('mydb/test:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume).toBe(true);
    expect(payload.processId).toBe('q');
  });

  it('accepts missing body (backwards compatible): resume defaults to false', async () => {
    const id = await insertLaunchable({ processId: 'r' });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id /* no resume */);
    expect(result.status).toBe(200);

    const entries = await redis.xrange('mydb/test:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume ?? false).toBe(false);
  });
});

describe('dual-form id resolution (ObjectId hex OR processId string)', () => {
  // The mkPid-style processId is the form excavator's recipe-debug
  // flow returns to the frontend at submit time, before the engine
  // creates the row. The frontend then hands it to optio-ui's
  // useProcessStream, which calls these handlers with the string.
  // Without dual-form lookup, every per-process route 500'd when
  // `new ObjectId(string)` threw on the non-hex input.
  const PROCESS_ID_STRING = 'someproject__recipe-debug_abc_def';

  async function insertProcess(state: string = 'idle') {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: PROCESS_ID_STRING,
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state },
      progress: { percent: null },
      cancellable: true,
      log: [
        { timestamp: '2026-05-06T00:00:00.000Z', level: 'info', message: 'one' },
        { timestamp: '2026-05-06T00:00:01.000Z', level: 'info', message: 'two' },
      ],
      supportsResume: false,
      hasSavedState: false,
    });
    return { oid: oid.toString(), processIdString: PROCESS_ID_STRING };
  }

  it('getProcess resolves both ObjectId hex and processId string to the same row', async () => {
    const { oid, processIdString } = await insertProcess();
    const byOid = await getProcess(makeCtx(db), { prefix: PREFIX }, oid);
    const byString = await getProcess(makeCtx(db), { prefix: PREFIX }, processIdString);
    expect(byOid).not.toBeNull();
    expect(byString).not.toBeNull();
    expect((byOid as any)._id).toBe((byString as any)._id);
  });

  it('getProcess returns null on unknown processId string (no throw)', async () => {
    const result = await getProcess(makeCtx(db), { prefix: PREFIX }, 'unknown__processid_string');
    expect(result).toBeNull();
  });

  it('getProcessTree accepts the processId string form', async () => {
    const { processIdString } = await insertProcess();
    const tree = await getProcessTree(makeCtx(db), { prefix: PREFIX }, processIdString);
    expect(tree).not.toBeNull();
    expect((tree as any).processId).toBe(processIdString);
  });

  it('getProcessLog accepts the processId string form', async () => {
    const { processIdString } = await insertProcess();
    const log = await getProcessLog(makeCtx(db), { limit: 10, prefix: PREFIX }, processIdString);
    expect(log).not.toBeNull();
    expect((log as any).items.length).toBe(2);
  });

  it('getProcessTreeLog accepts the processId string form', async () => {
    const { processIdString } = await insertProcess();
    const log = await getProcessTreeLog(makeCtx(db), { limit: 10, prefix: PREFIX }, processIdString);
    expect(log).not.toBeNull();
    expect((log as any).items.length).toBe(2);
  });

  it('launchProcess accepts the processId string form', async () => {
    const redis = new Redis();
    await redis.flushall();
    const { processIdString } = await insertProcess('idle');
    const result = await launchProcess(db, redis, 'mydb', PREFIX, processIdString);
    expect(result.status).toBe(200);
  });

  it('cancelProcess accepts the processId string form', async () => {
    const redis = new Redis();
    await redis.flushall();
    const { processIdString } = await insertProcess('running');
    const result = await cancelProcess(db, redis, 'mydb', PREFIX, processIdString);
    expect(result.status).toBe(200);
  });

  it('dismissProcess accepts the processId string form', async () => {
    const redis = new Redis();
    await redis.flushall();
    const { processIdString } = await insertProcess('done');
    const result = await dismissProcess(db, redis, 'mydb', PREFIX, processIdString);
    expect(result.status).toBe(200);
  });
});

describe('listProcesses metadataFilter', () => {
  beforeEach(async () => {
    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  async function insert(metadata: Record<string, unknown>) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      metadata,
    });
    return oid.toString();
  }

  it('returns all processes when no filter', async () => {
    await insert({ project: 'x' });
    await insert({ project: 'y' });
    const r = await listProcesses(makeCtx(db), { limit: 50, prefix: PREFIX });
    expect(r.items.length).toBe(2);
    expect(r.totalCount).toBe(2);
  });

  it('filters processes by metadata key', async () => {
    await insert({ project: 'x' });
    await insert({ project: 'y' });
    const r = await listProcesses(makeCtx(db), { limit: 50, prefix: PREFIX, metadataFilter: { project: 'x' } });
    expect(r.items.length).toBe(1);
    expect((r.items[0] as any).metadata.project).toBe('x');
    expect(r.totalCount).toBe(1);
  });

  it('AND-matches multiple keys', async () => {
    await insert({ project: 'x', kind: 'a' });
    await insert({ project: 'x', kind: 'b' });
    await insert({ project: 'y', kind: 'a' });
    const r = await listProcesses(makeCtx(db), {
      limit: 50,
      prefix: PREFIX,
      metadataFilter: { project: 'x', kind: 'a' },
    });
    expect(r.items.length).toBe(1);
    expect((r.items[0] as any).metadata).toEqual({ project: 'x', kind: 'a' });
  });

  it('returns empty when filter matches nothing', async () => {
    await insert({ project: 'x' });
    const r = await listProcesses(makeCtx(db), { limit: 50, prefix: PREFIX, metadataFilter: { project: 'nope' } });
    expect(r.items.length).toBe(0);
    expect(r.totalCount).toBe(0);
  });
});
