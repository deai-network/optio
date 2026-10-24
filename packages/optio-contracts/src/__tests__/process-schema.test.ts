import { describe, it, expect } from 'vitest';
import {
  ProcessSchema,
  MetadataFilterQueryParamSchema,
  ProcessMetadataFilterSchema,
  ProcessMetadataPredicateSchema,
  FilterFieldPath,
  FilterLeafOps,
} from '../schemas/process.js';
import { processesContract } from '../api-to-frontend.js';
import {
  and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte,
} from '../process-filter-helpers.js';

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

describe('ProcessSchema task-factory fields', () => {
  it('carries taskFactory and parameters (the field definitions as data)', () => {
    const parameters = [{ kind: 'numeric', id: 'n', label: { en: 'N' }, min: 1 }];
    const parsed = ProcessSchema.parse({ ...baseProcess(), taskFactory: true, parameters });
    expect(parsed.taskFactory).toBe(true);
    expect(parsed.parameters).toEqual(parameters);
  });
});

describe('ProcessSchema ttlSeconds', () => {
  it('carries ttlSeconds as a number or null', () => {
    expect(ProcessSchema.parse({ ...baseProcess(), ttlSeconds: 60 }).ttlSeconds).toBe(60);
    expect(ProcessSchema.parse({ ...baseProcess(), ttlSeconds: null }).ttlSeconds).toBeNull();
    expect(ProcessSchema.parse(baseProcess()).ttlSeconds).toBeUndefined();
  });
});

describe('ProcessSchema result', () => {
  it('a process may carry its result', () => {
    const result = { filename: 'a.pdf', contentType: 'application/pdf', size: 3 };
    expect(ProcessSchema.parse({ ...baseProcess(), result }).result).toEqual(result);
    expect(ProcessSchema.parse(baseProcess()).result).toBeUndefined();
    expect(ProcessSchema.safeParse({ ...baseProcess(), result: { ...result, size: -1 } }).success).toBe(false);
    // The GridFS id (result.fileId) is optio's own, never part of the schema.
    expect(ProcessSchema.parse({ ...baseProcess(), result: { ...result, fileId: 'x' } }).result).toEqual(result);
  });
});

describe('getResult route', () => {
  it('is GET /processes/:id/result: the file\'s bytes, or a 404 with its reason', () => {
    const route = processesContract.getResult;
    expect(route.method).toBe('GET');
    expect(route.path).toBe('/processes/:id/result');
    expect((route.query as any).parse({ database: 'd', prefix: 'p' })).toEqual({ database: 'd', prefix: 'p' });
    expect(Object.keys(route.responses).map(Number).sort()).toEqual([200, 404]);
    expect((route.responses[200] as any).contentType).toBe('application/octet-stream');
    const notFound = route.responses[404] as any;
    expect(notFound.parse({ reason: 'no-result', message: 'x' })).toEqual({ reason: 'no-result', message: 'x' });
    expect(notFound.parse({ reason: 'not-found', message: 'x' }).reason).toBe('not-found');
    expect(notFound.safeParse({ reason: 'forbidden', message: 'x' }).success).toBe(false);
  });
});

describe('launch route body', () => {
  it('carries params through', () => {
    const body = processesContract.launch.body as any;
    expect(body.parse({ params: { n: 3 } })).toEqual({ params: { n: 3 } });
  });

  it('carries ephemeral through', () => {
    const body = processesContract.launch.body as any;
    expect(body.parse({ ephemeral: true })).toEqual({ ephemeral: true });
    expect(body.parse({ ephemeral: false })).toEqual({ ephemeral: false });
    expect(body.safeParse({ ephemeral: 'yes' }).success).toBe(false);
  });
});

describe('dismiss route 200 body', () => {
  it('is a process, or null when the record was deleted', () => {
    const ok = processesContract.dismiss.responses[200] as any;
    expect(ok.parse(null)).toBeNull();
    expect(ok.parse({ ...baseProcess() }).processId).toBe('p1');
  });
});

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
        { requestId: 'r2', type: 'client', keyword: 'k', data: { n: 1 } },
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

describe('ProcessMetadataFilterSchema (legacy flat shape, backwards-compatible)', () => {
  it('accepts an empty object', () => {
    expect(ProcessMetadataFilterSchema.parse({})).toEqual({});
  });

  it('accepts a flat scalar record (legacy)', () => {
    const v = { targetId: 'abc', kind: 'x', n: 5, b: true, nl: null };
    expect(ProcessMetadataFilterSchema.parse(v)).toEqual(v);
  });

  it('accepts dotted field path in legacy shape', () => {
    expect(ProcessMetadataFilterSchema.parse({ 'foo.bar': 'x' })).toEqual({ 'foo.bar': 'x' });
  });
});

