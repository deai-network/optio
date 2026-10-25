import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import {
  getProcess, getProcessTree, getProcessLog, getProcessTreeLog, getProcessConversation,
  listProcesses, launchProcess, cancelProcess, dismissProcess, resurrectProcess, resyncProcesses,
} from '../handlers.js';
import type { OptioContext } from '../context.js';
import type { Access } from '../auth.js';
import { or, not, eq } from 'optio-contracts';

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
  return { restricted: true, scope: { customerId }, visibility: null, authorize: vi.fn(async () => allow) };
}

/** An Access restricted only by `visibility`, with no scope; `authorize` always allows. */
function visible(predicate: NonNullable<Access['visibility']>): Access {
  return { restricted: true, scope: null, visibility: predicate, authorize: vi.fn(async () => true) };
}

/** Inserts one process doc with sane defaults; overrides supply the rest. */
async function insertProcess(overrides: {
  _id?: ObjectId;
  processId?: string;
  name?: string;
  rootId?: ObjectId;
  parentId?: ObjectId | null;
  depth?: number;
  metadata?: Record<string, unknown>;
  log?: { timestamp: string; level: string; message: string }[];
  taskFactory?: boolean;
} = {}) {
  const _id = overrides._id ?? new ObjectId();
  const rootId = overrides.rootId ?? _id;
  const doc = {
    _id,
    processId: overrides.processId ?? `p-${_id.toHexString()}`,
    name: overrides.name ?? 'P',
    rootId,
    parentId: overrides.parentId ?? null,
    depth: overrides.depth ?? 0,
    order: 0,
    status: { state: 'idle' },
    progress: { percent: null },
    cancellable: true,
    log: overrides.log ?? [],
    metadata: overrides.metadata ?? {},
    ...(overrides.taskFactory !== undefined ? { taskFactory: overrides.taskFactory } : {}),
  };
  await col().insertOne(doc as any);
  return doc;
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

// launchMetadata (auth.ts), bound by the adapter to the request: launchProcess
// takes it as its trailing `stamp`.
describe('launchProcess: the launch stamp', () => {
  const STAMP = { user: 'u1', n: 1, ok: true, none: null };
  const stampOf = () => vi.fn(async () => STAMP);
  const launch = (engine: any, id: string, access: Access | undefined, stamp: any) =>
    launchProcess(makeCtx(engine), { prefix: PREFIX }, id, false, null, access, { n: 3 }, undefined, stamp);
  const sent = (engine: any) => engine.launch.mock.calls[0][0];

  it('a factory record under access hooks: the stamp sees its ref and goes to the engine as metadata', async () => {
    const factory = await insertProcess({ processId: 'fac', taskFactory: true, metadata: { customerId: 1 } });
    const engine = makeEngine();
    const access = scoped(1);
    const stamp = stampOf();
    const result: any = await launch(engine, 'fac', access, stamp);
    expect(result.status).toBe(200);
    expect(stamp).toHaveBeenCalledTimes(1);
    expect(stamp).toHaveBeenCalledWith(expect.objectContaining({
      _id: factory._id.toHexString(), processId: 'fac', taskFactory: true, metadata: { customerId: 1 },
    }));
    expect(sent(engine)).toEqual({
      processId: 'fac', resume: false, sessionId: null, params: { n: 3 }, metadata: STAMP,
    });
    // authorize allowed the launch before the stamp was asked.
    const authorized = (access.authorize as any).mock.invocationCallOrder[0];
    expect(authorized).toBeLessThan(stamp.mock.invocationCallOrder[0]);
  });

  it('a factory record without access hooks: the target is looked up here, by either id form', async () => {
    const factory = await insertProcess({ processId: 'fac', taskFactory: true });
    for (const id of ['fac', factory._id.toHexString()]) {
      const engine = makeEngine();
      const stamp = stampOf();
      const result: any = await launch(engine, id, undefined, stamp);
      expect(result.status).toBe(200);
      expect(stamp).toHaveBeenCalledWith(expect.objectContaining({ processId: 'fac', taskFactory: true }));
      expect(sent(engine)).toEqual(expect.objectContaining({ processId: id, metadata: STAMP }));
    }
  });

  it('a plain record: the stamp is never called and no metadata key goes to the engine', async () => {
    await insertProcess({ processId: 'plain', metadata: { customerId: 1 } });
    await insertProcess({ processId: 'not-factory', taskFactory: false, metadata: { customerId: 1 } });
    for (const id of ['plain', 'not-factory']) {
      for (const access of [scoped(1), undefined]) {
        const engine = makeEngine();
        const stamp = stampOf();
        const result: any = await launch(engine, id, access, stamp);
        expect(result.status).toBe(200);
        expect(stamp).not.toHaveBeenCalled();
        expect(Object.keys(sent(engine))).not.toContain('metadata');
      }
    }
  });

  it('a target that is not there, without access hooks: no stamp, the engine decides', async () => {
    const engine = makeEngine();
    const stamp = stampOf();
    const result: any = await launch(engine, 'not-in-db', undefined, stamp);
    expect(result.status).toBe(200);
    expect(stamp).not.toHaveBeenCalled();
    expect(Object.keys(sent(engine))).not.toContain('metadata');
  });

  it('authorize refusing, or the factory out of scope: the stamp is not called', async () => {
    await insertProcess({ processId: 'fac', taskFactory: true, metadata: { customerId: 1 } });
    const engine = makeEngine();
    const stamp = stampOf();
    const denied: any = await launch(engine, 'fac', scoped(1, false), stamp);
    expect(denied).toEqual({ status: 403, body: { message: 'Forbidden' } });
    const outside: any = await launch(engine, 'fac', scoped(2), stamp);
    expect(outside.status).toBe(404);
    expect(stamp).not.toHaveBeenCalled();
    expect(engine.launch).not.toHaveBeenCalled();
  });

  it('a stamp answering null: no metadata key goes to the engine', async () => {
    await insertProcess({ processId: 'fac', taskFactory: true });
    const engine = makeEngine();
    const stamp = vi.fn(async () => null);
    const result: any = await launch(engine, 'fac', undefined, stamp);
    expect(result.status).toBe(200);
    expect(stamp).toHaveBeenCalledTimes(1);
    expect(Object.keys(sent(engine))).not.toContain('metadata');
  });
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

describe('read handlers under visibility', () => {
  // Excavator's rule, from the spec: every process that isn't a data-chat, plus
  // the requester's own data-chats. A missing `kind` or `ownerId` follows Mongo's
  // rules (fails closed for `eq`, matches for `not(eq(...))`).
  const PREDICATE = or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'));

  describe('listProcesses', () => {
    it('pages through only visible processes, one per page, in _id order, totalCount counting from the cursor on', async () => {
      const visibleIds: string[] = [];
      for (let i = 0; i < 3; i += 1) {
        const v = await insertProcess({ metadata: { kind: 'other', n: i } });
        visibleIds.push(v._id.toHexString());
        await insertProcess({ metadata: { kind: 'data-chat', ownerId: 'someone-else' } });
      }
      const ctx = makeCtx(makeEngine());
      const access = visible(PREDICATE);
      let cursor: string | undefined;
      const collected: string[] = [];
      // totalCount counts the still-matching (visible) processes from the cursor on, same as
      // today's (visibility-free) cursor semantics: 3 on the first page, then 2, then 1 --
      // never inflated by the hidden processes interleaved between them.
      const expectedTotals = [3, 2, 1];
      for (let i = 0; i < 5; i += 1) {
        const page: any = await listProcesses(ctx, { prefix: PREFIX, limit: 1, cursor }, access);
        expect(page.items).toHaveLength(1);
        expect(page.totalCount).toBe(expectedTotals[i]);
        collected.push(page.items[0]._id);
        if (!page.nextCursor) break;
        cursor = page.nextCursor;
      }
      expect(collected).toEqual(visibleIds);
    });

    it('a client metadataFilter cannot widen past visibility (Review focus 4)', async () => {
      const visibleChat = await insertProcess({ metadata: { kind: 'data-chat', ownerId: 'u1' } });
      await insertProcess({ metadata: { kind: 'data-chat', ownerId: 'someone-else' } });
      const ctx = makeCtx(makeEngine());
      const result: any = await listProcesses(
        ctx,
        { prefix: PREFIX, limit: 50, metadataFilter: or(eq('kind', 'data-chat')) },
        visible(PREDICATE),
      );
      expect(result.totalCount).toBe(1);
      expect(result.items).toHaveLength(1);
      expect(result.items[0]._id).toBe(visibleChat._id.toHexString());
    });
  });

  describe('getProcess and getProcessLog', () => {
    it('return null for a hidden process by _id and by processId; return the process for a visible one', async () => {
      const hidden = await insertProcess({ processId: 'hidden-1', metadata: { kind: 'data-chat' } });
      const vis = await insertProcess({ processId: 'visible-1', metadata: { kind: 'other' } });
      const ctx = makeCtx(makeEngine());
      const access = visible(PREDICATE);
      const q = { prefix: PREFIX, limit: 50 };
      for (const id of [hidden._id.toHexString(), 'hidden-1']) {
        expect(await getProcess(ctx, q, id, access)).toBeNull();
        expect(await getProcessLog(ctx, q, id, access)).toBeNull();
      }
      expect(await getProcess(ctx, q, vis._id.toHexString(), access)).not.toBeNull();
      expect(await getProcess(ctx, q, 'visible-1', access)).not.toBeNull();
      expect(await getProcessLog(ctx, q, 'visible-1', access)).not.toBeNull();
    });
  });

  describe('getProcessTree and getProcessTreeLog', () => {
    // root: visible. child: hidden. grandchild: visible on its own metadata,
    // diverged from its (hidden) parent -- the spec's documented edge case.
    // sibling: a second, directly-visible child of root (T3#4: a positive
    // case -- without it, a regression that empties every tree under
    // visibility would pass these tests unnoticed, since every other node
    // here is expected to disappear).
    async function insertFixture() {
      const root = await insertProcess({
        metadata: { kind: 'other' },
        log: [{ timestamp: new Date(1).toISOString(), level: 'info', message: 'root' }],
      });
      const child = await insertProcess({
        rootId: root._id, parentId: root._id, depth: 1,
        metadata: { kind: 'data-chat', ownerId: 'someone-else' },
        log: [{ timestamp: new Date(2).toISOString(), level: 'info', message: 'child' }],
      });
      const grandchild = await insertProcess({
        rootId: root._id, parentId: child._id, depth: 2,
        metadata: { kind: 'other' },
        log: [{ timestamp: new Date(3).toISOString(), level: 'info', message: 'grandchild' }],
      });
      const sibling = await insertProcess({
        rootId: root._id, parentId: root._id, depth: 1,
        metadata: { kind: 'other' },
        log: [{ timestamp: new Date(4).toISOString(), level: 'info', message: 'sibling' }],
      });
      return { root, child, grandchild, sibling };
    }

    it('getProcessTree keeps the visible sibling and drops the hidden branch; a hidden root gives null', async () => {
      const { root, sibling } = await insertFixture();
      const ctx = makeCtx(makeEngine());
      const access = visible(PREDICATE);
      const q = { prefix: PREFIX, limit: 50 };
      const tree: any = await getProcessTree(ctx, q, root._id.toHexString(), access);
      expect(tree).not.toBeNull();
      expect(tree.children.map((c: any) => c._id)).toEqual([sibling._id.toString()]);
      expect(tree.children[0].children).toEqual([]);

      const hiddenRoot = await insertProcess({ metadata: { kind: 'data-chat' } });
      expect(await getProcessTree(ctx, q, hiddenRoot._id.toHexString(), access)).toBeNull();
    });

    it("getProcessTreeLog returns the root's and the visible sibling's log lines; a hidden entry gives null", async () => {
      const { root, child } = await insertFixture();
      const ctx = makeCtx(makeEngine());
      const access = visible(PREDICATE);
      const q = { prefix: PREFIX, limit: 50 };
      const log: any = await getProcessTreeLog(ctx, q, root._id.toHexString(), access);
      expect(log).not.toBeNull();
      expect(log.totalCount).toBe(2);
      expect(log.items.map((i: any) => i.message)).toEqual(['root', 'sibling']);

      expect(await getProcessTreeLog(ctx, q, child._id.toHexString(), access)).toBeNull();
    });
  });

  describe('getProcessConversation', () => {
    it('answers 404 not-found for a hidden process without calling authorize', async () => {
      const hidden = await insertProcess({ metadata: { kind: 'data-chat' } });
      const ctx = makeCtx(makeEngine());
      const access = visible(PREDICATE);
      const result: any = await getProcessConversation(ctx, { prefix: PREFIX }, hidden._id.toHexString(), access);
      expect(result.status).toBe(404);
      expect(result.body.reason).toBe('not-found');
      expect(access.authorize).not.toHaveBeenCalled();
    });
  });

  describe('pruning applies only when visibility is set', () => {
    it("without access, a tree log with a deleted middle parent keeps today's (orphan-inclusive) result", async () => {
      const root = await insertProcess({
        log: [{ timestamp: new Date(1).toISOString(), level: 'info', message: 'root' }],
      });
      const middle = await insertProcess({ rootId: root._id, parentId: root._id, depth: 1 });
      await insertProcess({
        rootId: root._id, parentId: middle._id, depth: 2,
        log: [{ timestamp: new Date(3).toISOString(), level: 'info', message: 'grandchild' }],
      });
      // The middle doc is gone; the grandchild's parentId now dangles.
      await col().deleteOne({ _id: middle._id });
      const ctx = makeCtx(makeEngine());
      const log: any = await getProcessTreeLog(ctx, { prefix: PREFIX, limit: 50 }, root._id.toHexString());
      expect(log.totalCount).toBe(2);
      expect(log.items.map((i: any) => i.message).sort()).toEqual(['grandchild', 'root']);
    });
  });
});
