import { describe, it, expect, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createTreePoller, createListPoller, createSessionEventsPoller } from '../stream-poller.js';
import {
  ProcessChangeHub, createMongoProcessChangeSource, type ProcessChangeSource,
} from '../process-change-hub.js';

// Spec: docs/2026-10-09-api-change-streams-design.md (Testing: real MongoDB)

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_change_streams';
const PREFIX = 'cs';

const client = new MongoClient(MONGO_URL);
await client.connect();
const db: Db = client.db(DB_NAME);
const isReplicaSet = Boolean((await client.db('admin').command({ hello: 1 })).setName);

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

/** A generous ceiling only bounds a hang; correctness waits on the condition. */
async function waitUntil(pred: () => boolean | Promise<boolean>, timeoutMs = 60_000): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await pred()) return;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error('waitUntil: condition not met within timeout');
}

/** `db` whose collections count their find() calls in `counter.finds`. */
function countingDb(counter: { finds: number }): Db {
  return new Proxy(db, {
    get(target, prop) {
      if (prop === 'collection') {
        return (name: string) => {
          const coll = target.collection(name);
          return new Proxy(coll, {
            get(c, p) {
              if (p === 'find') {
                return (...args: any[]) => { counter.finds++; return (c.find as any)(...args); };
              }
              const v = Reflect.get(c, p, c);
              return typeof v === 'function' ? v.bind(c) : v;
            },
          });
        };
      }
      const v = Reflect.get(target, prop, target);
      return typeof v === 'function' ? v.bind(target) : v;
    },
  });
}

const procs = () => db.collection(`${PREFIX}_processes`);

/** A write elsewhere, so the stream's start time (the cluster time of `hello`)
 *  lies after the test's own setup writes and none of them is replayed. */
const moveClusterTime = () => db.collection(`${PREFIX}_other`).insertOne({ at: new Date() });

function root(state = 'idle') {
  const _id = new ObjectId();
  return {
    _id, processId: `p-${_id}`, name: 'P', rootId: _id, parentId: null, depth: 0, order: 0,
    status: { state }, progress: { percent: null }, cancellable: true, log: [], metadata: {},
  };
}

describe.skipIf(!isReplicaSet)('live streams on a change stream (needs a replica set)', () => {
  let hub: ProcessChangeHub;

  beforeEach(async () => {
    await procs().deleteMany({});
    hub = new ProcessChangeHub(createMongoProcessChangeSource(db, PREFIX), { minIntervalMs: 0 });
  });

  it('a tree stream re-reads for its own process, not for an unrelated one', async () => {
    const mine = root();
    const other = root();
    await procs().insertMany([mine, other]);
    await moveClusterTime();
    const counter = { finds: 0 };
    const updates: any[] = [];
    const poller = createTreePoller({
      db: countingDb(counter), prefix: PREFIX, rootId: mine._id.toString(), baseDepth: 0, hub,
      sendEvent: (e: any) => { if (e.type === 'update') updates.push(e); },
      onError: () => {},
    });
    poller.start();
    await waitUntil(() => updates.length === 1);

    await procs().updateOne({ _id: other._id }, { $set: { 'status.state': 'running' } });
    await procs().updateOne({ _id: mine._id }, { $set: { 'status.state': 'running' } });
    await waitUntil(() => updates.length === 2);
    poller.stop();

    expect(updates[1].processes[0].status.state).toBe('running');
    expect(counter.finds).toBe(2);
  });

  it('a list stream re-reads when a shown process changes status', async () => {
    const p = root();
    await procs().insertOne(p);
    await moveClusterTime();
    const updates: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX, hub,
      sendEvent: (e: any) => { if (e.type === 'update') updates.push(e); },
      onError: () => {},
    });
    poller.start();
    await waitUntil(() => updates.length === 1);

    await procs().updateOne({ _id: p._id }, { $set: { 'status.state': 'running' } });
    await waitUntil(() => updates.length === 2);
    poller.stop();

    expect(updates[1].processes[0].status.state).toBe('running');
  });

  it('a session-events stream picks up a process that joins the session', async () => {
    const p = root('running');
    await procs().insertOne(p);
    await moveClusterTime();
    const events: any[] = [];
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 's1', hub,
      sendEvent: (e: any) => events.push(e),
      onError: () => {},
    });
    poller.start();
    await waitUntil(() => hub.mode === 'streaming');

    await procs().updateOne({ _id: p._id }, { $set: { originatingSessionId: 's1' } });
    await procs().updateOne({ _id: p._id }, { $push: { sessionEvents: { kind: 'e' } } } as any);
    await waitUntil(() => events.length > 0);
    poller.stop();

    expect(events).toEqual([
      { type: 'session-events', processId: p._id.toString(), events: [{ kind: 'e' }] },
    ]);
  });
});

describe('stopping a stream before its hub opened', () => {
  it('leaves no subscription and makes no read', async () => {
    let resolveOpen: () => void = () => {};
    const source: ProcessChangeSource = {
      open: () => new Promise((resolve) => {
        resolveOpen = () => resolve({ close: async () => {} });
      }),
    };
    const pending = new ProcessChangeHub(source);
    const counter = { finds: 0 };
    const poller = createListPoller({
      db: countingDb(counter), prefix: PREFIX, hub: pending,
      sendEvent: () => {}, onError: () => {},
    });

    poller.start();
    poller.stop();
    resolveOpen();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(pending.size).toBe(0);
    expect(counter.finds).toBe(0);
  });
});
