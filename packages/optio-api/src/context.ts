import type { Redis } from 'ioredis';
import { createEngineCache, type EngineCache } from './engine-cache.js';
import type { DbOptions } from './resolve-db.js';

export interface OptioContext {
  dbOpts: DbOptions;
  engineCache: EngineCache;
  redis: Redis;
}

export function createOptioContext(opts: { dbOpts: DbOptions; redis: Redis }): OptioContext {
  return {
    dbOpts: opts.dbOpts,
    engineCache: createEngineCache(opts.redis),
    redis: opts.redis,
  };
}
