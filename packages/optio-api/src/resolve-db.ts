import type { Db, MongoClient } from 'mongodb';

export interface SingleDbOptions {
  db: Db;
  mongoClient?: never;
}

export interface MultiDbOptions {
  mongoClient: MongoClient;
  db?: never;
}

export type DbOptions = SingleDbOptions | MultiDbOptions;

export function resolveDb(
  opts: DbOptions,
  query: { database?: string; prefix?: string },
): { db: Db; prefix: string } {
  const prefix = query.prefix || 'optio';

  if ('db' in opts && opts.db) {
    return { db: opts.db, prefix };
  }

  if (!query.database) {
    throw new Error('database query parameter is required in multi-db mode');
  }

  return { db: opts.mongoClient!.db(query.database), prefix };
}
