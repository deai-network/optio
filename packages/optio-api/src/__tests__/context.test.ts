import { describe, it, expect, vi } from 'vitest';
import { Redis } from 'ioredis';
import { createOptioContext } from '../context.js';
import type { Db } from 'mongodb';

describe('createOptioContext', () => {
  it('returns a context with the supplied dbOpts and redis', () => {
    const fakeDb = { databaseName: 'testdb' } as unknown as Db;
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    expect(ctx.dbOpts).toEqual({ db: fakeDb });
    expect(ctx.redis).toBe(fakeRedis);
    expect(ctx.engineCache).toBeDefined();
    expect(typeof ctx.engineCache.get).toBe('function');
    expect(typeof ctx.engineCache.closeAll).toBe('function');
  });

  it('engineCache.get returns the same instance for the same key', () => {
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: { databaseName: 'd' } as any }, redis: fakeRedis });
    const a = ctx.engineCache.get('d', 'optio');
    const b = ctx.engineCache.get('d', 'optio');
    expect(a).toBe(b);
  });

  it('closeAll is idempotent', async () => {
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: { databaseName: 'd' } as any }, redis: fakeRedis });
    const stopSpy = vi.fn().mockResolvedValue(undefined);
    const e = ctx.engineCache.get('d', 'optio');
    (e as any).stop = stopSpy;
    await ctx.engineCache.closeAll();
    await ctx.engineCache.closeAll();   // second call is a no-op
    expect(stopSpy).toHaveBeenCalledTimes(1);
  });
});
