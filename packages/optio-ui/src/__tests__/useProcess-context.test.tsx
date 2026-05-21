import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
  /** Return currently-open (non-closed) instances. */
  static live(): MockEventSource[] {
    return this.instances.filter((es) => !es.closed);
  }
}

beforeEach(() => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
  vi.resetModules();
  (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useProcess context-awareness', () => {
  it('returns provider slice rootProcess when pid is registered via hook (flat)', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');
    const { useProcess } = await import('../hooks/useProcessQueries.js');

    // No flatIds prop — useProcess self-registers via ctx.registerFlat
    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            <MultiProcessStreamProvider>
              {children}
            </MultiProcessStreamProvider>
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useProcess('pA'), { wrapper });

    // Flush debounce
    act(() => { vi.runAllTimers(); });

    // One live EventSource with flatIds=pA
    const liveES = MockEventSource.live();
    expect(liveES).toHaveLength(1);
    expect(liveES[0].url).toContain('flatIds=pA');

    // Emit an update event with a pA row through the live EventSource
    act(() => {
      liveES[0].emit({
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

    // Hook should return rootProcess from the slice
    expect(result.current.process).not.toBeNull();
    expect(result.current.process!.status.state).toBe('running');
    expect(result.current.isLoading).toBe(false);
  });
});
