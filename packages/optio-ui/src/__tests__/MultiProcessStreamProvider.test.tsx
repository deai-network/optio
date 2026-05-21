import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import React, { useContext } from 'react';

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
});

describe('MultiProcessStreamProvider', () => {
  it('opens exactly one EventSource for a non-empty pid set', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: React.ReactNode }) {
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
        <MultiProcessStreamProvider treeIds={['a', 'b']} flatIds={['c']}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('treeIds=a%2Cb');
    expect(MockEventSource.instances[0].url).toContain('flatIds=c');
  });

  it('reconnects (closes old EventSource, opens new) on pids prop change', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider } =
      await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: React.ReactNode }) {
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
        <MultiProcessStreamProvider treeIds={['a']} flatIds={[]}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(MockEventSource.instances).toHaveLength(1);
    const firstES = MockEventSource.instances[0];

    rerender(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['a', 'b']} flatIds={[]}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(firstES.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
  });

  it('exposes per-pid slices populated by update events', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: React.ReactNode }) {
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
    function Probe() {
      ctxRef = useContext(MultiProcessStreamContext);
      return null;
    }
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    act(() => {
      MockEventSource.instances[0].emit({
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

  it('returns null slice for a pid the provider does not watch', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: React.ReactNode }) {
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
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(ctxRef.getSlice('pUnknown')).toBeNull();
  });

  it('log-clear for one root does not affect another root logs', async () => {
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { OptioProvider } = await import('../context/OptioProvider.js');
    const { MultiProcessStreamProvider, MultiProcessStreamContext } =
      await import('../context/MultiProcessStreamContext.js');

    function Wrapper({ children }: { children: React.ReactNode }) {
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
        <MultiProcessStreamProvider treeIds={['pA', 'pB']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [
          { _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
          { _id: 'oid-B', processId: 'pB', parentId: null, rootId: 'oid-B', name: 'B', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
        ],
      });
      MockEventSource.instances[0].emit({
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
      MockEventSource.instances[0].emit({ type: 'log-clear', rootId: 'oid-A' });
    });
    expect(ctxRef.getSlice('pA').logs).toHaveLength(0);
    expect(ctxRef.getSlice('pB').logs).toHaveLength(1);
  });
});
