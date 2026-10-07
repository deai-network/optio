import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createListPoller, createSessionEventsPoller } from '../stream-poller.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_stream_poller_scope';
const PREFIX = 'test';

/** Wait for a condition; the ceiling only bounds a genuine hang. */
async function waitUntil(
  pred: () => boolean | Promise<boolean>,
  timeoutMs = 60_000,
  stepMs = 20,
): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await pred()) return;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitUntil: condition not met within timeout');
}

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
});

async function insertProc(fields: Record<string, unknown>) {
  const oid = new ObjectId();
  await col().insertOne({
    _id: oid, processId: `p-${oid.toHexString()}`, name: 'P', rootId: oid, parentId: null,
    depth: 0, order: 0, status: { state: 'idle' }, progress: { percent: null }, log: [],
    ...fields,
  } as any);
  return oid;
}

describe('createListPoller scope', () => {
  it('emits only in-scope processes, also when the client filter asks for others', async () => {
    await insertProc({ metadata: { customerId: 0, dataspace: 'a' } });
    const mine = await insertProc({ metadata: { customerId: 1, dataspace: 'b' } });
    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX, onError: () => {}, sendEvent: (e) => events.push(e),
      scope: { customerId: 1 },
    });
    poller.start();
    try {
      await waitUntil(() => events.length > 0);
      expect(events[0].processes.map((p: any) => p._id)).toEqual([mine.toHexString()]);
    } finally {
      poller.stop();
    }

    const sneaky: any[] = [];
    const poller2 = createListPoller({
      db, prefix: PREFIX, onError: () => {}, sendEvent: (e) => sneaky.push(e),
      metadataFilter: { dataspace: 'a' }, scope: { customerId: 1 },
    });
    poller2.start();
    try {
      await waitUntil(() => sneaky.length > 0);
      expect(sneaky[0].processes).toEqual([]);
    } finally {
      poller2.stop();
    }
  });
});

describe('createSessionEventsPoller scope', () => {
  it('emits session events only for in-scope processes', async () => {
    const event = { type: 'open-browser', at: new Date(0).toISOString() };
    await insertProc({ originatingSessionId: 's1', metadata: { customerId: 0 }, sessionEvents: [event] });
    const mine = await insertProc({ originatingSessionId: 's1', metadata: { customerId: 1 }, sessionEvents: [event] });
    const events: any[] = [];
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 's1', onError: () => {}, sendEvent: (e) => events.push(e),
      scope: { customerId: 1 },
    });
    poller.start();
    try {
      await waitUntil(() => events.length > 0);
      // One more tick would have delivered the other process's events together
      // with these (both exist from the start), so the first batch is complete.
      expect(events.map((e) => e.processId)).toEqual([mine.toHexString()]);
    } finally {
      poller.stop();
    }
  });
});
