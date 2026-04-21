import { describe, it, expect } from 'vitest';
import { ProcessSchema } from '../schemas/process.js';

function baseProcess() {
  return {
    _id: '507f1f77bcf86cd799439011',
    processId: 'p1',
    name: 'P1',
    rootId: '507f1f77bcf86cd799439011',
    depth: 0,
    order: 0,
    cancellable: true,
    status: { state: 'idle' as const },
    progress: { percent: null },
    log: [],
    createdAt: new Date(),
  };
}

describe('ProcessSchema widget fields', () => {
  it('accepts uiWidget as an optional string', () => {
    const parsed = ProcessSchema.parse({ ...baseProcess(), uiWidget: 'iframe' });
    expect(parsed.uiWidget).toBe('iframe');
  });

  it('accepts widgetData as arbitrary JSON', () => {
    const data = { localStorageOverrides: { foo: 'bar' }, nested: { a: [1, 2] } };
    const parsed = ProcessSchema.parse({ ...baseProcess(), widgetData: data });
    expect(parsed.widgetData).toEqual(data);
  });

  it('accepts a process without widget fields', () => {
    expect(() => ProcessSchema.parse(baseProcess())).not.toThrow();
  });

  it('accepts uiWidget: null as produced by the Python store', () => {
    const parsed = ProcessSchema.parse({ ...baseProcess(), uiWidget: null });
    expect(parsed.uiWidget).toBeNull();
  });

  it('accepts widgetData: null as produced by the Python store', () => {
    const parsed = ProcessSchema.parse({ ...baseProcess(), widgetData: null });
    expect(parsed.widgetData).toBeNull();
  });

  it('rejects widgetUpstream (server-side only; must not be in the schema)', () => {
    // widgetUpstream is intentionally NOT part of ProcessSchema.
    // Strict parsing would reject it; default (non-strict) strips it. Assert it is stripped.
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      widgetUpstream: { url: 'http://x' },
    } as any);
    expect((parsed as any).widgetUpstream).toBeUndefined();
  });
});
