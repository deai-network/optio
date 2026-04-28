import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
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
  static last: MockEventSource | null = null;
  url: string;
  closed = false;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    MockEventSource.last = this;
  }
  close() { this.closed = true; }
  static reset() { MockEventSource.instances = []; MockEventSource.last = null; }
}

beforeEach(async () => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource as any;
  // Reset the singleton state captured at module level by re-importing fresh.
  vi.resetModules();
});

describe('useProcessListStream metadataFilter', () => {
  it('opens SSE without metadataFilter when no filter provided', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { useProcessListStream } = await import('../hooks/useProcessListStream.js');
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
    renderHook(() => useProcessListStream(), { wrapper });
    expect(MockEventSource.last).not.toBeNull();
    expect(MockEventSource.last!.url).not.toContain('metadataFilter');
  });

  it('URL-encodes JSON metadataFilter into the SSE URL', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { useProcessListStream } = await import('../hooks/useProcessListStream.js');
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
    renderHook(
      () => useProcessListStream({ metadataFilter: { project: 'x' } }),
      { wrapper },
    );
    expect(MockEventSource.last).not.toBeNull();
    const url = new URL(MockEventSource.last!.url, 'http://localhost');
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"x"}');
  });

  it('reconnects (closes prev, opens new) when filter changes', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { useProcessListStream } = await import('../hooks/useProcessListStream.js');
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
    const { rerender } = renderHook(
      ({ f }: { f?: Record<string, string> }) => useProcessListStream({ metadataFilter: f }),
      { wrapper, initialProps: { f: { project: 'x' } } },
    );
    const first = MockEventSource.last!;
    expect(first).not.toBeNull();
    expect(first.url).toContain('%22x%22');

    rerender({ f: { project: 'y' } });

    // Previous EventSource closed; new one opened with new filter
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances.length).toBe(2);
    const second = MockEventSource.instances[1];
    const url = new URL(second.url, 'http://localhost');
    expect(url.searchParams.get('metadataFilter')).toBe('{"project":"y"}');
  });
});
