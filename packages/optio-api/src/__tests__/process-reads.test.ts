import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Collection, type Db } from 'mongodb';
import { eq } from 'optio-contracts';
import { processReads, processReadsOf, processCollection } from '../process-reads.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_process_reads';
const PREFIX = 'test';

let client: MongoClient;
let db: Db;
const col = () => processCollection(db, PREFIX);

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

describe('processReads with a visibility predicate', () => {
  const visibility = eq('ownerId', 'u1');

  it('find({}) returns only the processes the predicate admits', async () => {
    const mine = await insertProcess({ metadata: { ownerId: 'u1' } });
    await insertProcess({ metadata: { ownerId: 'u2' } });
    const reads = processReads(db, PREFIX, visibility);
    const found = await reads.find({}).toArray();
    expect(found.map((d) => d._id.toString())).toEqual([mine._id.toString()]);
  });

  it('findOne by _id gives null for a hidden process', async () => {
    const other = await insertProcess({ metadata: { ownerId: 'u2' } });
    const reads = processReads(db, PREFIX, visibility);
    expect(await reads.findOne({ _id: other._id })).toBeNull();
  });

  it('countDocuments({}) counts only the visible processes', async () => {
    await insertProcess({ metadata: { ownerId: 'u1' } });
    await insertProcess({ metadata: { ownerId: 'u1' } });
    await insertProcess({ metadata: { ownerId: 'u2' } });
    const reads = processReads(db, PREFIX, visibility);
    expect(await reads.countDocuments({})).toBe(2);
  });

  it('ANDs a caller filter rather than replacing it: a client filter cannot widen past visibility', async () => {
    await insertProcess({ metadata: { ownerId: 'u1' } });
    await insertProcess({ metadata: { ownerId: 'u2' } });
    const reads = processReads(db, PREFIX, visibility);
    const found = await reads.find({ 'metadata.ownerId': 'u2' }).toArray();
    expect(found).toEqual([]);
  });
});

describe('processReads with null visibility', () => {
  it('passes the caller filter object through unchanged to find, findOne and countDocuments', async () => {
    const filterFind = { a: 1 };
    const filterFindOne = { b: 2 };
    const filterCount = { c: 3 };
    const calls: Record<string, unknown> = {};
    const stub = {
      find(filter: unknown) {
        calls.find = filter;
        return { toArray: async () => [] } as any;
      },
      findOne(filter: unknown) {
        calls.findOne = filter;
        return Promise.resolve(null);
      },
      countDocuments(filter: unknown) {
        calls.countDocuments = filter;
        return Promise.resolve(0);
      },
    } as unknown as Collection;

    const reads = processReadsOf(stub, null);
    reads.find(filterFind as any);
    await reads.findOne(filterFindOne as any);
    await reads.countDocuments(filterCount as any);

    expect(calls.find).toBe(filterFind);
    expect(calls.findOne).toBe(filterFindOne);
    expect(calls.countDocuments).toBe(filterCount);
  });
});

describe('processCollection', () => {
  it('names the {prefix}_processes collection', async () => {
    const inserted = await insertProcess();
    const found = await processCollection(db, PREFIX).findOne({ _id: inserted._id });
    expect(found?._id.toString()).toBe(inserted._id.toString());
  });
});
