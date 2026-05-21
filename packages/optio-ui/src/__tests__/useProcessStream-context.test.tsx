import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import type { ReactNode } from 'react';

vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: false },
    instances: [{ database: 'test-db', prefix: 'test', live: false }],
    isLoading: false,
  }),
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 0;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: any) { this.onmessage?.({ data: JSON.stringify(data) } as any); }
  static reset() { this.instances = []; }
}

beforeEach(() => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
  vi.resetModules();
  // Stub fetch so the per-PID preflight probe doesn't error out in the fallback path
  (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 });
});

describe('useProcessStream context-awareness', () => {
  it('consumes provider slice without opening a per-PID EventSource when provider knows the pid', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');
    const { useProcessStream } = await import('../hooks/useProcessStream.js');

    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
              {children}
            </MultiProcessStreamProvider>
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useProcessStream('pA'), { wrapper });

    // Exactly ONE EventSource opened — the provider's multi-stream, not a per-PID one
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('/api/processes/tree/multi/stream');

    // Emit an update event with a pA row through the provider's EventSource
    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [
          {
            _id: 'oid-A',
            processId: 'pA',
            parentId: null,
            rootId: 'oid-A',
            name: 'A-root',
            status: { state: 'running' },
            progress: { percent: 50 },
            cancellable: true,
            depth: 0,
            order: 0,
            metadata: {},
          },
        ],
      });
    });

    // Hook should return rootProcess reflecting the pA row
    expect(result.current.rootProcess).not.toBeNull();
    expect(result.current.rootProcess!.name).toBe('A-root');

    // Still exactly ONE EventSource — no per-PID SSE was opened
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('falls back to per-PID EventSource when no provider mounted', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { useProcessStream } = await import('../hooks/useProcessStream.js');

    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    renderHook(() => useProcessStream('pX'), { wrapper });

    // The fetch probe resolves to ok — EventSource should be opened for the per-PID stream
    // We need to wait for the async probe to complete
    await act(async () => {
      await Promise.resolve();
    });

    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('/api/processes/pX/tree/stream');
  });

  it('falls back to per-PID EventSource when provider does not watch the pid', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');
    const { useProcessStream } = await import('../hooks/useProcessStream.js');

    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
              {children}
            </MultiProcessStreamProvider>
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    renderHook(() => useProcessStream('pNotWatched'), { wrapper });

    // Wait for the per-PID async probe to settle
    await act(async () => {
      await Promise.resolve();
    });

    // TWO EventSources: one for the provider's multi-stream, one for the hook's per-PID fallback
    expect(MockEventSource.instances).toHaveLength(2);

    const urls = MockEventSource.instances.map((es) => es.url);
    expect(urls.some((u) => u.includes('/api/processes/tree/multi/stream'))).toBe(true);
    expect(urls.some((u) => u.includes('/api/processes/pNotWatched/tree/stream'))).toBe(true);
  });
});
