import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createSessionEventsPoller } from '../stream-poller.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_session_events_poller';
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

describe('createSessionEventsPoller', () => {
  it('delivers only events for matching originatingSessionId', async () => {
    const events: any[] = [];
    await db.collection(`${PREFIX}_processes`).insertMany([
      {
        _id: new ObjectId(), processId: 'mine', name: 'Mine',
        originatingSessionId: 'tok-A',
        sessionEvents: [{ requestId: 'r1', type: 'attention', reason: 'help' }],
      },
      {
        _id: new ObjectId(), processId: 'other', name: 'Other',
        originatingSessionId: 'tok-B',
        sessionEvents: [{ requestId: 'r2', type: 'attention', reason: 'nope' }],
      },
    ]);
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 'tok-A',
      sendEvent: (d) => events.push(d), onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const msgs = events.filter((e) => e.type === 'session-events');
    expect(msgs).toHaveLength(1);
    expect(msgs[0].events).toEqual([{ requestId: 'r1', type: 'attention', reason: 'help' }]);
  });

  it('emits only newly-appended events on subsequent ticks (dedup by high-water mark)', async () => {
    const events: any[] = [];
    const id = new ObjectId();
    const coll = db.collection(`${PREFIX}_processes`);
    await coll.insertOne({
      _id: id, processId: 'p', name: 'P', originatingSessionId: 'tok',
      sessionEvents: [{ requestId: 'r1', type: 'attention', reason: 'a' }],
    });
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 'tok',
      sendEvent: (d) => events.push(d), onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    await coll.updateOne({ _id: id }, { $push: { sessionEvents: { requestId: 'r2', type: 'client', keyword: 'k', data: 1 } } });
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const msgs = events.filter((e) => e.type === 'session-events');
    const allReqIds = msgs.flatMap((m) => m.events.map((e: any) => e.requestId));
    expect(allReqIds).toEqual(['r1', 'r2']);
  });
});
