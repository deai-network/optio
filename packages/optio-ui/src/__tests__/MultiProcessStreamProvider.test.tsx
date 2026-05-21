import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import React, { useContext, useEffect } from 'react';
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
  /** Return the currently-open (non-closed) instances. */
  static live(): MockEventSource[] {
    return this.instances.filter((es) => !es.closed);
  }
}

beforeEach(() => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
  vi.resetModules();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('MultiProcessStreamProvider (dynamic registration)', () => {
  it('does NOT open an EventSource when no consumer has registered', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } = await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    // Flush any timers
    act(() => { vi.runAllTimers(); });

    // No live EventSources when no pid registered
    expect(MockEventSource.live()).toHaveLength(0);
  });

  it('opens an EventSource once a hook registers a tree pid', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    // Flush debounce timer
    act(() => { vi.runAllTimers(); });

    // Exactly one live EventSource (React 19 double-invokes effects; first is closed, last is live)
    expect(MockEventSource.live()).toHaveLength(1);
    expect(MockEventSource.live()[0].url).toContain('/api/processes/tree/multi/stream');
    expect(MockEventSource.live()[0].url).toContain('treeIds=pA');
  });

  it('closes the EventSource when all consumers unmount', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    const { rerender } = render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    act(() => { vi.runAllTimers(); });
    expect(MockEventSource.live()).toHaveLength(1);

    // Unmount the consumer
    rerender(
      <Wrapper>
        <MultiProcessStreamProvider>
          {/* no consumer */}
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => { vi.runAllTimers(); });

    // All EventSources should now be closed
    expect(MockEventSource.live()).toHaveLength(0);
  });

  it('URL contains union of tree and flat pids from multiple consumers', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function FlatConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerFlat(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
          <FlatConsumer pid="pB" />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    act(() => { vi.runAllTimers(); });

    // One live EventSource with both pids in the URL
    expect(MockEventSource.live()).toHaveLength(1);
    const url = MockEventSource.live()[0].url;
    expect(url).toContain('treeIds=pA');
    expect(url).toContain('flatIds=pB');
  });

  it('batched registers in one render batch cause at most two live EventSources (debounce reduces reconnects)', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    // Mount both consumers at once
    act(() => {
      render(
        <Wrapper>
          <MultiProcessStreamProvider>
            <TreeConsumer pid="pA" />
            <TreeConsumer pid="pB" />
          </MultiProcessStreamProvider>
        </Wrapper>,
      );
    });

    act(() => { vi.runAllTimers(); });

    // At most 2 live EventSources; critical: the final live URL has both pids
    const liveES = MockEventSource.live();
    expect(liveES.length).toBeGreaterThanOrEqual(1);
    expect(liveES.length).toBeLessThanOrEqual(2);
    const lastLive = liveES[liveES.length - 1];
    expect(lastLive.url).toContain('pA');
    expect(lastLive.url).toContain('pB');
  });

  it('log-clear for one root does not affect another root logs', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    let ctxRef: any = null;
    function Probe() { ctxRef = useContext(MultiProcessStreamContext); return null; }
    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
          <TreeConsumer pid="pB" />
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => { vi.runAllTimers(); });

    const es = MockEventSource.live()[MockEventSource.live().length - 1];
    act(() => {
      es.emit({
        type: 'update',
        processes: [
          { _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
          { _id: 'oid-B', processId: 'pB', parentId: null, rootId: 'oid-B', name: 'B', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
        ],
      });
      es.emit({
        type: 'log',
        entries: [
          { processId: 'oid-A', processLabel: 'A', rootId: 'oid-A', timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'hello A' },
          { processId: 'oid-B', processLabel: 'B', rootId: 'oid-B', timestamp: '2026-05-21T00:00:01Z', level: 'info', message: 'hello B' },
        ],
      });
    });
    expect(ctxRef.getSlice('pA').logs).toHaveLength(1);
    expect(ctxRef.getSlice('pB').logs).toHaveLength(1);

    act(() => {
      es.emit({ type: 'log-clear', rootId: 'oid-A' });
    });
    expect(ctxRef.getSlice('pA').logs).toHaveLength(0);
    expect(ctxRef.getSlice('pB').logs).toHaveLength(1);
  });

  it('exposes per-pid slices populated by update events (via registered consumer)', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    let ctxRef: any = null;
    function Probe() { ctxRef = useContext(MultiProcessStreamContext); return null; }
    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => { vi.runAllTimers(); });

    const es = MockEventSource.live()[MockEventSource.live().length - 1];
    act(() => {
      es.emit({
        type: 'update',
        processes: [
          { _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
          { _id: 'oid-AC', processId: 'pAC', parentId: 'oid-A', rootId: 'oid-A', name: 'A-child', status: { state: 'done' }, progress: {}, cancellable: false, depth: 1, order: 0, metadata: {} },
        ],
      });
    });

    const sliceA = ctxRef.getSlice('pA');
    expect(sliceA).not.toBeNull();
    expect(sliceA.rootProcess.processId).toBe('pA');
    expect(sliceA.processes).toHaveLength(2);
    expect(sliceA.tree?.processId).toBe('pA');
  });

  it('returns null slice for a pid with no registered consumer', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function TreeConsumer({ pid }: { pid: string }) {
      const ctx = useContext(MultiProcessStreamContext);
      useEffect(() => {
        if (!ctx) return;
        return ctx.registerTree(pid);
      }, [ctx, pid]);
      return null;
    }

    function Wrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      return (
        <QueryClientProvider client={client}>
          <OptioProvider prefix="test" database="test-db" baseUrl="http://localhost:0">
            {children}
          </OptioProvider>
        </QueryClientProvider>
      );
    }

    let ctxRef: any = null;
    function Probe() { ctxRef = useContext(MultiProcessStreamContext); return null; }
    render(
      <Wrapper>
        <MultiProcessStreamProvider>
          <TreeConsumer pid="pA" />
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => { vi.runAllTimers(); });

    expect(ctxRef.getSlice('pUnknown')).toBeNull();
  });
});
