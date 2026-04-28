import { describe, it, expect } from 'vitest';
import {
  parseMetadataFilterQuery,
  metadataFilterToMongo,
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';

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
      'metadata.targetId': 'abc',
    });
  });

  it('prefixes multiple keys with metadata.', () => {
    expect(metadataFilterToMongo({ a: 1, b: 'x' })).toEqual({
      'metadata.a': 1,
      'metadata.b': 'x',
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
