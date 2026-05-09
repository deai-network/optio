import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
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

function makeCtx(database: Db, redis: any = fakeRedis): OptioContext {
  return createOptioContext({ dbOpts: { db: database }, redis });
}

// ctx with a stubbed engine cache, for command-handler tests that
// exercise the engine.launch RPC path. The cache returns the same
// stub for every (database, prefix) pair.
function makeCtxWithMockEngine(database: Db, mockEngine: any): OptioContext {
  return {
    dbOpts: { db: database },
    redis: fakeRedis,
    engineCache: {
      get: () => mockEngine,
      closeAll: async () => {},
    },
  } as any;
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

describe('launchProcess — pre-checks + engine RPC', () => {
  // Builds a mock engine. By default `launch` resolves with
  // `{ ok: true, process: <minimal-process> }` so the 200 path works
  // without per-test setup. Tests that need a failure response pass
  // an override via `launchImpl`.
  function makeMockEngine(launchImpl?: (params: any) => any) {
    return {
      launch: vi.fn(launchImpl ?? ((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'scheduled' },
          progress: { percent: null },
          cancellable: true,
          log: [],
          supportsResume: false,
          hasSavedState: false,
        },
      }))),
    };
  }

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

  it('200 on success: returns toResponse(result.process) and calls engine.launch with {processId, resume}', async () => {
    const id = await insertLaunchable({ processId: 'p' });
    const engine = makeMockEngine();
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id, false);
    expect(result.status).toBe(200);
    expect(engine.launch).toHaveBeenCalledTimes(1);
    expect(engine.launch).toHaveBeenCalledWith({ processId: 'p', resume: false });
    expect((result as any).body._id).toBeDefined();
  });

  it('forwards resume=true to engine.launch when task supportsResume', async () => {
    const id = await insertLaunchable({ processId: 'q', supportsResume: true });
    const engine = makeMockEngine();
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id, true);
    expect(result.status).toBe(200);
    expect(engine.launch).toHaveBeenCalledWith({ processId: 'q', resume: true });
  });

  it('404 not-found from pre-check: engine.launch never called', async () => {
    const engine = makeMockEngine();
    const fakeId = new ObjectId().toString();
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, fakeId);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({ reason: 'not-found', message: 'Process not found' });
    expect(engine.launch).not.toHaveBeenCalled();
  });

  it('409 not-launchable from pre-check (state=running): engine.launch never called', async () => {
    const id = await insertLaunchable({ status: { state: 'running' } });
    const engine = makeMockEngine();
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-launchable',
      message: 'Process is not in a launchable state',
    });
    expect(engine.launch).not.toHaveBeenCalled();
  });

  it('409 no-resume-support from pre-check: engine.launch never called', async () => {
    const id = await insertLaunchable({ supportsResume: false });
    const engine = makeMockEngine();
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id, true);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'no-resume-support',
      message: 'This task does not support resume',
    });
    expect(engine.launch).not.toHaveBeenCalled();
  });

  it('409 launch-blocked from engine: pre-check passes, engine returns ok=false reason=launch-blocked', async () => {
    const id = await insertLaunchable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'launch-blocked' }));
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'launch-blocked',
      message: 'Launches matching this filter are currently blocked',
    });
    expect(engine.launch).toHaveBeenCalledTimes(1);
  });

  it('404 not-found from engine (race): pre-check passes, engine returns ok=false reason=not-found', async () => {
    const id = await insertLaunchable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-found' }));
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({
      reason: 'not-found',
      message: 'Process not found',
    });
    expect(engine.launch).toHaveBeenCalledTimes(1);
  });
});

describe('cancelProcess — pre-checks + engine RPC', () => {
  // Builds a mock engine. By default `cancel` resolves with
  // `{ ok: true, process: <minimal-process> }` so the 200 path works
  // without per-test setup. Tests that need a failure response pass
  // an override via `cancelImpl`.
  function makeMockEngine(cancelImpl?: (params: any) => any) {
    return {
      cancel: vi.fn(cancelImpl ?? ((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'cancelled' },
          progress: { percent: null },
          cancellable: true,
          log: [],
        },
      }))),
    };
  }

  async function insertCancellable(extra: Record<string, unknown> = {}) {
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
      ...extra,
    });
    return oid.toString();
  }

  it('200 on success: returns toResponse(result.process) and calls engine.cancel with {processId}', async () => {
    const id = await insertCancellable({ processId: 'p' });
    const engine = makeMockEngine();
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(200);
    expect(engine.cancel).toHaveBeenCalledTimes(1);
    expect(engine.cancel).toHaveBeenCalledWith({ processId: 'p' });
    expect((result as any).body._id).toBeDefined();
  });

  it('404 not-found from pre-check: engine.cancel never called', async () => {
    const engine = makeMockEngine();
    const fakeId = new ObjectId().toString();
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, fakeId);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({ reason: 'not-found', message: 'Process not found' });
    expect(engine.cancel).not.toHaveBeenCalled();
  });

  it('409 not-cancellable from pre-check (cancellable=false): engine.cancel never called', async () => {
    const id = await insertCancellable({ cancellable: false });
    const engine = makeMockEngine();
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-cancellable',
      message: 'Process is not cancellable in its current state',
    });
    expect(engine.cancel).not.toHaveBeenCalled();
  });

  it('409 not-cancellable from pre-check (state=idle): engine.cancel never called', async () => {
    const id = await insertCancellable({ status: { state: 'idle' } });
    const engine = makeMockEngine();
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-cancellable',
      message: 'Process is not cancellable in its current state',
    });
    expect(engine.cancel).not.toHaveBeenCalled();
  });

  it('409 not-cancellable from engine (race): pre-check passes, engine returns ok=false reason=not-cancellable', async () => {
    const id = await insertCancellable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-cancellable' }));
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-cancellable',
      message: 'Process is not cancellable in its current state',
    });
    expect(engine.cancel).toHaveBeenCalledTimes(1);
  });

  it('404 not-found from engine (race): pre-check passes, engine returns ok=false reason=not-found', async () => {
    const id = await insertCancellable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-found' }));
    const result = await cancelProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({
      reason: 'not-found',
      message: 'Process not found',
    });
    expect(engine.cancel).toHaveBeenCalledTimes(1);
  });
});

