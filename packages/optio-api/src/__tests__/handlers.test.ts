import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { getProcess, getProcessTree, listProcesses } from '../handlers.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_handlers';
const PREFIX = 'test';

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
    const result = await getProcess(db, PREFIX, id);
    expect(result).not.toBeNull();
    expect(result).not.toHaveProperty('widgetUpstream');
  });

  it('getProcessTree does not return widgetUpstream', async () => {
    const id = await insertProcessWithUpstream();
    const result = await getProcessTree(db, PREFIX, id);
    expect(result).not.toBeNull();
    expect(result).not.toHaveProperty('widgetUpstream');
  });

  it('listProcesses does not return widgetUpstream in any item', async () => {
    await insertProcessWithUpstream();
    await insertProcessWithUpstream({ processId: 'q', name: 'Q' });
    const result = await listProcesses(db, PREFIX, { limit: 10 });
    expect(result.items.length).toBeGreaterThan(0);
    for (const item of result.items) {
      expect(item).not.toHaveProperty('widgetUpstream');
    }
  });
});
