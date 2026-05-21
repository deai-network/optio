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
  // Stub fetch so the per-PID preflight probe doesn't error out in the fallback path
  (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useProcessStream context-awareness', () => {
  it('consumes provider slice without opening a per-PID EventSource when hook registers and provider covers the pid', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');
    const { useProcessStream } = await import('../hooks/useProcessStream.js');

    // The hook self-registers via ctx.registerTree — no treeIds prop needed
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

    const { result } = renderHook(() => useProcessStream('pA'), { wrapper });

    // Flush the registration debounce timer
    act(() => { vi.runAllTimers(); });

    // Exactly ONE live EventSource — the provider's multi-stream, not a per-PID one
    // (React 19 double-invokes effects; closed instances are the probe run)
    const liveES = MockEventSource.live();
    expect(liveES).toHaveLength(1);
    expect(liveES[0].url).toContain('/api/processes/tree/multi/stream');
    expect(liveES[0].url).toContain('treeIds=pA');

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

    // Hook should return rootProcess reflecting the pA row
    expect(result.current.rootProcess).not.toBeNull();
    expect(result.current.rootProcess!.name).toBe('A-root');

    // Still exactly ONE live EventSource — no per-PID SSE was opened
    expect(MockEventSource.live()).toHaveLength(1);
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
    await act(async () => {
      vi.runAllTimers();
      await Promise.resolve();
    });

    // At least one live ES for the per-PID stream
    const liveES = MockEventSource.live();
    expect(liveES.length).toBeGreaterThanOrEqual(1);
    expect(liveES.some((es) => es.url.includes('/api/processes/pX/tree/stream'))).toBe(true);
  });
});
