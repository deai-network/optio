import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { usePrefixDiscovery } from '../hooks/usePrefixDiscovery.js';

// Mock the context hook
vi.mock('../context/useOptioContext.js', () => ({
  useOptioClient: () => ({
    discovery: {
      prefixes: {
        useQuery: (_key: unknown, _args: unknown) => mockQueryResult,
      },
    },
  }),
}));

let mockQueryResult: { data: any; isLoading: boolean; error: unknown };

describe('usePrefixDiscovery', () => {
  it('returns null prefix when loading', () => {
    mockQueryResult = { data: undefined, isLoading: true, error: null };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('returns the prefix when exactly one is found', () => {
    mockQueryResult = {
      data: { body: { prefixes: ['myapp'] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe('myapp');
    expect(result.current.prefixes).toEqual(['myapp']);
  });

  it('returns null prefix when multiple are found', () => {
    mockQueryResult = {
      data: { body: { prefixes: ['optio', 'myapp'] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual(['optio', 'myapp']);
  });

  it('returns null prefix when none are found', () => {
    mockQueryResult = {
      data: { body: { prefixes: [] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual([]);
  });
});
