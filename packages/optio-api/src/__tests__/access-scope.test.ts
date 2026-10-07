import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import {
  andScope,
  findScopedProcess,
  gateInstances,
  gateProcess,
  inScope,
  scopedResyncFilter,
  scopeToMongo,
  toProcessRef,
} from '../access-scope.js';
import { UNRESTRICTED, accessFor, checkAuth, resolveAccess, type Access } from '../auth.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_access_scope';
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

async function insertProcess(fields: Record<string, unknown> = {}) {
  const _id = (fields._id as ObjectId) ?? new ObjectId();
  const doc = {
    _id,
    processId: `p-${_id.toHexString()}`,
    name: 'P',
    rootId: _id,
    parentId: null,
    depth: 0,
    order: 0,
    status: { state: 'idle' },
    log: [],
    metadata: {},
    ...fields,
  };
  await col().insertOne(doc as any);
  return doc as any;
}

function access(scope: Access['scope'], allow: boolean | ((input: any) => boolean) = true): Access {
  const authorize = vi.fn(async (input: any) => (typeof allow === 'function' ? allow(input) : allow));
  return { restricted: true, scope, authorize };
}

describe('scopeToMongo / andScope', () => {
  it('maps a scope to metadata-prefixed equality', () => {
    expect(scopeToMongo({ customerId: 1, tier: 'gold' })).toEqual({
      'metadata.customerId': 1,
      'metadata.tier': 'gold',
    });
  });

  it('returns the base filter untouched without a scope', () => {
    const base = { 'metadata.dataspace': 'x' };
    expect(andScope(base, null)).toBe(base);
  });

  it('uses the scope alone when the base filter is empty', () => {
    expect(andScope({}, { customerId: 1 })).toEqual({ 'metadata.customerId': 1 });
  });

  it('ANDs scope with a base filter so a client key cannot widen it', () => {
    expect(andScope({ 'metadata.customerId': 0 }, { customerId: 1 })).toEqual({
      $and: [{ 'metadata.customerId': 0 }, { 'metadata.customerId': 1 }],
    });
  });
});

describe('inScope', () => {
  it('is true for any process without a scope', async () => {
    const p = await insertProcess({ metadata: { customerId: 0 } });
    expect(await inScope(col(), p, null)).toBe(true);
  });

  it('compares every scope key with the process metadata', async () => {
    const p = await insertProcess({ metadata: { customerId: 1, dataspace: 'a' } });
    expect(await inScope(col(), p, { customerId: 1 })).toBe(true);
    expect(await inScope(col(), p, { customerId: 0 })).toBe(false);
    expect(await inScope(col(), p, { customerId: '1' })).toBe(false);
  });

  it('judges a child without the scope keys by its root', async () => {
    const root = await insertProcess({ metadata: { customerId: 1 } });
    const child = await insertProcess({ rootId: root._id, parentId: root._id, depth: 1, metadata: {} });
    expect(await inScope(col(), child, { customerId: 1 })).toBe(true);
    expect(await inScope(col(), child, { customerId: 2 })).toBe(false);
  });

  it('fails closed when neither the process nor its root carries the keys', async () => {
    const root = await insertProcess({ metadata: {} });
    const child = await insertProcess({ rootId: root._id, parentId: root._id, depth: 1, metadata: {} });
    expect(await inScope(col(), root, { customerId: 1 })).toBe(false);
    expect(await inScope(col(), child, { customerId: 1 })).toBe(false);
  });
});

describe('findScopedProcess', () => {
  it('finds by ObjectId or processId inside the scope', async () => {
    const p = await insertProcess({ metadata: { customerId: 1 } });
    expect((await findScopedProcess(col(), p._id.toHexString(), { customerId: 1 }))?._id).toEqual(p._id);
    expect((await findScopedProcess(col(), p.processId, { customerId: 1 }))?._id).toEqual(p._id);
  });

  it('returns null outside the scope, as for a missing process', async () => {
    const p = await insertProcess({ metadata: { customerId: 0 } });
    expect(await findScopedProcess(col(), p._id.toHexString(), { customerId: 1 })).toBeNull();
    expect(await findScopedProcess(col(), new ObjectId().toHexString(), { customerId: 1 })).toBeNull();
  });
});

describe('gateProcess', () => {
  it('skips the lookup entirely for unrestricted access', async () => {
    const result = await gateProcess(col(), 'does-not-exist', UNRESTRICTED, 'launch');
    expect(result).toEqual({ ok: true, doc: null });
  });

  it('404s a missing or out-of-scope process without asking authorize', async () => {
    const other = await insertProcess({ metadata: { customerId: 0 } });
    const a = access({ customerId: 1 });
    expect(await gateProcess(col(), other._id.toHexString(), a, 'launch')).toEqual({ ok: false, status: 404 });
    expect(await gateProcess(col(), 'nope', a, 'launch')).toEqual({ ok: false, status: 404 });
    expect(a.authorize).not.toHaveBeenCalled();
  });

  it('403s when authorize denies, passing the action and a process ref', async () => {
    const p = await insertProcess({ metadata: { customerId: 1, requiredRoles: ['admin'] } });
    const a = access({ customerId: 1 }, false);
    expect(await gateProcess(col(), p.processId, a, 'cancel')).toEqual({ ok: false, status: 403 });
    expect(a.authorize).toHaveBeenCalledWith({ action: 'cancel', process: toProcessRef(p) });
  });

  it('passes an in-scope process that authorize allows', async () => {
    const p = await insertProcess({ metadata: { customerId: 1 } });
    const result = await gateProcess(col(), p._id.toHexString(), access({ customerId: 1 }), 'dismiss');
    expect(result.ok).toBe(true);
  });

  it('resolves a duplicated processId to the newest document', async () => {
    await insertProcess({ processId: 'dup', metadata: { customerId: 0 } });
    await insertProcess({ processId: 'dup', metadata: { customerId: 1 } });
    const result = await gateProcess(col(), 'dup', access({ customerId: 1 }), 'launch');
    expect(result.ok).toBe(true);
  });
});

