import { describe, it, expect } from 'vitest';
import { ProcessSchema } from '../schemas/process.js';
import { MetadataFilterQueryParamSchema } from '../schemas/process.js';

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

  it('accepts browserOpenRequests', () => {
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      browserOpenRequests: [{ requestId: 'r1', url: 'https://example.com' }],
    });
    expect(parsed.browserOpenRequests).toEqual([{ requestId: 'r1', url: 'https://example.com' }]);
  });

  it('accepts sessionEvents (attention + domain)', () => {
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      sessionEvents: [
        { requestId: 'r1', type: 'attention', reason: 'help' },
        { requestId: 'r2', type: 'domain', keyword: 'k', data: { n: 1 } },
      ],
    });
    expect(parsed.sessionEvents).toHaveLength(2);
  });

  it('accepts originatingSessionId string and null', () => {
    expect(ProcessSchema.parse({ ...baseProcess(), originatingSessionId: 'tok' }).originatingSessionId).toBe('tok');
    expect(ProcessSchema.parse({ ...baseProcess(), originatingSessionId: null }).originatingSessionId).toBeNull();
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

describe('MetadataFilterQueryParamSchema', () => {
  it('parses a valid URL-decoded JSON object', () => {
    const parsed = MetadataFilterQueryParamSchema.parse('{"targetId":"abc"}');
    expect(parsed).toEqual({ targetId: 'abc' });
  });

  it('parses a multi-key object', () => {
    const parsed = MetadataFilterQueryParamSchema.parse('{"a":"x","b":"y"}');
    expect(parsed).toEqual({ a: 'x', b: 'y' });
  });

  it('returns undefined for undefined input', () => {
    const parsed = MetadataFilterQueryParamSchema.parse(undefined);
    expect(parsed).toBeUndefined();
  });

  it('rejects malformed JSON', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('not json');
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toContain('valid JSON');
    }
  });

  it('rejects a JSON value that is not an object', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('"foo"');
    expect(result.success).toBe(false);
  });

  it('rejects a JSON array', () => {
    const result = MetadataFilterQueryParamSchema.safeParse('[1,2,3]');
    expect(result.success).toBe(false);
  });
});
