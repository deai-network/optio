import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createTreePoller, createListPoller } from '../stream-poller.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_stream_poller';
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
    await new Promise((r) => setTimeout(r, 1100));
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
    await new Promise((r) => setTimeout(r, 1100));

    const before = events.filter((e) => e.type === 'update').length;
    expect(before).toBeGreaterThanOrEqual(1);

    await coll.updateOne(
      { _id: rootId },
      { $set: { widgetData: { v: 2 } } },
    );
    await new Promise((r) => setTimeout(r, 1100));
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
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].uiWidget).toBe('my-custom-widget');
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
    await new Promise((r) => setTimeout(r, 1100));
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
    await new Promise((r) => setTimeout(r, 1100));
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
    await new Promise((r) => setTimeout(r, 1100));
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
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes.length).toBe(1);
  });
});
