import { describe, it, expect } from 'vitest';
import { optioEngineContract } from '../optio-engine-to-api.js';
import { LaunchFailureReason } from '../engine-failure-reasons.js';

describe('optioEngineContract', () => {
  it('declares the expected service name', () => {
    expect(optioEngineContract.service).toBe('optio-engine');
  });

  it('exposes launch as a method with discriminated-union result', () => {
    const launch = optioEngineContract.methods.launch;
    expect(launch).toBeDefined();
    const ok = launch.result.parse({
      ok: true,
      process: {
        _id: '507f1f77bcf86cd799439011',
        processId: 'p1',
        name: 'P1',
        rootId: '507f1f77bcf86cd799439011',
        depth: 0,
        order: 0,
        cancellable: true,
        status: { state: 'idle' },
        progress: { percent: null },
        log: [],
        createdAt: new Date().toISOString(),
      },
    });
    expect(ok.ok).toBe(true);
    const fail = launch.result.parse({ ok: false, reason: 'not-found' });
    expect(fail.ok).toBe(false);
    if (!fail.ok) expect(fail.reason).toBe('not-found');
  });

  it('rejects an unknown LaunchFailureReason', () => {
    expect(() => LaunchFailureReason.parse('bogus')).toThrow();
  });

  it('exposes resync as a notification (no result schema)', () => {
    const resync = optioEngineContract.methods.resync;
    expect(resync).toBeDefined();
    expect((resync as { result?: unknown }).result).toBeUndefined();
  });
});
