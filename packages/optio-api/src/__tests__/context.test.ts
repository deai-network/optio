import { describe, it, expect, vi } from 'vitest';
import { createOptioContext } from '../context.js';
import type { Db } from 'mongodb';

// Minimal Redis stub — optio-transports passes it to RedisRpcClient via
// `.duplicate()` and start()/stop() on the resulting RpcClient.
const fakeRedis: any = { duplicate: () => fakeRedis };

describe('createOptioContext', () => {
  it('returns a context with the supplied dbOpts and redis plus a transport cache', () => {
    const fakeDb = { databaseName: 'testdb' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    expect(ctx.dbOpts).toEqual({ db: fakeDb });
    expect(ctx.redis).toBe(fakeRedis);
    expect(ctx.transports).toBeDefined();
    expect(typeof ctx.transports.get).toBe('function');
    expect(typeof ctx.transports.closeAll).toBe('function');
    expect(typeof ctx.closeAll).toBe('function');
  });

  it('transports.get returns the same instance for the same key', () => {
    const fakeDb = { databaseName: 'd' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    const a = ctx.transports.get('d', 'optio');
    const b = ctx.transports.get('d', 'optio');
    expect(a).toBe(b);
  });

  it('ctx.closeAll delegates to transports.closeAll', async () => {
    const fakeDb = { databaseName: 'd' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    const spy = vi.spyOn(ctx.transports, 'closeAll').mockResolvedValue(undefined);
    await ctx.closeAll();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
