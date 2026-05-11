import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createOptioTransports } from '../optio-transports.js';

const startMock = vi.fn(async () => {});
const stopMock = vi.fn(async () => {});

vi.mock('@clamator/over-redis', () => ({
  RedisRpcClient: vi.fn().mockImplementation((opts: any) => ({
    keyPrefix: opts.keyPrefix,
    start: startMock,
    stop: stopMock,
  })),
}));

const fakeRedis: any = { duplicate: () => fakeRedis };

beforeEach(() => {
  startMock.mockClear();
  stopMock.mockClear();
  stopMock.mockResolvedValue(undefined);
});

describe('createOptioTransports', () => {
  it('returns a fresh RpcClient on first get for a (db, prefix) pair', async () => {
    const transports = createOptioTransports(fakeRedis);
    const rpc = transports.get('mydb', 'optio');
    expect((rpc as any).keyPrefix).toBe('mydb/optio');
    await new Promise((res) => setImmediate(res));
    expect(startMock).toHaveBeenCalledTimes(1);
  });

  it('returns the same RpcClient instance for the same (db, prefix) on subsequent calls', () => {
    const transports = createOptioTransports(fakeRedis);
    const rpc1 = transports.get('mydb', 'optio');
    const rpc2 = transports.get('mydb', 'optio');
    expect(rpc2).toBe(rpc1);
  });

  it('returns distinct RpcClient instances for different (db, prefix) pairs', () => {
    const transports = createOptioTransports(fakeRedis);
    const a = transports.get('mydb', 'optio');
    const b = transports.get('mydb', 'excavator');
    const c = transports.get('otherdb', 'optio');
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  it('closeAll stops every cached RpcClient and clears the cache', async () => {
    const transports = createOptioTransports(fakeRedis);
    transports.get('mydb', 'optio');
    transports.get('mydb', 'excavator');
    await transports.closeAll();
    expect(stopMock).toHaveBeenCalledTimes(2);

    const fresh = transports.get('mydb', 'optio');
    expect(fresh).toBeDefined();
  });

  it('closeAll aggregates rejections without short-circuiting', async () => {
    stopMock.mockRejectedValueOnce(new Error('first stop failed'));
    stopMock.mockResolvedValueOnce(undefined);
    const transports = createOptioTransports(fakeRedis);
    transports.get('mydb', 'optio');
    transports.get('mydb', 'excavator');
    await expect(transports.closeAll()).rejects.toThrow(AggregateError);
    expect(stopMock).toHaveBeenCalledTimes(2);
  });
});
