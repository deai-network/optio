import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import {
  getProcess, getProcessTree, getProcessLog, getProcessTreeLog,
  listProcesses, launchProcess, cancelProcess, dismissProcess, resurrectProcess, resyncProcesses,
} from '../handlers.js';
import type { OptioContext } from '../context.js';
import type { Access } from '../auth.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_handlers_access';
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
});

function makeEngine() {
  const ok = (params: any) => ({
    ok: true,
    process: {
      _id: new ObjectId(), processId: params.processId, rootId: new ObjectId(),
      name: 'P', status: { state: 'scheduled' },
    },
  });
  return {
    launch: vi.fn(ok),
    cancel: vi.fn(ok),
    dismiss: vi.fn(ok),
    resurrect: vi.fn(ok),
    resync: vi.fn(async () => undefined),
  };
}

function makeCtx(engine: any): OptioContext {
  const fakeRpc: any = {
    async call(_service: string, method: string, params: any) { return engine[method](params); },
    async notify(_service: string, method: string, params: any) { return engine[method](params); },
  };
  return {
    dbOpts: { db },
    redis: {},
    transports: { get: () => fakeRpc, closeAll: async () => {} },
    closeAll: async () => {},
  } as any;
}

function scoped(customerId: number, allow = true): Access {
  return { restricted: true, scope: { customerId }, authorize: vi.fn(async () => allow) };
}

async function insertTree(customerId: number) {
  const rootId = new ObjectId();
  const childId = new ObjectId();
  const base = { status: { state: 'idle' }, progress: { percent: null }, cancellable: true, order: 0 };
  await col().insertMany([
    {
      _id: rootId, processId: `root-${customerId}`, name: `Root ${customerId}`, rootId, parentId: null,
      depth: 0, metadata: { customerId }, log: [{ timestamp: new Date(1).toISOString(), level: 'info', message: 'r' }],
      ...base,
    },
    {
      _id: childId, processId: `child-${customerId}`, name: `Child ${customerId}`, rootId, parentId: rootId,
      depth: 1, metadata: { customerId }, log: [{ timestamp: new Date(2).toISOString(), level: 'info', message: 'c' }],
      ...base,
    },
  ] as any[]);
  return { rootId, childId };
}

describe('read handlers under a scope', () => {
  it('listProcesses returns only in-scope processes, even with a contradicting client filter', async () => {
    await insertTree(0);
    await insertTree(1);
    const ctx = makeCtx(makeEngine());
    const all = await listProcesses(ctx, { prefix: PREFIX, limit: 50 });
    expect(all.totalCount).toBe(4);
    const mine = await listProcesses(ctx, { prefix: PREFIX, limit: 50 }, scoped(1));
    expect(mine.totalCount).toBe(2);
    expect(mine.items.every((p: any) => p.metadata.customerId === 1)).toBe(true);
    const sneaky = await listProcesses(
      ctx, { prefix: PREFIX, limit: 50, metadataFilter: { customerId: 0 } }, scoped(1),
    );
    expect(sneaky.totalCount).toBe(0);
  });

  it('single-process reads hide out-of-scope processes as not found', async () => {
    const other = await insertTree(0);
    const mine = await insertTree(1);
    const ctx = makeCtx(makeEngine());
    const q = { prefix: PREFIX, limit: 50 };
    for (const id of [other.rootId.toHexString(), 'root-0', other.childId.toHexString()]) {
      expect(await getProcess(ctx, q, id, scoped(1))).toBeNull();
      expect(await getProcessTree(ctx, q, id, scoped(1))).toBeNull();
      expect(await getProcessLog(ctx, q, id, scoped(1))).toBeNull();
      expect(await getProcessTreeLog(ctx, q, id, scoped(1))).toBeNull();
    }
    expect(await getProcess(ctx, q, mine.rootId.toHexString(), scoped(1))).not.toBeNull();
    expect((await getProcessTree(ctx, q, 'root-1', scoped(1)))?.children).toHaveLength(1);
    expect((await getProcessLog(ctx, q, 'child-1', scoped(1)))?.totalCount).toBe(1);
    expect((await getProcessTreeLog(ctx, q, 'root-1', scoped(1)))?.totalCount).toBe(2);
  });
});

