import type { Db, MongoClient } from 'mongodb';
import type { DbOptions } from './resolve-db.js';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

interface OptioInstance {
  database: string;
  prefix: string;
}

async function discoverPrefixesInDb(db: Db): Promise<string[]> {
  const collections = await db.listCollections().toArray();
  const candidates = collections
    .map((c) => c.name)
    .filter((name) => name.endsWith('_processes'))
    .map((name) => name.slice(0, -'_processes'.length));

  const confirmed: string[] = [];

  for (const prefix of candidates) {
    const doc = await db.collection(`${prefix}_processes`).findOne();
    if (doc && REQUIRED_FIELDS.every((f) => f in doc)) {
      confirmed.push(prefix);
    }
  }

  return confirmed.sort();
}

export async function discoverInstances(opts: DbOptions): Promise<OptioInstance[]> {
  if ('db' in opts && opts.db) {
    const prefixes = await discoverPrefixesInDb(opts.db);
    const dbName = opts.db.databaseName;
    return prefixes.map((prefix) => ({ database: dbName, prefix }));
  }

  const adminDb = opts.mongoClient!.db().admin();
  const { databases } = await adminDb.listDatabases();
  const instances: OptioInstance[] = [];

  for (const dbInfo of databases) {
    const db = opts.mongoClient!.db(dbInfo.name);
    const prefixes = await discoverPrefixesInDb(db);
    for (const prefix of prefixes) {
      instances.push({ database: dbInfo.name, prefix });
    }
  }

  return instances.sort((a, b) =>
    a.database.localeCompare(b.database) || a.prefix.localeCompare(b.prefix),
  );
}
