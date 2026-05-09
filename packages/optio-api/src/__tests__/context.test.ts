import { describe, it, expect, vi } from 'vitest';
import { createOptioContext } from '../context.js';
import type { Db } from 'mongodb';

// Minimal Redis stub — engine-cache passes it to RedisRpcClient via
// `.duplicate()`. Match the convention used by engine-cache.test.ts so
// RedisRpcClient.start() doesn't throw and pollute stderr.
const fakeRedis: any = { duplicate: () => fakeRedis };

describe('createOptioContext', () => {
  it('returns a context with the supplied dbOpts and redis', () => {
    const fakeDb = { databaseName: 'testdb' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    expect(ctx.dbOpts).toEqual({ db: fakeDb });
    expect(ctx.redis).toBe(fakeRedis);
    expect(ctx.engineCache).toBeDefined();
    expect(typeof ctx.engineCache.get).toBe('function');
    expect(typeof ctx.engineCache.closeAll).toBe('function');
  });

  it('engineCache.get returns the same instance for the same key', () => {
    const fakeDb = { databaseName: 'd' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    const a = ctx.engineCache.get('d', 'optio');
    const b = ctx.engineCache.get('d', 'optio');
    expect(a).toBe(b);
  });

  it('closeAll is idempotent', async () => {
    const fakeDb = { databaseName: 'd' } as unknown as Db;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    const stopSpy = vi.fn().mockResolvedValue(undefined);
    const e = ctx.engineCache.get('d', 'optio');
    (e as any).stop = stopSpy;
    await ctx.engineCache.closeAll();
    await ctx.engineCache.closeAll();   // second call is a no-op
    expect(stopSpy).toHaveBeenCalledTimes(1);
  });
});
