import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { MongoClient, type Db } from 'mongodb';
import { metadataFilterToMongo } from '../metadata-filter-query.js';
import { and, or, eq, isIn } from 'optio-contracts';

// Connect to the running Docker MongoDB (per feedback_mongodb_docker.md).
// Do NOT use mongodb-memory-server.
const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = `optio-filter-it-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const COLL = 'processes';

let client: MongoClient;
let db: Db;

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
  await db.collection(COLL).insertMany([
    { _id: 'p1' as any, metadata: { tag: 'demo', owner: 'kris', region: 'us' } },
    { _id: 'p2' as any, metadata: { tag: 'demo', owner: 'sam',  region: 'eu' } },
    { _id: 'p3' as any, metadata: { tag: 'prod', owner: 'kris', region: 'us' } },
    { _id: 'p4' as any, metadata: { tag: 'prod', owner: 'sam',  region: 'jp' } },
    { _id: 'p5' as any, metadata: { tag: 'test', owner: 'kris', region: 'us' } },
  ]);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

async function matchedIds(query: Record<string, unknown>): Promise<string[]> {
  const docs = await db.collection(COLL).find(query).project({ _id: 1 }).toArray();
  return docs.map((d) => d._id as unknown as string).sort();
}

describe('metadataFilterToMongo integration with MongoDB', () => {
  it('(tag=demo AND owner=kris) OR (tag=prod AND region in [us,eu]) matches p1, p3', async () => {
    const filter = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    const mongo = metadataFilterToMongo(filter);
    expect(await matchedIds(mongo)).toEqual(['p1', 'p3']);
  });

  it('legacy flat shape { tag: "demo" } still matches p1, p2', async () => {
    const mongo = metadataFilterToMongo({ tag: 'demo' });
    expect(await matchedIds(mongo)).toEqual(['p1', 'p2']);
  });

  it('OR with three branches matches union', async () => {
    const filter = or(eq('owner', 'kris'), eq('region', 'jp'));
    const mongo = metadataFilterToMongo(filter);
    expect(await matchedIds(mongo)).toEqual(['p1', 'p3', 'p4', 'p5']);
  });
});
