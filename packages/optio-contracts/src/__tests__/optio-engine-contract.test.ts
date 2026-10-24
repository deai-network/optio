import { describe, it, expect } from 'vitest';
import { optioEngineContract } from '../optio-engine-to-api.js';
import { LaunchFailureReason, ResurrectFailureReason } from '../engine-failure-reasons.js';

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

  it('knows every reason optio-core can refuse a launch with', () => {
    expect(LaunchFailureReason.options).toEqual([
      'not-found', 'not-launchable', 'no-resume-support', 'launch-blocked',
      'shutting-down', 'parameters-required', 'not-a-factory', 'invalid-parameters',
      'invalid-metadata',
    ]);
  });

  it('launch takes optional params and a refusal carries an optional message', () => {
    const launch = optioEngineContract.methods.launch;
    const withParams = launch.params.parse({ processId: 'f', sessionId: null, params: { n: 3 } });
    expect(withParams.params).toEqual({ n: 3 });
    expect(launch.params.parse({ processId: 'f', sessionId: null }).params).toBeUndefined();
    const fail = launch.result.parse({ ok: false, reason: 'invalid-parameters', message: 'n: too big' });
    expect(fail.ok).toBe(false);
    if (!fail.ok) expect(fail.message).toBe('n: too big');
    expect(launch.result.parse({ ok: false, reason: 'parameters-required' }).ok).toBe(false);
    expect(launch.result.parse({ ok: false, reason: 'shutting-down' }).ok).toBe(false);
  });

  it('launch takes an optional ephemeral', () => {
    const launch = optioEngineContract.methods.launch;
    expect(launch.params.parse({ processId: 'f', sessionId: null, ephemeral: true }).ephemeral).toBe(true);
    expect(launch.params.parse({ processId: 'f', sessionId: null }).ephemeral).toBeUndefined();
    expect(launch.params.safeParse({ processId: 'f', sessionId: null, ephemeral: 1 }).success).toBe(false);
  });

  it('launch params may carry metadata', () => {
    const launch = optioEngineContract.methods.launch;
    const metadata = { user: 'u1', n: 3, admin: false, team: null };
    expect(launch.params.parse({ processId: 'f', sessionId: null, metadata }).metadata).toEqual(metadata);
    expect(launch.params.parse({ processId: 'f', sessionId: null }).metadata).toBeUndefined();
    expect(launch.params.safeParse({ processId: 'f', sessionId: null, metadata: { user: { id: 1 } } }).success).toBe(false);
    expect(launch.params.safeParse({ processId: 'f', sessionId: null, metadata: { tags: ['a'] } }).success).toBe(false);
    const fail = launch.result.parse({ ok: false, reason: 'invalid-metadata', message: 'kind' });
    expect(fail.ok).toBe(false);
    if (!fail.ok) expect(fail.message).toBe('kind');
  });

  it('dismiss result carries a process, or none when the record was deleted', () => {
    const dismiss = optioEngineContract.methods.dismiss;
    const proc = {
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
    };
    const withProc = dismiss.result.parse({ ok: true, process: proc });
    expect(withProc.ok && withProc.process?.processId).toBe('p1');
    const without = dismiss.result.parse({ ok: true });
    expect(without.ok && without.process).toBeUndefined();
    expect(dismiss.result.parse({ ok: false, reason: 'not-found' }).ok).toBe(false);
  });

  it('exposes resync as a notification (no result schema)', () => {
    const resync = optioEngineContract.methods.resync;
    expect(resync).toBeDefined();
    expect((resync as { result?: unknown }).result).toBeUndefined();
  });

  it('exposes resurrect with the launch-shaped result', () => {
    const resurrect = optioEngineContract.methods.resurrect;
    expect(resurrect).toBeDefined();
    expect(resurrect.params.parse({ processId: 'p1', sessionId: null })).toEqual({ processId: 'p1', sessionId: null });
    const fail = resurrect.result.parse({ ok: false, reason: 'resurrect-in-progress' });
    expect(fail.ok).toBe(false);
  });

  it('knows exactly the resurrect failure reasons', () => {
    expect(ResurrectFailureReason.options).toEqual([
      'not-found', 'not-resurrectable', 'no-resurrect-support',
      'resurrect-in-progress', 'launch-blocked', 'shutting-down',
    ]);
  });
});
