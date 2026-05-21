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
  (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: true, status: 200 });
});

describe('useProcess context-awareness', () => {
  it('returns provider slice rootProcess when pid is in flatIds', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');
    const { useProcess } = await import('../hooks/useProcessQueries.js');

    function wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            <MultiProcessStreamProvider treeIds={[]} flatIds={['pA']}>
              {children}
            </MultiProcessStreamProvider>
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useProcess('pA'), { wrapper });

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

    // Hook should return rootProcess from the slice
    expect(result.current.process).not.toBeNull();
    expect(result.current.process!.status.state).toBe('running');
    expect(result.current.isLoading).toBe(false);
  });
});
