import { describe, it, expect } from 'vitest';
import {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';
import {
  and, or, not, eq, ne, isIn, notIn, exists, gt, gte, lt, lte,
} from 'optio-contracts';

describe('parseMetadataFilterQuery', () => {
  it('returns undefined value for undefined input', () => {
    expect(parseMetadataFilterQuery(undefined)).toEqual({ ok: true, value: undefined });
  });

  it('returns undefined value for null input', () => {
    expect(parseMetadataFilterQuery(null)).toEqual({ ok: true, value: undefined });
  });

  it('returns undefined value for empty string', () => {
    expect(parseMetadataFilterQuery('')).toEqual({ ok: true, value: undefined });
  });

  it('rejects non-string raw input', () => {
    const r = parseMetadataFilterQuery(123 as unknown);
    expect(r.ok).toBe(false);
  });

  it('rejects malformed JSON', () => {
    const r = parseMetadataFilterQuery('not json');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain('JSON');
  });

  it('rejects JSON array', () => {
    const r = parseMetadataFilterQuery('[1,2,3]');
    expect(r.ok).toBe(false);
  });

  it('rejects JSON string scalar', () => {
    const r = parseMetadataFilterQuery('"foo"');
    expect(r.ok).toBe(false);
  });

  it('parses valid object', () => {
    const r = parseMetadataFilterQuery('{"targetId":"abc","kind":"x"}');
    expect(r).toEqual({ ok: true, value: { targetId: 'abc', kind: 'x' } });
  });
});

describe('metadataFilterToMongo', () => {
  it('returns empty object for undefined', () => {
    expect(metadataFilterToMongo(undefined)).toEqual({});
  });

  it('returns empty object for empty filter', () => {
    expect(metadataFilterToMongo({})).toEqual({});
  });

  it('prefixes single key with metadata.', () => {
    expect(metadataFilterToMongo({ targetId: 'abc' })).toEqual({
      'metadata.targetId': { $eq: 'abc' },
    });
  });

  it('prefixes multiple keys with metadata.', () => {
    expect(metadataFilterToMongo({ a: 1, b: 'x' })).toEqual({
      $and: [
        { 'metadata.a': { $eq: 1 } },
        { 'metadata.b': { $eq: 'x' } },
      ],
    });
  });
});

describe('detectLegacyMetadataParams', () => {
  it('returns empty array when none present', () => {
    expect(detectLegacyMetadataParams({ rootId: 'abc' })).toEqual([]);
  });

  it('returns matching legacy keys sorted', () => {
    expect(detectLegacyMetadataParams({
      'metadata.zeta': 'z',
      rootId: 'r',
      'metadata.alpha': 'a',
    })).toEqual(['metadata.alpha', 'metadata.zeta']);
  });
});

describe('formatLegacyMetadataMessage', () => {
  it('formats message with single key', () => {
    expect(formatLegacyMetadataMessage(['metadata.foo'])).toBe(
      "Legacy 'metadata.*' query params are no longer supported. " +
      "Use ?metadataFilter=<URL-encoded JSON>. Offending keys: metadata.foo",
    );
  });

  it('formats message with multiple keys joined by comma', () => {
    expect(formatLegacyMetadataMessage(['metadata.a', 'metadata.b'])).toContain(
      'Offending keys: metadata.a, metadata.b',
    );
  });
});

describe('metadataFilterToMongo (predicate tree)', () => {
  it('translates single-leaf single-op', () => {
    expect(metadataFilterToMongo(eq('foo', 'x'))).toEqual({
      'metadata.foo': { $eq: 'x' },
    });
  });

  it('translates single-leaf multi-op (one node, multiple operators)', () => {
    const p = { foo: { gt: 1, lte: 10 } } as any;
    expect(metadataFilterToMongo(p)).toEqual({
      $and: [
        { 'metadata.foo': { $gt: 1 } },
        { 'metadata.foo': { $lte: 10 } },
      ],
    });
  });

  it('translates multi-leaf node into $and of single-key objects', () => {
    const p = { foo: { eq: 'x' }, bar: { in: [1, 2] } } as any;
    expect(metadataFilterToMongo(p)).toEqual({
      $and: [
        { 'metadata.foo': { $eq: 'x' } },
        { 'metadata.bar': { $in: [1, 2] } },
      ],
    });
  });

  it('translates AND into $and', () => {
    expect(metadataFilterToMongo(and(eq('a', 1), eq('b', 2)))).toEqual({
      $and: [
        { 'metadata.a': { $eq: 1 } },
        { 'metadata.b': { $eq: 2 } },
      ],
    });
  });

  it('translates OR into $or', () => {
    expect(metadataFilterToMongo(or(eq('a', 1), eq('b', 2)))).toEqual({
      $or: [
        { 'metadata.a': { $eq: 1 } },
        { 'metadata.b': { $eq: 2 } },
      ],
    });
  });

  it('translates NOT into $nor over a singleton array', () => {
    expect(metadataFilterToMongo(not(eq('a', 1)))).toEqual({
      $nor: [{ 'metadata.a': { $eq: 1 } }],
    });
  });

  it('translates nested (A AND B) OR (C AND D)', () => {
    const p = or(
      and(eq('tag', 'demo'), eq('owner', 'kris')),
      and(eq('tag', 'prod'), isIn('region', ['us', 'eu'])),
    );
    expect(metadataFilterToMongo(p)).toEqual({
      $or: [
        {
          $and: [
            { 'metadata.tag': { $eq: 'demo' } },
            { 'metadata.owner': { $eq: 'kris' } },
          ],
        },
        {
          $and: [
            { 'metadata.tag': { $eq: 'prod' } },
            { 'metadata.region': { $in: ['us', 'eu'] } },
          ],
        },
      ],
    });
  });

  it('translates each leaf operator', () => {
    expect(metadataFilterToMongo(eq('f', 1)))     .toEqual({ 'metadata.f': { $eq: 1 } });
    expect(metadataFilterToMongo(ne('f', 1)))     .toEqual({ 'metadata.f': { $ne: 1 } });
    expect(metadataFilterToMongo(isIn('f', [1]))) .toEqual({ 'metadata.f': { $in: [1] } });
    expect(metadataFilterToMongo(notIn('f', [1]))).toEqual({ 'metadata.f': { $nin: [1] } });
    expect(metadataFilterToMongo(exists('f')))    .toEqual({ 'metadata.f': { $exists: true } });
    expect(metadataFilterToMongo(exists('f', false))).toEqual({ 'metadata.f': { $exists: false } });
    expect(metadataFilterToMongo(gt('f', 1)))     .toEqual({ 'metadata.f': { $gt: 1 } });
    expect(metadataFilterToMongo(gte('f', 1)))    .toEqual({ 'metadata.f': { $gte: 1 } });
    expect(metadataFilterToMongo(lt('f', 1)))     .toEqual({ 'metadata.f': { $lt: 1 } });
    expect(metadataFilterToMongo(lte('f', 1)))    .toEqual({ 'metadata.f': { $lte: 1 } });
  });

  it('prefixes dotted paths with metadata.', () => {
    expect(metadataFilterToMongo(eq('foo.bar.baz', 1))).toEqual({
      'metadata.foo.bar.baz': { $eq: 1 },
    });
  });
});

describe('parseMetadataFilterQuery (predicate JSON round-trip)', () => {
  it('parses a JSON-encoded predicate tree', () => {
    const json = '{"OR":[{"a":{"eq":1}},{"b":{"eq":2}}]}';
    const r = parseMetadataFilterQuery(json);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value).toEqual({ OR: [{ a: { eq: 1 } }, { b: { eq: 2 } }] });
    }
  });

  it('rejects predicate JSON with invalid field path', () => {
    const json = '{"$where":{"eq":"x"}}';
    const r = parseMetadataFilterQuery(json);
    expect(r.ok).toBe(false);
  });
});
