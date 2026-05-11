import type { Db, MongoClient } from 'mongodb';
import { OptioEngineClient } from './_generated/optio-engine.js';
import type { OptioContext } from './context.js';

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
): { db: Db; database: string; prefix: string } {
  const prefix = query.prefix || 'optio';

  if ('db' in opts && opts.db) {
    return { db: opts.db, database: opts.db.databaseName, prefix };
  }

  if (!query.database) {
    throw new Error('database query parameter is required in multi-db mode');
  }

  return { db: opts.mongoClient!.db(query.database), database: query.database, prefix };
}

export function resolveOptioEngine(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
): OptioEngineClient {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  return new OptioEngineClient(ctx.transports.get(database, prefix));
}