describe('toProcessRef', () => {
  it('exposes ids, name and metadata only', async () => {
    const root = await insertProcess({ metadata: { customerId: 1 } });
    const child = await insertProcess({
      rootId: root._id, parentId: root._id, depth: 1, name: 'Child',
      metadata: { customerId: 1, kind: 'x' },
      widgetUpstream: { url: 'http://secret' },
    });
    expect(toProcessRef(child)).toEqual({
      _id: child._id.toHexString(),
      processId: child.processId,
      name: 'Child',
      parentId: root._id.toHexString(),
      rootId: root._id.toHexString(),
      metadata: { customerId: 1, kind: 'x' },
    });
  });
});

describe('scopedResyncFilter', () => {
  it('passes the filter through without a scope', () => {
    expect(scopedResyncFilter({ AND: [] } as any, null)).toEqual({ ok: true, filter: { AND: [] } });
    expect(scopedResyncFilter(undefined, null)).toEqual({ ok: true, filter: undefined });
  });

  it('narrows a missing or empty filter to the scope', () => {
    expect(scopedResyncFilter(undefined, { customerId: 1 })).toEqual({ ok: true, filter: { customerId: 1 } });
    expect(scopedResyncFilter({}, { customerId: 1 })).toEqual({ ok: true, filter: { customerId: 1 } });
  });

  it('merges the scope into a flat filter', () => {
    expect(scopedResyncFilter({ dataspace: 'a' }, { customerId: 1 })).toEqual({
      ok: true, filter: { dataspace: 'a', customerId: 1 },
    });
    expect(scopedResyncFilter({ customerId: 1, dataspace: 'a' }, { customerId: 1 })).toEqual({
      ok: true, filter: { customerId: 1, dataspace: 'a' },
    });
  });

  it('403s a flat filter that contradicts the scope', () => {
    expect(scopedResyncFilter({ customerId: 0 }, { customerId: 1 })).toMatchObject({ ok: false, status: 403 });
  });

  it('400s a predicate-tree filter under a scope', () => {
    expect(scopedResyncFilter({ dataspace: { eq: 'a' } } as any, { customerId: 1 })).toMatchObject({
      ok: false, status: 400,
    });
  });
});

describe('gateInstances', () => {
  it('allows unrestricted access and asks authorize otherwise', async () => {
    expect(await gateInstances(UNRESTRICTED)).toBe(true);
    const deny = access(null, false);
    expect(await gateInstances(deny)).toBe(false);
    expect(deny.authorize).toHaveBeenCalledWith({ action: 'instances' });
  });
});

describe('resolveAccess / accessFor', () => {
  it('is unrestricted without hooks', async () => {
    const req = {};
    await checkAuth(req, () => 'operator', false);
    expect(await accessFor(req, {})).toBe(UNRESTRICTED);
  });

  it('binds the hooks to the request and the authenticated role', async () => {
    const req = { user: 'u1' };
    const scope = vi.fn(() => ({ customerId: 7 }));
    const authorize = vi.fn(() => true);
    await checkAuth(req, () => 'viewer', false);
    const a = await accessFor(req, { scope, authorize });
    expect(a.restricted).toBe(true);
    expect(a.scope).toEqual({ customerId: 7 });
    expect(scope).toHaveBeenCalledWith(req);
    await a.authorize({ action: 'resync', clean: true });
    expect(authorize).toHaveBeenCalledWith(req, { action: 'resync', clean: true, role: 'viewer' });
  });

  it('resolves the scope once per request', async () => {
    const req = {};
    const scope = vi.fn(async () => ({ customerId: 1 }));
    await checkAuth(req, () => 'operator', false);
    await accessFor(req, { scope });
    await accessFor(req, { scope });
    expect(scope).toHaveBeenCalledTimes(1);
  });

  it('treats a null or empty scope as unscoped but still restricted by authorize', async () => {
    const a = await resolveAccess({ scope: () => null, authorize: () => false }, {}, 'operator');
    expect(a.restricted).toBe(true);
    expect(a.scope).toBeNull();
    expect(await a.authorize({ action: 'instances' })).toBe(false);
    const b = await resolveAccess({ scope: () => ({}) }, {}, 'operator');
    expect(b.scope).toBeNull();
  });

  it('refuses to resolve access for a request that was never authenticated', () => {
    expect(() => accessFor({}, { scope: () => null })).toThrow(/before checkAuth/);
  });
});
