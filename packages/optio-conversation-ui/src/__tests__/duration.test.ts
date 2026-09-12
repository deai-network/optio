import { describe, it, expect } from 'vitest';
import { formatDuration } from '../duration.js';

describe('formatDuration', () => {
  it.each([
    [0, '0s'],
    [-5, '0s'],
    [42_000, '42s'],
    [59_999, '59s'],
    [60_000, '1m 00s'],
    [184_000, '3m 04s'],
    [3_599_000, '59m 59s'],
    [3_600_000, '1h 00m'],
    [4_020_000, '1h 07m'],
  ])('%i ms -> %s', (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});
