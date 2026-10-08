import type { Db, MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import type { DbOptions } from './resolve.js';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

interface OptioInstance {
  database: string;
  prefix: string;
  live: boolean;
}

async function discoverPrefixesInDb(db: Db, only?: string): Promise<string[]> {
  const collections = await db.listCollections().toArray();
  const candidates = collections
    .map((c) => c.name)
    .filter((name) => name.endsWith('_processes'))
    .map((name) => name.slice(0, -'_processes'.length))
    .filter((prefix) => only === undefined || prefix === only);

  const confirmed: string[] = [];

  for (const prefix of candidates) {
    const doc = await db.collection(`${prefix}_processes`).findOne();
    if (doc && REQUIRED_FIELDS.every((f) => f in doc)) {
      confirmed.push(prefix);
    }
  }

  return confirmed.sort();
}

async function checkLive(redis: Redis | undefined, database: string, prefix: string): Promise<boolean> {
  if (!redis) return false;
  const key = `${database}/${prefix}:heartbeat`;
  const result = await redis.exists(key);
  return result === 1;
}

/**
 * The optio instances (database + prefix) reachable through `opts`: in its one
 * database (single-db) or in every database on the server (multi-db). With
 * `prefix`, only instances of that prefix (an app pinned to one prefix, e.g.
 * the adapters' `prefix` option).
 */
export async function discoverInstances(
  opts: DbOptions, redis?: Redis, prefix?: string,
): Promise<OptioInstance[]> {
  if ('db' in opts && opts.db) {
    const dbName = opts.db.databaseName;
    const prefixes = await discoverPrefixesInDb(opts.db, prefix);
    const instances: OptioInstance[] = [];
    for (const found of prefixes) {
      const live = await checkLive(redis, dbName, found);
      instances.push({ database: dbName, prefix: found, live });
    }
    return instances;
  }

  const adminDb = opts.mongoClient!.db().admin();
  const { databases } = await adminDb.listDatabases();
  const instances: OptioInstance[] = [];

  for (const dbInfo of databases) {
    const db = opts.mongoClient!.db(dbInfo.name);
    const prefixes = await discoverPrefixesInDb(db, prefix);
    for (const found of prefixes) {
      const live = await checkLive(redis, dbInfo.name, found);
      instances.push({ database: dbInfo.name, prefix: found, live });
    }
  }

  return instances.sort((a, b) =>
    a.database.localeCompare(b.database) || a.prefix.localeCompare(b.prefix),
  );
}
