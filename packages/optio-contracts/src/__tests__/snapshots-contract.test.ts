import { describe, it, expect } from 'vitest';
import {
  processesContract,
  ListSnapshotsFailureReason,
  ProcessSnapshotSchema,
} from '../index.js';

// A listed capturedAt in its wire form (UTC, three fraction digits).
const TS = '2026-11-19T13:05:12.345Z';

describe('processesContract.getSnapshots', () => {
  it('is GET /processes/:id/snapshots with the instance query', () => {
    const route = processesContract.getSnapshots;
    expect(route.method).toBe('GET');
    expect(route.path).toBe('/processes/:id/snapshots');
    expect(Object.keys(route.responses).map(Number).sort()).toEqual([200, 404]);
    expect((route.query as any).parse({ database: 'd', prefix: 'p' })).toEqual({ database: 'd', prefix: 'p' });
  });

  it('answers the snapshots, each a capturedAt and an endState', () => {
    const ok = (processesContract.getSnapshots.responses as any)[200];
    const body = { snapshots: [{ capturedAt: TS, endState: 'cancelled' }] };
    expect(ok.parse(body)).toEqual(body);
    expect(ok.safeParse({ snapshots: [{ capturedAt: TS }] }).success).toBe(false);
    expect(ProcessSnapshotSchema.parse({ capturedAt: TS, endState: '' })).toEqual({ capturedAt: TS, endState: '' });
    // Every listed capturedAt must be a valid resumeFrom (non-empty).
    expect(ProcessSnapshotSchema.safeParse({ capturedAt: '', endState: '' }).success).toBe(false);
  });

  it('its 404 carries a typed reason', () => {
    expect(ListSnapshotsFailureReason.options).toEqual(['not-found', 'no-snapshot-support']);
    const notFound = (processesContract.getSnapshots.responses as any)[404];
    expect(notFound.parse({ reason: 'no-snapshot-support', message: 'x' })).toEqual({ reason: 'no-snapshot-support', message: 'x' });
    expect(notFound.safeParse({ reason: 'bogus', message: 'x' }).success).toBe(false);
  });
});

describe('processesContract.launch body', () => {
  it('takes resumeFrom beside resume, and refuses an empty one', () => {
    const body = processesContract.launch.body as any;
    expect(body.parse({ resume: true, resumeFrom: TS })).toEqual({ resume: true, resumeFrom: TS });
    expect(body.safeParse({ resumeFrom: '' }).success).toBe(false);
  });
});
