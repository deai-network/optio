import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';

vi.mock('../context/useOptioContext.js', () => ({
  useOptioClient: () => ({
    discovery: {
      instances: {
        useQuery: (_key: unknown, _args: unknown) => mockQueryResult,
      },
    },
  }),
}));

let mockQueryResult: { data: any; isLoading: boolean; error: unknown };

describe('useInstanceDiscovery', () => {
  it('returns null instance when loading', () => {
    mockQueryResult = { data: undefined, isLoading: true, error: null };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('returns the instance when exactly one is found', () => {
    mockQueryResult = {
      data: { body: { instances: [{ database: 'mydb', prefix: 'myapp', live: true }] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toEqual({ database: 'mydb', prefix: 'myapp', live: true });
    expect(result.current.instances).toHaveLength(1);
  });

  it('returns null instance when multiple are found', () => {
    mockQueryResult = {
      data: { body: { instances: [
        { database: 'db1', prefix: 'optio', live: true },
        { database: 'db2', prefix: 'myapp', live: false },
      ] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toHaveLength(2);
  });

  it('returns null instance when none are found', () => {
    mockQueryResult = {
      data: { body: { instances: [] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toBe(null);
    expect(result.current.instances).toEqual([]);
  });
});
