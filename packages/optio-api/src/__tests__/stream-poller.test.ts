import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createTreePoller, createListPoller } from '../stream-poller.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_stream_poller';
const PREFIX = 'test';

/**
 * Wait until `pred` holds, polling frequently. The generous ceiling only
 * bounds a genuine hang; correctness never depends on wall-clock timing, so
 * this stays reliable even when the CPU is heavily oversubscribed and the
 * poller's ~1s ticks are delayed.
 */
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

describe('createTreePoller widgetData propagation', () => {
  it('includes widgetData in the update event payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetData: { hello: 'world' },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].widgetData).toEqual({ hello: 'world' });
  });

  it('fires an update event when ONLY widgetData changes', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    const coll = db.collection(`${PREFIX}_processes`);
    await coll.insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetData: { v: 1 },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.filter((e) => e.type === 'update').length >= 1);

    const before = events.filter((e) => e.type === 'update').length;
    expect(before).toBeGreaterThanOrEqual(1);

    await coll.updateOne(
      { _id: rootId },
      { $set: { widgetData: { v: 2 } } },
    );
    await waitUntil(() => events.filter((e) => e.type === 'update').length > before);
    poller.stop();

    const after = events.filter((e) => e.type === 'update').length;
    expect(after).toBeGreaterThan(before);
    const last = [...events].reverse().find((e) => e.type === 'update');
    expect(last.processes[0].widgetData).toEqual({ v: 2 });
  });

  it('includes uiWidget in the update event payload when set', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      uiWidget: 'my-custom-widget',
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].uiWidget).toBe('my-custom-widget');
  });

  it('includes metadata in the update event payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      metadata: { known_bad: true, broken_reason: 'target disabled' },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].metadata).toEqual({
      known_bad: true, broken_reason: 'target disabled',
    });
  });

  it('fires an update event when ONLY metadata changes', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    const coll = db.collection(`${PREFIX}_processes`);
    await coll.insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      metadata: { known_bad: false },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.filter((e) => e.type === 'update').length >= 1);

    const before = events.filter((e) => e.type === 'update').length;
    expect(before).toBeGreaterThanOrEqual(1);

    await coll.updateOne(
      { _id: rootId },
      { $set: { metadata: { known_bad: true, broken_reason: 'target disabled' } } },
    );
    await waitUntil(() => events.filter((e) => e.type === 'update').length > before);
    poller.stop();

    const after = events.filter((e) => e.type === 'update').length;
    expect(after).toBeGreaterThan(before);
    const last = [...events].reverse().find((e) => e.type === 'update');
    expect(last.processes[0].metadata.known_bad).toBe(true);
    expect(last.processes[0].metadata.broken_reason).toBe('target disabled');
  });

  it('never includes widgetUpstream in the payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetUpstream: {
        url: 'http://127.0.0.1:9000',
        innerAuth: { kind: 'basic', username: 'u', password: 'p' },
      },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].widgetUpstream).toBeUndefined();
    for (const p of update.processes) {
      expect(Object.keys(p)).not.toContain('widgetUpstream');
    }
  });
});

