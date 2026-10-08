import { describe, it, expect } from 'vitest';
import Fastify from 'fastify';
import type { Db, MongoClient } from 'mongodb';
import { discoverInstances } from '../discovery.js';
import { registerOptioApi } from '../adapters/fastify.js';

// Instance discovery honours a configured prefix (owner request 2026-10-08):
// an app pinned to one prefix (optio-dashboard's OPTIO_PREFIX) is offered
// only that instance, however many others share the Mongo server.

const PROCESS = { processId: 'p', rootId: 'r', depth: 0 };

function fakeDb(name: string, collections: Record<string, unknown>): Db {
  return {
    databaseName: name,
    listCollections: () => ({ toArray: async () => Object.keys(collections).map((c) => ({ name: c })) }),
    collection: (c: string) => ({ findOne: async () => collections[c] ?? null }),
  } as unknown as Db;
}

function fakeClient(dbs: Record<string, Db>): MongoClient {
  return {
    db: (name?: string) =>
      name === undefined
        ? { admin: () => ({ listDatabases: async () => ({ databases: Object.keys(dbs).map((n) => ({ name: n })) }) }) }
        : dbs[name],
  } as unknown as MongoClient;
}

const demo = () => fakeDb('optio-demo', { optio_processes: PROCESS, test_processes: PROCESS });
const excavator = () => fakeDb('excavator', { gm_processes: PROCESS });

describe('discoverInstances prefix filter', () => {
  it('single-db: only the configured prefix', async () => {
    expect(await discoverInstances({ db: demo() }, undefined, 'optio')).toEqual([
      { database: 'optio-demo', prefix: 'optio', live: false },
    ]);
  });

  it('multi-db: only the configured prefix, in every database', async () => {
    const client = fakeClient({ 'optio-demo': demo(), excavator: excavator() });
    expect(await discoverInstances({ mongoClient: client }, undefined, 'gm')).toEqual([
      { database: 'excavator', prefix: 'gm', live: false },
    ]);
  });

  it('no prefix: every instance, as before', async () => {
    const client = fakeClient({ 'optio-demo': demo(), excavator: excavator() });
    expect((await discoverInstances({ mongoClient: client })).map((i) => `${i.database}/${i.prefix}`)).toEqual([
      'excavator/gm', 'optio-demo/optio', 'optio-demo/test',
    ]);
  });
});

describe('registerOptioApi prefix option', () => {
  it('GET /api/optio/instances offers only the configured prefix', async () => {
    const app = Fastify();
    registerOptioApi(app, {
      ctx: {
        dbOpts: { db: demo() },
        redis: undefined,
        transports: { get: () => ({}), closeAll: async () => {} },
        closeAll: async () => {},
      } as any,
      authenticate: () => 'operator',
      prefix: 'optio',
    });
    const res = await app.inject({ method: 'GET', url: '/api/optio/instances' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ instances: [{ database: 'optio-demo', prefix: 'optio', live: false }] });
    await app.close();
  });
});
