import type { Redis } from 'ioredis';
import { RedisRpcClient } from '@clamator/over-redis';
import { EngineClient } from './_generated/engine.js';

// EngineClient (generated) wraps a ClamatorClient and exposes only RPC
// methods. We augment it with start()/stop() delegating to the underlying
// RedisRpcClient so callers can manage the connection lifecycle without
// needing a separate handle.
export type ManagedEngineClient = EngineClient & {
  start(): Promise<void>;
  stop(): Promise<void>;
};

export interface EngineCache {
  get(database: string, prefix: string): ManagedEngineClient;
  closeAll(): Promise<void>;
}

// TODO: cache is unbounded by design. Multi-db deployments are expected to
// have a small (~10) number of (database, prefix) pairs. If the cache exceeds
// 100 entries in production, file an issue and revisit eviction strategy.
export function createEngineCache(redis: Redis): EngineCache {
  const map = new Map<string, ManagedEngineClient>();

  return {
    get(database, prefix) {
      const key = `${database}/${prefix}`;
      let engine = map.get(key);
      if (!engine) {
        const rpc = new RedisRpcClient({ redis, keyPrefix: key });
        engine = Object.assign(new EngineClient(rpc), {
          start: () => rpc.start(),
          stop: () => rpc.stop(),
        });
        engine.start();
        map.set(key, engine);
      }
      return engine;
    },

    async closeAll() {
      await Promise.all([...map.values()].map((e) => e.stop()));
      map.clear();
    },
  };
}