describe('createListPoller metadataFilter', () => {
  beforeEach(async () => {
    await db.collection(`${PREFIX}_processes`).deleteMany({});
  });

  async function insertProc(metadata: Record<string, unknown>) {
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
    return oid;
  }

  it('emits all processes when no filter is set', async () => {
    await insertProc({ project: 'x' });
    await insertProc({ project: 'y' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(2);
  });

  it('emits only matching processes when filter is set', async () => {
    await insertProc({ project: 'x' });
    await insertProc({ project: 'y' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
      metadataFilter: { project: 'x' },
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(1);
    expect(update.processes[0].metadata.project).toBe('x');
  });

  it('AND-matches multiple keys', async () => {
    await insertProc({ project: 'x', kind: 'a' });
    await insertProc({ project: 'x', kind: 'b' });
    await insertProc({ project: 'y', kind: 'a' });

    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (e) => events.push(e),
      onError: () => {},
      metadataFilter: { project: 'x', kind: 'a' },
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(1);
  });
});

describe('createMultiTreePoller', () => {
  it('emits combined update for multiple tree roots', async () => {
    const events: any[] = [];
    const rootA = new ObjectId(); const childA = new ObjectId();
    const rootB = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: childA, processId: 'pAC', name: 'A-child', rootId: rootA, parentId: rootA, depth: 1, order: 0, status: { state: 'done' }, progress: {}, cancellable: false, log: [] },
      { _id: rootB, processId: 'pB', name: 'B', rootId: rootB, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      treeRoots: [
        { rootId: rootA, baseDepth: 0 },
        { rootId: rootB, baseDepth: 0 },
      ],
      flatIds: [],
    });
    poller.start();
    await waitUntil(() => events.length > 0);
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const procs = events[0].processes;
    const ids = procs.map((p: any) => p.processId).sort();
    expect(ids).toEqual(['pA', 'pAC', 'pB']);
    const rowA = procs.find((p: any) => p.processId === 'pA');
    const rowAC = procs.find((p: any) => p.processId === 'pAC');
    const rowB = procs.find((p: any) => p.processId === 'pB');
    expect(rowA.rootId).toBe(rootA.toString());
    expect(rowAC.rootId).toBe(rootA.toString());
    expect(rowB.rootId).toBe(rootB.toString());
  });

  it('flat ids fetch only the named row, not descendants', async () => {
    const events: any[] = [];
    const flatRoot = new ObjectId(); const flatChild = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: flatRoot, processId: 'flatR', name: 'flat-root', rootId: flatRoot, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: flatChild, processId: 'flatC', name: 'flat-child', rootId: flatRoot, parentId: flatRoot, depth: 1, order: 0, status: { state: 'running' }, progress: {}, cancellable: false, log: [] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      treeRoots: [],
      flatIds: [flatRoot],
    });
    poller.start();
    await waitUntil(() => events.length > 0);
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const ids = events[0].processes.map((p: any) => p.processId);
    expect(ids).toEqual(['flatR']);
    expect(ids).not.toContain('flatC');
  });

  it('log events carry rootId for client routing', async () => {
    const logs: any[] = [];
    const rootA = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null,
      depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true,
      log: [{ timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'hello' }],
    });
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'log') logs.push(e); },
      onError: () => {},
      treeRoots: [{ rootId: rootA, baseDepth: 0 }],
      flatIds: [],
    });
    poller.start();
    await waitUntil(() => logs.length > 0);
    poller.stop();

    expect(logs.length).toBeGreaterThan(0);
    expect(logs[0].entries[0].rootId).toBe(rootA.toString());
  });

  it('flat-id descendants do NOT emit log entries', async () => {
    const logs: any[] = [];
    const updates: any[] = [];
    const flatRoot = new ObjectId(); const flatChild = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: flatRoot, processId: 'flatR', name: 'flat-root', rootId: flatRoot, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: flatChild, processId: 'flatC', name: 'flat-child', rootId: flatRoot, parentId: flatRoot, depth: 1, order: 0, status: { state: 'running' }, progress: {}, cancellable: false, log: [{ timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'descendant log' }] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => {
        if (e.type === 'log') logs.push(e);
        else if (e.type === 'update') updates.push(e);
      },
      onError: () => {},
      treeRoots: [],
      flatIds: [flatRoot],
    });
    poller.start();
    // Wait for a full poll cycle to complete (the update proves the poll ran).
    // Log entries are emitted synchronously in that same poll after the update,
    // so if any were due for the flat row they would already be present.
    await waitUntil(() => updates.length > 0);
    poller.stop();

    expect(logs).toHaveLength(0);
  });
});

describe('createTreePoller rootId propagation', () => {
  it('includes rootId on every process in the update event', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p-root', name: 'Root',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      cancellable: true,
      log: [],
    });
    const childId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: childId,
      processId: 'p-child', name: 'Child',
      rootId, parentId: rootId,
      depth: 1, order: 0,
      status: { state: 'done' },
      progress: { percent: 100 },
      cancellable: false,
      log: [],
    });
    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.length > 0);
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const procs = events[0].processes;
    expect(procs).toHaveLength(2);
    expect(procs[0].rootId).toBe(rootId.toString());
    expect(procs[1].rootId).toBe(rootId.toString());
  });
});

describe('browserOpenRequests propagation', () => {
  it('createTreePoller includes browserOpenRequests in the update payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      browserOpenRequests: [{ requestId: 'r1', url: 'https://x' }],
      cancellable: true,
      log: [],
    });
    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();
    const update = events.find((e) => e.type === 'update');
    expect(update.processes[0].browserOpenRequests).toEqual([{ requestId: 'r1', url: 'https://x' }]);
  });

  it('createListPoller includes browserOpenRequests in the update payload', async () => {
    const events: any[] = [];
    const id = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: id, processId: 'p2', name: 'P2', rootId: id, parentId: null,
      depth: 0, order: 0, status: { state: 'running' }, progress: { percent: null },
      browserOpenRequests: [{ requestId: 'r2', url: 'https://y' }], cancellable: true, log: [],
    });
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();
    const update = events.find((e) => e.type === 'update');
    const p2 = update.processes.find((p: any) => p.processId === 'p2');
    expect(p2.browserOpenRequests).toEqual([{ requestId: 'r2', url: 'https://y' }]);
  });
});

describe('autoResumeScheduled propagation', () => {
  it('createTreePoller includes autoResumeScheduled in the update payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'par', name: 'PAR',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'cancelled' },
      progress: { percent: null },
      hasSavedState: true,
      autoResumeScheduled: true,
      cancellable: true,
      log: [],
    });
    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();
    const update = events.find((e) => e.type === 'update');
    expect(update.processes[0].autoResumeScheduled).toBe(true);
  });
});
