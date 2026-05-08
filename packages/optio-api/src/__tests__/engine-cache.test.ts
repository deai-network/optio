import { describe, it, expect, vi } from 'vitest';
import { createEngineCache } from '../engine-cache.js';

// Minimal Redis stub — engine-cache passes it to RedisRpcClient. We don't
// actually communicate; we just check that cache keys, lifecycle, and
// idempotency behave.
const fakeRedis: any = { duplicate: () => fakeRedis };

describe('createEngineCache', () => {
  it('returns the same EngineClient for the same (database, prefix)', () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db1', 'optio');
    expect(a).toBe(b);
  });

  it('returns distinct clients for distinct keys', () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db2', 'optio');
    const c = cache.get('db1', 'other');
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  it('closeAll() stops every cached client', async () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db2', 'optio');
    const stopA = vi.spyOn(a, 'stop').mockResolvedValue(undefined);
    const stopB = vi.spyOn(b, 'stop').mockResolvedValue(undefined);
    await cache.closeAll();
    expect(stopA).toHaveBeenCalledOnce();
    expect(stopB).toHaveBeenCalledOnce();
  });

  it('closeAll() clears map even if a client stop() rejects', async () => {
    const cache = createEngineCache(fakeRedis);
    const a = cache.get('db1', 'optio');
    const b = cache.get('db2', 'optio');
    vi.spyOn(a, 'stop').mockRejectedValue(new Error('boom'));
    vi.spyOn(b, 'stop').mockResolvedValue(undefined);

    await expect(cache.closeAll()).rejects.toThrow();

    // Map was cleared despite the rejection — second closeAll is a no-op.
    await expect(cache.closeAll()).resolves.toBeUndefined();
  });

  it('closeAll() called twice succeeds (idempotent)', async () => {
    const cache = createEngineCache(fakeRedis);
    cache.get('db1', 'optio');
    await cache.closeAll();
    await expect(cache.closeAll()).resolves.toBeUndefined();
  });
});