describe('dismissProcess — pre-checks + engine RPC', () => {
  // Builds a mock engine. By default `dismiss` resolves with
  // `{ ok: true, process: <minimal-process> }` so the 200 path works
  // without per-test setup. Tests that need a failure response pass
  // an override via `dismissImpl`.
  function makeMockEngine(dismissImpl?: (params: any) => any) {
    return {
      dismiss: vi.fn(dismissImpl ?? ((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'idle' },
          progress: { percent: null },
          cancellable: true,
          log: [],
        },
      }))),
    };
  }

  async function insertDismissable(extra: Record<string, unknown> = {}) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'done' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      ...extra,
    });
    return oid.toString();
  }

  it('200 on success: returns toResponse(result.process) and calls engine.dismiss with {processId}', async () => {
    const id = await insertDismissable({ processId: 'p' });
    const engine = makeMockEngine();
    const result = await dismissProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(200);
    expect(engine.dismiss).toHaveBeenCalledTimes(1);
    expect(engine.dismiss).toHaveBeenCalledWith({ processId: 'p' });
    expect((result as any).body._id).toBeDefined();
  });

  it('404 not-found from pre-check: engine.dismiss never called', async () => {
    const engine = makeMockEngine();
    const fakeId = new ObjectId().toString();
    const result = await dismissProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, fakeId);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({ reason: 'not-found', message: 'Process not found' });
    expect(engine.dismiss).not.toHaveBeenCalled();
  });

  it('409 not-dismissable from pre-check (state=running): engine.dismiss never called', async () => {
    const id = await insertDismissable({ status: { state: 'running' } });
    const engine = makeMockEngine();
    const result = await dismissProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-dismissable',
      message: 'Process is not in a dismissable state',
    });
    expect(engine.dismiss).not.toHaveBeenCalled();
  });

  it('409 not-dismissable from engine (race): pre-check passes, engine returns ok=false reason=not-dismissable', async () => {
    const id = await insertDismissable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-dismissable' }));
    const result = await dismissProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-dismissable',
      message: 'Process is not in a dismissable state',
    });
    expect(engine.dismiss).toHaveBeenCalledTimes(1);
  });

  it('404 not-found from engine (race): pre-check passes, engine returns ok=false reason=not-found', async () => {
    const id = await insertDismissable();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-found' }));
    const result = await dismissProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, id);
    expect(result.status).toBe(404);
    expect((result as any).body).toEqual({
      reason: 'not-found',
      message: 'Process not found',
    });
    expect(engine.dismiss).toHaveBeenCalledTimes(1);
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
    const { processIdString } = await insertProcess('idle');
    const engine = {
      launch: vi.fn((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'scheduled' },
          progress: { percent: null },
          cancellable: true,
          log: [],
        },
      })),
    };
    const ctx: OptioContext = {
      dbOpts: { db },
      redis: fakeRedis,
      engineCache: {
        get: () => engine,
        closeAll: async () => {},
      },
    } as any;
    const result = await launchProcess(ctx, { prefix: PREFIX }, processIdString);
    expect(result.status).toBe(200);
    expect(engine.launch).toHaveBeenCalledWith({ processId: processIdString, resume: false });
  });

  it('cancelProcess accepts the processId string form', async () => {
    const { processIdString } = await insertProcess('running');
    const engine = {
      cancel: vi.fn((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'cancelled' },
          progress: { percent: null },
          cancellable: true,
          log: [],
        },
      })),
    };
    const ctx: OptioContext = {
      dbOpts: { db },
      redis: fakeRedis,
      engineCache: {
        get: () => engine,
        closeAll: async () => {},
      },
    } as any;
    const result = await cancelProcess(ctx, { prefix: PREFIX }, processIdString);
    expect(result.status).toBe(200);
    expect(engine.cancel).toHaveBeenCalledWith({ processId: processIdString });
  });

  it('dismissProcess accepts the processId string form', async () => {
    const { processIdString } = await insertProcess('done');
    const engine = {
      dismiss: vi.fn((params: any) => ({
        ok: true,
        process: {
          _id: new ObjectId(),
          processId: params.processId,
          name: 'P',
          rootId: new ObjectId(),
          parentId: null,
          depth: 0,
          order: 0,
          status: { state: 'idle' },
          progress: { percent: null },
          cancellable: true,
          log: [],
        },
      })),
    };
    const ctx: OptioContext = {
      dbOpts: { db },
      redis: fakeRedis,
      engineCache: {
        get: () => engine,
        closeAll: async () => {},
      },
    } as any;
    const result = await dismissProcess(ctx, { prefix: PREFIX }, processIdString);
    expect(result.status).toBe(200);
    expect(engine.dismiss).toHaveBeenCalledWith({ processId: processIdString });
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
