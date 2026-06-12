import { describe, expect, it } from 'vitest';
import { toolSummary } from '../toolSummary.js';
import * as pkg from '../index.js';

describe('toolSummary', () => {
  it('picks the salient string from a tool input', () => {
    expect(toolSummary({ query: 'foo bar' })).toBe('foo bar');
    expect(toolSummary({ command: 'ls -la' })).toBe('ls -la');
    expect(toolSummary({ file_path: '/x/y.ts' })).toBe('/x/y.ts');
  });

  it('prefers description over other keys', () => {
    expect(toolSummary({ description: 'Search the web', query: 'foo' })).toBe('Search the web');
  });

  it('truncates long values and returns empty when nothing salient', () => {
    expect(toolSummary({ query: 'x'.repeat(200) })).toHaveLength(118); // 117 + ellipsis
    expect(toolSummary({})).toBe('');
    expect(toolSummary(null)).toBe('');
    expect(toolSummary('nope')).toBe('');
  });

  it('is re-exported from the package index', () => {
    expect(pkg.toolSummary).toBe(toolSummary);
  });
});
