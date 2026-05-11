import type { Redis } from 'ioredis';
import { createOptioTransports, type OptioTransports } from './optio-transports.js';
import type { DbOptions } from './resolve.js';

export interface OptioContext {
  dbOpts: DbOptions;
  transports: OptioTransports;
  redis: Redis;
  closeAll(): Promise<void>;
}

export function createOptioContext(opts: { dbOpts: DbOptions; redis: Redis }): OptioContext {
  const transports = createOptioTransports(opts.redis);
  return {
    dbOpts: opts.dbOpts,
    transports,
    redis: opts.redis,
    closeAll() {
      return transports.closeAll();
    },
  };
}