describe('ProcessMetadataPredicateSchema (new predicate-tree shape)', () => {
  it('accepts a single-leaf single-op predicate', () => {
    const p = { foo: { eq: 'x' } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts a single-leaf multi-op predicate', () => {
    const p = { foo: { gt: 1, lte: 10 } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts a multi-leaf predicate (implicit AND across keys)', () => {
    const p = { foo: { eq: 'x' }, bar: { in: [1, 2] } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts AND of leaves', () => {
    const p = { AND: [{ a: { eq: 1 } }, { b: { eq: 2 } }] };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts OR of leaves', () => {
    const p = { OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts NOT of a single predicate', () => {
    const p = { NOT: { a: { eq: 1 } } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts nested combinators: (A AND B) OR (C AND D)', () => {
    const p = {
      OR: [
        { AND: [{ tag: { eq: 'demo' } }, { owner: { eq: 'kris' } }] },
        { AND: [{ tag: { eq: 'prod' } }, { region: { in: ['us', 'eu'] } }] },
      ],
    };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('accepts dotted field paths in predicate leaves', () => {
    const p = { 'foo.bar.baz': { eq: 'x' } };
    expect(ProcessMetadataPredicateSchema.parse(p)).toEqual(p);
  });

  it('rejects empty AND array', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ AND: [] });
    expect(r.success).toBe(false);
  });

  it('rejects empty OR array', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ OR: [] });
    expect(r.success).toBe(false);
  });

  it('rejects NOT carrying an array (NOT takes a single predicate)', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ NOT: [{ a: { eq: 1 } }] });
    expect(r.success).toBe(false);
  });

  it('rejects an empty leaf operator object', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ foo: {} });
    expect(r.success).toBe(false);
  });

  it('rejects an unknown operator in a leaf', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({ foo: { eq: 'x', regex: '^a' } });
    expect(r.success).toBe(false);
  });

  it('rejects mixing combinator and field keys in the same object', () => {
    const r = ProcessMetadataPredicateSchema.safeParse({
      AND: [{ a: { eq: 1 } }],
      foo: { eq: 'x' },
    });
    expect(r.success).toBe(false);
  });
});

describe('FilterFieldPath', () => {
  it('accepts simple alphanumeric path', () => {
    expect(FilterFieldPath.parse('foo')).toBe('foo');
  });

  it('accepts dotted multi-segment path', () => {
    expect(FilterFieldPath.parse('foo.bar.baz')).toBe('foo.bar.baz');
  });

  it('rejects path containing $', () => {
    expect(FilterFieldPath.safeParse('$where').success).toBe(false);
  });

  it('rejects path containing $ in any segment', () => {
    expect(FilterFieldPath.safeParse('foo.$bar').success).toBe(false);
  });

  it('rejects leading dot', () => {
    expect(FilterFieldPath.safeParse('.foo').success).toBe(false);
  });

  it('rejects trailing dot', () => {
    expect(FilterFieldPath.safeParse('foo.').success).toBe(false);
  });

  it('rejects empty segment (consecutive dots)', () => {
    expect(FilterFieldPath.safeParse('a..b').success).toBe(false);
  });

  it('rejects empty string', () => {
    expect(FilterFieldPath.safeParse('').success).toBe(false);
  });
});

describe('FilterLeafOps strict mode', () => {
  it('accepts a single valid op', () => {
    expect(FilterLeafOps.parse({ eq: 'x' })).toEqual({ eq: 'x' });
  });

  it('rejects unknown keys (strict)', () => {
    expect(FilterLeafOps.safeParse({ eq: 'x', startsWith: 'a' }).success).toBe(false);
  });
});

describe('process-filter-helpers', () => {
  it('and produces AND wrapper', () => {
    const p = and(eq('a', 1), eq('b', 2));
    expect(p).toEqual({ AND: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
    expect(ProcessMetadataPredicateSchema.safeParse(p).success).toBe(true);
  });

  it('or produces OR wrapper', () => {
    const p = or(eq('a', 1), eq('b', 2));
    expect(p).toEqual({ OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
  });

  it('not produces NOT wrapper', () => {
    expect(not(eq('a', 1))).toEqual({ NOT: { a: { eq: 1 } } });
  });

  it('leaf builders cover all operators', () => {
    expect(eq('f', 1)).toEqual({ f: { eq: 1 } });
    expect(ne('f', 1)).toEqual({ f: { ne: 1 } });
    expect(isIn('f', [1, 2])).toEqual({ f: { in: [1, 2] } });
    expect(notIn('f', [1, 2])).toEqual({ f: { nin: [1, 2] } });
    expect(exists('f')).toEqual({ f: { exists: true } });
    expect(exists('f', false)).toEqual({ f: { exists: false } });
    expect(gt('f', 1)).toEqual({ f: { gt: 1 } });
    expect(gte('f', 1)).toEqual({ f: { gte: 1 } });
    expect(lt('f', 1)).toEqual({ f: { lt: 1 } });
    expect(lte('f', 1)).toEqual({ f: { lte: 1 } });
  });

  it('builders compose into the spec example: (A AND B) OR (C AND D)', () => {
    const p = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    expect(ProcessMetadataPredicateSchema.safeParse(p).success).toBe(true);
    expect(p).toEqual({
      OR: [
        { AND: [{ tag: { eq: 'demo' } }, { owner: { eq: 'kris' } }] },
        { AND: [{ tag: { eq: 'prod' } }, { region: { in: ['us', 'eu'] } }] },
      ],
    });
  });
});

it('accepts supportsResurrect and hasUnsavedWork', () => {
  const parsed = ProcessSchema.parse({ ...baseProcess(), supportsResurrect: true, hasUnsavedWork: true });
  expect(parsed.supportsResurrect).toBe(true);
  expect(parsed.hasUnsavedWork).toBe(true);
});

it('accepts resurrecting', () => {
  const parsed = ProcessSchema.parse({ ...baseProcess(), resurrecting: true });
  expect(parsed.resurrecting).toBe(true);
});
