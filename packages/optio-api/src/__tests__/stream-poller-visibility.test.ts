import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { eq } from 'optio-contracts';
import {
  createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller,
} from '../stream-poller.js';

// Spec: docs/2026-10-13-optio-api-visibility-design.md ("Streams")

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_stream_poller_visibility';
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
const visibility = eq('ownerId', 'u1');

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

describe('createListPoller visibility', () => {
  it('emits only visible processes', async () => {
    await insertProc({ metadata: { ownerId: 'u2' } });
    const mine = await insertProc({ metadata: { ownerId: 'u1' } });
    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX, onError: () => {}, sendEvent: (e) => events.push(e), visibility,
    });
    poller.start();
    try {
      await waitUntil(() => events.length > 0);
      expect(events[0].processes.map((p: any) => p._id)).toEqual([mine.toHexString()]);
    } finally {
      poller.stop();
    }
  });
});

describe('createSessionEventsPoller visibility', () => {
  it('emits session events only for visible processes', async () => {
    const event = { type: 'open-browser', at: new Date(0).toISOString() };
    await insertProc({ originatingSessionId: 's1', metadata: { ownerId: 'u2' }, sessionEvents: [event] });
    const mine = await insertProc({ originatingSessionId: 's1', metadata: { ownerId: 'u1' }, sessionEvents: [event] });
    const events: any[] = [];
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 's1', onError: () => {}, sendEvent: (e) => events.push(e), visibility,
    });
    poller.start();
    try {
      await waitUntil(() => events.length > 0);
      expect(events.map((e) => e.processId)).toEqual([mine.toHexString()]);
    } finally {
      poller.stop();
    }
  });
});

describe('createTreePoller visibility', () => {
  /** root (u1) -> child (u2, hidden) -> grandchild (u1). */
  async function insertFixture() {
    const root = new ObjectId();
    const child = new ObjectId();
    const grandchild = new ObjectId();
    await col().insertMany([
      {
        _id: root, processId: 'root', name: 'Root', rootId: root, parentId: null,
        depth: 0, order: 0, status: { state: 'idle' }, progress: {},
        metadata: { ownerId: 'u1' },
        log: [{ timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'root line' }],
      },
      {
        _id: child, processId: 'child', name: 'Child', rootId: root, parentId: root,
        depth: 1, order: 0, status: { state: 'idle' }, progress: {},
        metadata: { ownerId: 'u2' },
        log: [{ timestamp: '2026-05-21T00:00:01Z', level: 'info', message: 'child line' }],
      },
      {
        _id: grandchild, processId: 'grandchild', name: 'Grandchild', rootId: root, parentId: child,
        depth: 2, order: 0, status: { state: 'idle' }, progress: {},
        metadata: { ownerId: 'u1' },
        log: [{ timestamp: '2026-05-21T00:00:02Z', level: 'info', message: 'grandchild line' }],
      },
    ] as any);
    return { root, child, grandchild };
  }

  it('holds only the root at first, then re-reads as the child becomes visible, then as the root is hidden', async () => {
    const { root, child, grandchild } = await insertFixture();
    const updates: any[] = [];
    const logs: any[] = [];
    const poller = createTreePoller({
      db, prefix: PREFIX, onError: () => {},
      sendEvent: (e: any) => { if (e.type === 'update') updates.push(e); else if (e.type === 'log') logs.push(e); },
      rootId: root.toString(), baseDepth: 0, visibility,
    });
    poller.start();
    try {
      // The first update holds only the root: child and grandchild are
      // pruned as orphans of a hidden parent.
      await waitUntil(() => updates.length > 0);
      expect(updates[0].processes.map((p: any) => p._id)).toEqual([root.toHexString()]);

      // The log events carry only the root's lines.
      await waitUntil(() => logs.length > 0);
      const allEntries = logs.flatMap((e) => e.entries);
      expect(allEntries.every((e: any) => e.processId === root.toHexString())).toBe(true);
      expect(allEntries.length).toBeGreaterThan(0);

      // The relevance fix: a metadata change of a non-member (the child,
      // currently hidden) still triggers a re-read.
      await col().updateOne({ _id: child }, { $set: { 'metadata.ownerId': 'u1' } });
      await waitUntil(() => {
        const last = updates.at(-1);
        const ids = last?.processes.map((p: any) => p._id) ?? [];
        return ids.includes(child.toHexString()) && ids.includes(grandchild.toHexString());
      });

      // The root becomes hidden: an update with processes: [] arrives.
      await col().updateOne({ _id: root }, { $set: { 'metadata.ownerId': 'u2' } });
      await waitUntil(() => updates.at(-1)?.processes.length === 0);
    } finally {
      poller.stop();
    }
  }, 120_000);
});

describe('createMultiTreePoller visibility', () => {
  it('emits only the visible one of a hidden tree root and a visible flat id', async () => {
    const hiddenRoot = await insertProc({ metadata: { ownerId: 'u2' } });
    const visibleFlat = await insertProc({ metadata: { ownerId: 'u1' } });
    const updates: any[] = [];
    const poller = createMultiTreePoller({
      db, prefix: PREFIX, onError: () => {},
      sendEvent: (e: any) => { if (e.type === 'update') updates.push(e); },
      treeRoots: [{ rootId: hiddenRoot, baseDepth: 0 }],
      flatIds: [visibleFlat],
      visibility,
    });
    poller.start();
    try {
      await waitUntil(() => updates.length > 0);
      expect(updates[0].processes.map((p: any) => p._id)).toEqual([visibleFlat.toHexString()]);
    } finally {
      poller.stop();
    }
  });
});
