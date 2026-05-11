import type { Redis } from 'ioredis';
import { RedisRpcClient } from '@clamator/over-redis';
import type { RpcClientCore } from '@clamator/protocol';

export interface OptioTransports {
  get(database: string, prefix: string): RpcClientCore;
  closeAll(): Promise<void>;
}

// Caches one RpcClientCore (concretely RedisRpcClient) per (database, prefix)
// pair. Each transport is bound to a unique redis namespace via
// keyPrefix = `${database}/${prefix}` and can be wrapped by any number of
// clamator contract clients (OptioEngineClient + custom consumer contracts).
//
// Cache is unbounded by design. Multi-db deployments are expected to have a
// small (~10) number of pairs. If the cache exceeds 100 entries in
// production, file an issue and revisit eviction strategy.
export function createOptioTransports(redis: Redis): OptioTransports {
  const map = new Map<string, RpcClientCore>();

  return {
    get(database, prefix) {
      const key = `${database}/${prefix}`;
      let rpc = map.get(key);
      if (!rpc) {
        rpc = new RedisRpcClient({ redis, keyPrefix: key });
        rpc.start().catch((err) => {
          console.error(`[optio-transports] start failed for ${key}:`, err);
        });
        map.set(key, rpc);
      }
      return rpc;
    },

    async closeAll() {
      const results = await Promise.allSettled([...map.values()].map((r) => r.stop()));
      map.clear();
      const rejections = results
        .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
        .map((r) => r.reason);
      if (rejections.length > 0) {
        throw new AggregateError(rejections, 'closeAll: some transports failed to stop');
      }
    },
  };
}
