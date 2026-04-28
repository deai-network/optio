import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useProcessList } from '../hooks/useProcessQueries.js';

// Avoid network calls during instance discovery — return a stable instance.
vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: false },
    instances: [{ database: 'test-db', prefix: 'test', live: false }],
    isLoading: false,
  }),
}));

function wrap({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <OptioProvider prefix="test" database="test-db">
        {children}
      </OptioProvider>
    </QueryClientProvider>
  );
}

function stubFetchOk() {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(
      JSON.stringify({ items: [], totalCount: 0, nextCursor: null }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('useProcessList metadataFilter', () => {
  it('omits metadataFilter from the query string when not provided', async () => {
    const fetchMock = stubFetchOk();
    const { result } = renderHook(() => useProcessList(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(fetchMock).toHaveBeenCalled();
    const url = String((fetchMock.mock.calls[0] as [string | URL, ...unknown[]])[0]);
    expect(url).not.toContain('metadataFilter');
  });

  it('includes URL-encoded JSON metadataFilter when provided', async () => {
    const fetchMock = stubFetchOk();
    const { result } = renderHook(
      () => useProcessList({ metadataFilter: { project: 'x' } }),
      { wrapper: wrap },
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const raw = String((fetchMock.mock.calls[0] as [string | URL, ...unknown[]])[0]);
    const url = new URL(raw, 'http://localhost');
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"x"}');
  });

  it('refetches when metadataFilter changes (separate cache entries)', async () => {
    const fetchMock = stubFetchOk();
    const { result, rerender } = renderHook(
      ({ f }: { f?: Record<string, string> }) => useProcessList({ metadataFilter: f }),
      { wrapper: wrap, initialProps: { f: { project: 'x' } } },
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const initialCallCount = fetchMock.mock.calls.length;

    rerender({ f: { project: 'y' } });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCallCount));
    const lastRaw = String((fetchMock.mock.calls.at(-1) as [string | URL, ...unknown[]])[0]);
    const lastUrl = new URL(lastRaw, 'http://localhost');
    expect(lastUrl.searchParams.get('metadataFilter')).toBe('{"project":"y"}');
  });
});