describe('command handlers under a scope', () => {
  const commands = [
    ['launch', (ctx: OptioContext, id: string, a?: Access) => launchProcess(ctx, { prefix: PREFIX }, id, false, null, a)],
    ['cancel', (ctx: OptioContext, id: string, a?: Access) => cancelProcess(ctx, { prefix: PREFIX }, id, a)],
    ['dismiss', (ctx: OptioContext, id: string, a?: Access) => dismissProcess(ctx, { prefix: PREFIX }, id, a)],
    ['resurrect', (ctx: OptioContext, id: string, a?: Access) => resurrectProcess(ctx, { prefix: PREFIX }, id, null, a)],
  ] as const;

  for (const [name, run] of commands) {
    it(`${name}: 404 not-found for an out-of-scope process, without calling the engine`, async () => {
      await insertTree(0);
      const engine = makeEngine();
      const result: any = await run(makeCtx(engine), 'root-0', scoped(1));
      expect(result).toEqual({ status: 404, body: { reason: 'not-found', message: 'Process not found' } });
      expect((engine as any)[name]).not.toHaveBeenCalled();
    });

    it(`${name}: 403 when authorize denies, without calling the engine`, async () => {
      await insertTree(1);
      const engine = makeEngine();
      const a = scoped(1, false);
      const result: any = await run(makeCtx(engine), 'root-1', a);
      expect(result).toEqual({ status: 403, body: { message: 'Forbidden' } });
      expect(a.authorize).toHaveBeenCalledWith(expect.objectContaining({ action: name }));
      expect((engine as any)[name]).not.toHaveBeenCalled();
    });

    it(`${name}: forwards an allowed in-scope process to the engine with the id as given`, async () => {
      await insertTree(1);
      const engine = makeEngine();
      const result: any = await run(makeCtx(engine), 'root-1', scoped(1));
      expect(result.status).toBe(200);
      expect((engine as any)[name]).toHaveBeenCalledWith(expect.objectContaining({ processId: 'root-1' }));
    });

    it(`${name}: unchanged without access (no lookup, engine decides)`, async () => {
      const engine = makeEngine();
      const result: any = await run(makeCtx(engine), 'not-in-db');
      expect(result.status).toBe(200);
      expect((engine as any)[name]).toHaveBeenCalledTimes(1);
    });
  }
});

describe('resurrectProcess', () => {
  it('asks authorize with action resurrect and the process', async () => {
    await insertTree(1);
    const engine = makeEngine();
    const access = scoped(1);
    const result: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', 's1', access);
    expect(result.status).toBe(200);
    expect(access.authorize).toHaveBeenCalledWith(expect.objectContaining({
      action: 'resurrect',
      process: expect.objectContaining({ processId: 'root-1' }),
    }));
    expect(engine.resurrect).toHaveBeenCalledWith({ processId: 'root-1', sessionId: 's1' });
  });

  it('maps engine reasons to 404/409', async () => {
    await insertTree(1);
    const engine = makeEngine();
    engine.resurrect = vi.fn(() => ({ ok: false, reason: 'not-resurrectable' })) as any;
    const r1: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', null);
    expect(r1.status).toBe(409);
    expect(r1.body.reason).toBe('not-resurrectable');
    engine.resurrect = vi.fn(() => ({ ok: false, reason: 'not-found' })) as any;
    const r2: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', null);
    expect(r2.status).toBe(404);
  });
});

describe('resyncProcesses', () => {
  it('returns 202 and forwards clean + filter unchanged without access', async () => {
    const engine = makeEngine();
    const result = await resyncProcesses(makeCtx(engine), { prefix: PREFIX }, true, { dataspace: 'a' });
    expect(result).toEqual({ status: 202, body: { message: 'Nuke and resync requested' } });
    expect(engine.resync).toHaveBeenCalledWith({ clean: true, metadataFilter: { dataspace: 'a' } });
  });

  it('narrows the filter to the scope', async () => {
    const engine = makeEngine();
    const result = await resyncProcesses(
      makeCtx(engine), { prefix: PREFIX }, false, { dataspace: 'a' }, scoped(1),
    );
    expect(result.status).toBe(202);
    expect(engine.resync).toHaveBeenCalledWith({ clean: false, metadataFilter: { dataspace: 'a', customerId: 1 } });
  });

  it('asks authorize with the effective filter and the clean flag; 403 on deny', async () => {
    const engine = makeEngine();
    const a = scoped(1, false);
    const result = await resyncProcesses(makeCtx(engine), { prefix: PREFIX }, true, { dataspace: 'a' }, a);
    expect(result).toEqual({ status: 403, body: { message: 'Forbidden' } });
    expect(a.authorize).toHaveBeenCalledWith({
      action: 'resync', clean: true, metadataFilter: { dataspace: 'a', customerId: 1 },
    });
    expect(engine.resync).not.toHaveBeenCalled();
  });

  it('403s a filter that contradicts the scope and 400s a predicate tree', async () => {
    const engine = makeEngine();
    const contradict = await resyncProcesses(makeCtx(engine), { prefix: PREFIX }, false, { customerId: 0 }, scoped(1));
    expect(contradict.status).toBe(403);
    const tree = await resyncProcesses(
      makeCtx(engine), { prefix: PREFIX }, false, { dataspace: { eq: 'a' } } as any, scoped(1),
    );
    expect(tree.status).toBe(400);
    expect(engine.resync).not.toHaveBeenCalled();
  });
});
