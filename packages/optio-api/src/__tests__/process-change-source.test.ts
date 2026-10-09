import { describe, it, expect, afterAll } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import {
  ChangeStreamsUnavailable, createMongoProcessChangeSource,
} from '../process-change-hub.js';
import type { ProcessChange } from '../process-change-relevance.js';

// Spec: docs/2026-10-09-api-change-streams-design.md (When change streams are
// missing or break); the trimmed events: owner decision after the measurement.

/** A Db whose `hello` answers `hello`, and whose watch() cursor's first
 *  tryNext() resolves or rejects as given. */
function fakeDb(hello: Record<string, unknown>, firstTry: () => Promise<unknown>): Db {
  const stream = {
    tryNext: firstTry,
    next: () => new Promise(() => {}),
    close: async () => {},
  };
  return {
    admin: () => ({ command: async () => hello }),
    collection: () => ({ watch: () => stream }),
  } as unknown as Db;
}

describe('createMongoProcessChangeSource at open', () => {
  it('opens on a sharded cluster (mongos: no setName in hello)', async () => {
    const source = createMongoProcessChangeSource(
      fakeDb({ msg: 'isdbgrid', isWritablePrimary: true }, async () => null), 'p',
    );
    const handle = await source.open(() => {}, () => {});
    await handle.close();
  });

  it('reports a standalone server as unavailable', async () => {
    const source = createMongoProcessChangeSource(
      fakeDb({ isWritablePrimary: true }, async () => {
        throw Object.assign(new Error('The $changeStream stage is only supported on replica sets'), { code: 40573 });
      }), 'p',
    );
    await expect(source.open(() => {}, () => {})).rejects.toBeInstanceOf(ChangeStreamsUnavailable);
  });
});

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const client = new MongoClient(MONGO_URL);
await client.connect();
const db = client.db('optio_test_change_source');
const isReplicaSet = Boolean((await client.db('admin').command({ hello: 1 })).setName);

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

describe.skipIf(!isReplicaSet)('the change events reaching the API (needs a replica set)', () => {
  it('carry only what the relevance rules use', async () => {
    const changes: ProcessChange[] = [];
    const handle = await createMongoProcessChangeSource(db, 'trim').open((c) => changes.push(c), () => {});
    const col = db.collection('trim_processes');
    const rootId = new ObjectId();
    const _id = new ObjectId();
    try {
      await col.insertOne({
        _id, rootId, processId: 'p', params: { big: 'x'.repeat(5000) }, metadata: { m: 1 },
        status: { state: 'scheduled' }, log: [{ message: 'y'.repeat(5000) }],
      });
      await col.updateOne({ _id }, {
        $set: { progress: { percent: 5, message: 'z'.repeat(5000) } },
        $push: { log: { message: 'w'.repeat(5000) } },
      } as any);
      await col.updateOne({ _id }, { $set: { originatingSessionId: 's1' } });
      const end = Date.now() + 60_000;
      while (changes.length < 3 && Date.now() < end) await new Promise((r) => setTimeout(r, 20));
    } finally {
      await handle.close();
    }

    expect(changes).toEqual([
      { op: 'insert', id: _id.toString(), doc: { rootId } },
      { op: 'update', id: _id.toString(), fields: new Set(['progress', 'log']), values: {} },
      { op: 'update', id: _id.toString(), fields: new Set(['originatingSessionId']), values: { originatingSessionId: 's1' } },
    ]);
  });
});
