import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Collection, type Db } from 'mongodb';
import { or, not, eq, isIn } from 'optio-contracts';
import {
  normalizeVisibility,
  andVisibility,
  isVisible,
  pruneOrphans,
} from '../visibility.js';
import { metadataFilterToMongo } from '../metadata-filter-query.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_visibility';
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

describe('normalizeVisibility', () => {
  it('treats null, undefined and {} as no restriction', () => {
    expect(normalizeVisibility(null)).toBeNull();
    expect(normalizeVisibility(undefined)).toBeNull();
    expect(normalizeVisibility({})).toBeNull();
  });

  it('returns a predicate-tree value unchanged', () => {
    const predicate = or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'));
    expect(normalizeVisibility(predicate)).toEqual(predicate);
  });

  it('returns a legacy flat map unchanged', () => {
    const predicate = { ownerId: 'u1' };
    expect(normalizeVisibility(predicate)).toEqual(predicate);
  });

  it('throws on an invalid predicate', () => {
    expect(() => normalizeVisibility({ AND: [] })).toThrow(
      /^optio-api: the visibility hook returned an invalid metadata predicate/,
    );
  });

  // I1: `eq('ownerId', undefined)` passes the zod schema (`.optional()` on
  // every leaf op), then reaches the Mongo driver as `$eq: undefined`, which
  // the driver sends as null -- and `$eq: null` matches a missing key. A
  // leaf with an `undefined` value must never reach `andVisibility`, however
  // deep it is nested, so it is rejected here instead.
  it('rejects undefined on a leaf operator nested inside or(...), naming the field', () => {
    const predicate = or(not(eq('kind', 'data-chat')), eq('ownerId', undefined as any));
    expect(() => normalizeVisibility(predicate)).toThrow(
      /^optio-api: the visibility hook returned an invalid metadata predicate:.*ownerId/,
    );
  });

  it('rejects undefined in a legacy flat map, naming the field', () => {
    expect(() => normalizeVisibility({ ownerId: undefined })).toThrow(
      /^optio-api: the visibility hook returned an invalid metadata predicate:.*ownerId/,
    );
  });

  it('rejects undefined inside an `in` array', () => {
    const predicate = isIn('ownerId', [undefined as any, 'u1']);
    expect(() => normalizeVisibility(predicate)).toThrow(
      /^optio-api: the visibility hook returned an invalid metadata predicate:.*ownerId/,
    );
  });

  it('accepts a leaf null value (matches a missing key too, per MongoDB semantics)', () => {
    const predicate = eq('ownerId', null);
    expect(normalizeVisibility(predicate)).toEqual(predicate);
  });
});

describe('andVisibility', () => {
  it('returns the base filter untouched without a visibility predicate', () => {
    expect(andVisibility({ a: 1 }, null)).toEqual({ a: 1 });
  });

  it('uses the condition alone when the base filter is empty', () => {
    const predicate = eq('k', 'v');
    expect(andVisibility({}, predicate)).toEqual(metadataFilterToMongo(predicate));
  });

  it('ANDs the condition with a base filter', () => {
    const predicate = eq('k', 'v');
    expect(andVisibility({ a: 1 }, predicate)).toEqual({
      $and: [{ a: 1 }, metadataFilterToMongo(predicate)],
    });
  });
});

describe('isVisible', () => {
  const predicate = or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'));

  it('is true for a process the predicate admits', async () => {
    const p = await insertProcess({ metadata: { kind: 'data-chat', ownerId: 'u1' } });
    expect(await isVisible(col(), p, predicate)).toBe(true);
  });

  it('is false for a process owned by someone else', async () => {
    const p = await insertProcess({ metadata: { kind: 'data-chat', ownerId: 'u2' } });
    expect(await isVisible(col(), p, predicate)).toBe(false);
  });

  it('fails closed when the process has no owner at all', async () => {
    const p = await insertProcess({ metadata: { kind: 'data-chat' } });
    expect(await isVisible(col(), p, predicate)).toBe(false);
  });

  it('is true for a process the chat rule does not apply to', async () => {
    const empty = await insertProcess({ metadata: {} });
    const source = await insertProcess({ metadata: { kind: 'source' } });
    expect(await isVisible(col(), empty, predicate)).toBe(true);
    expect(await isVisible(col(), source, predicate)).toBe(true);
  });

  it('works with a legacy flat map as equality', async () => {
    const legacy = { ownerId: 'u1' };
    const owned = await insertProcess({ metadata: { ownerId: 'u1' } });
    const other = await insertProcess({ metadata: { ownerId: 'u2' } });
    expect(await isVisible(col(), owned, legacy)).toBe(true);
    expect(await isVisible(col(), other, legacy)).toBe(false);
  });

  it('is true without issuing a query when visibility is null', async () => {
    const doc = { _id: new ObjectId() };
    const stub = {
      countDocuments: () => {
        throw new Error('countDocuments should not be called for null visibility');
      },
    } as unknown as Collection;
    await expect(isVisible(stub, doc, null)).resolves.toBe(true);
  });
});

describe('pruneOrphans', () => {
  function tree() {
    const root = { _id: new ObjectId(), parentId: null };
    const child = { _id: new ObjectId(), parentId: root._id };
    const grandchild = { _id: new ObjectId(), parentId: child._id };
    const orphan = { _id: new ObjectId(), parentId: new ObjectId() }; // parent absent
    return { root, child, grandchild, orphan };
  }

  it('keeps every process whose parent chain resolves to a root, drops the orphan', () => {
    const { root, child, grandchild, orphan } = tree();
    const docs = [root, child, grandchild, orphan];
    expect(pruneOrphans(docs)).toEqual([root, child, grandchild]);
  });

  it('drops a grandchild whose parent was itself removed from the input', () => {
    const { root, grandchild, orphan } = tree();
    const docs = [root, grandchild, orphan];
    expect(pruneOrphans(docs)).toEqual([root]);
  });

  it('keeps an orphan explicitly named in `keep`', () => {
    const { root, child, grandchild, orphan } = tree();
    const docs = [root, child, grandchild, orphan];
    const keep = new Set([orphan._id.toString()]);
    expect(pruneOrphans(docs, keep)).toEqual([root, child, grandchild, orphan]);
  });

  it('preserves input order', () => {
    const { root, child, grandchild, orphan } = tree();
    const docs = [grandchild, orphan, root, child];
    expect(pruneOrphans(docs)).toEqual([grandchild, root, child]);
  });
});
