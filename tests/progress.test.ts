import { describe, it, expect } from 'vitest';
import { computeAggregatedProgress } from '../src/progress.js';

describe('computeAggregatedProgress', () => {
  it('returns own progress when mode is self', () => {
    const process = {
      progressMode: 'self' as const,
      progress: { percent: 42, message: 'Working...' },
    };
    const result = computeAggregatedProgress(process, []);
    expect(result.percent).toBe(42);
    expect(result.message).toBe('Working...');
  });

  it('returns own progress when no children', () => {
    const process = {
      progressMode: 'children' as const,
      progress: { percent: 10, message: 'Waiting...' },
    };
    const result = computeAggregatedProgress(process, []);
    expect(result.percent).toBe(10);
    expect(result.message).toBe('Waiting...');
  });

  it('computes equal-weighted average from children', () => {
    const process = {
      progressMode: 'children' as const,
      childWeights: { method: 'equal' as const, timing: 'sequential' as const },
      progress: { percent: 0, message: 'Syncing...' },
    };
    const children = [
      { progress: { percent: 100 } },
      { progress: { percent: 50 } },
    ];
    const result = computeAggregatedProgress(process, children);
    expect(result.percent).toBe(75);
    expect(result.message).toBe('Syncing...');
  });

  it('computes weighted average from children', () => {
    const process = {
      progressMode: 'children' as const,
      childWeights: {
        method: 'weighted' as const,
        timing: 'parallel' as const,
        weights: { a: 3, b: 1 },
      },
      progress: { percent: 0 },
    };
    const children = [
      { _id: 'a', progress: { percent: 100 } },
      { _id: 'b', progress: { percent: 0 } },
    ];
    const result = computeAggregatedProgress(process, children);
    expect(result.percent).toBe(75);
  });
});
