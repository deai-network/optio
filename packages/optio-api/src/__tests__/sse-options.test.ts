import { describe, it, expect } from 'vitest';
import {
  parseSseOptions,
  checkLegacyMetadataParams,
  LegacyMetadataParamError,
} from '../sse-options.js';

describe('parseSseOptions', () => {
  it('parses metadataFilter from JSON string', () => {
    const result = parseSseOptions({ metadataFilter: '{"targetId":"abc"}' });
    expect(result.metadataFilter).toEqual({ targetId: 'abc' });
  });

  it('returns undefined metadataFilter when absent', () => {
    const result = parseSseOptions({});
    expect(result.metadataFilter).toBeUndefined();
  });

  it('returns undefined metadataFilter when empty string', () => {
    const result = parseSseOptions({ metadataFilter: '' });
    expect(result.metadataFilter).toBeUndefined();
  });

  it('throws on invalid metadataFilter JSON', () => {
    expect(() => parseSseOptions({ metadataFilter: 'not json' })).toThrow();
  });

  it('coerces maxDepth from string to number', () => {
    const result = parseSseOptions({ maxDepth: '3' });
    expect(result.maxDepth).toBe(3);
  });

  it('returns undefined maxDepth when absent', () => {
    const result = parseSseOptions({});
    expect(result.maxDepth).toBeUndefined();
  });

  it('throws on negative maxDepth string', () => {
    expect(() => parseSseOptions({ maxDepth: '-1' })).toThrow(/maxDepth/i);
  });

  it('throws on non-finite maxDepth string', () => {
    expect(() => parseSseOptions({ maxDepth: 'abc' })).toThrow(/maxDepth/i);
  });

  it('preserves database and prefix passthrough', () => {
    const result = parseSseOptions({ database: 'mydb', prefix: 'optio' });
    expect(result.database).toBe('mydb');
    expect(result.prefix).toBe('optio');
  });

  it('ignores non-string database and prefix', () => {
    const result = parseSseOptions({ database: 123 as unknown, prefix: null });
    expect(result.database).toBeUndefined();
    expect(result.prefix).toBeUndefined();
  });
});

describe('checkLegacyMetadataParams', () => {
  it('throws LegacyMetadataParamError on legacy metadata.* keys', () => {
    expect(() => checkLegacyMetadataParams({ 'metadata.targetId': 'abc' }))
      .toThrow(LegacyMetadataParamError);
  });

  it('LegacyMetadataParamError exposes keys and a helpful message', () => {
    try {
      checkLegacyMetadataParams({ 'metadata.foo': '1', 'metadata.bar': '2' });
      expect.fail('expected throw');
    } catch (e) {
      expect(e).toBeInstanceOf(LegacyMetadataParamError);
      const err = e as LegacyMetadataParamError;
      expect(err.keys).toEqual(['metadata.bar', 'metadata.foo']);
      expect(err.message).toContain('metadata.bar');
      expect(err.message).toContain('metadata.foo');
    }
  });

  it('does not throw when only valid keys are present', () => {
    expect(() => checkLegacyMetadataParams({
      database: 'mydb',
      metadataFilter: '{}',
    })).not.toThrow();
  });

  it('does not throw when query is empty', () => {
    expect(() => checkLegacyMetadataParams({})).not.toThrow();
  });
});
