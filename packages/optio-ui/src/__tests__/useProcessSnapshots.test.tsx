import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ProcessSnapshot } from 'optio-contracts';
import { OptioProvider } from '../context/OptioProvider.js';
import { useProcessSnapshots } from '../hooks/useProcessSnapshots.js';
import { useProcess } from '../hooks/useProcessQueries.js';

// Avoid network calls during instance discovery — return a stable instance.
vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: false },
    instances: [{ database: 'test-db', prefix: 'test', live: false }],
    isLoading: false,
  }),
}));

const TS = '2026-11-19T13:05:12.345Z';
const S1: ProcessSnapshot = { capturedAt: '2026-11-19T14:10:00.000Z', endState: 'cancelled' };
const S2: ProcessSnapshot = { capturedAt: TS, endState: 'done' };

// No retry default: the hook's own `retry: false` is what keeps a failed read to one request.
function wrapper(queryClient: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <OptioProvider prefix="test" database="test-db">{children}</OptioProvider>
    </QueryClientProvider>
  );
}

function isSnapshotsUrl(url: string): boolean {
  return /\/snapshots(\?|$)/.test(url);
}

function urlOf(input: unknown): string {
  return typeof input === 'string' ? input : String(input);
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function snapshotCalls(fetchMock: { mock: { calls: unknown[][] } }): string[] {
  return fetchMock.mock.calls.map((c) => urlOf(c[0])).filter(isSnapshotsUrl);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('useProcessSnapshots', () => {
  it('returns the list on a 200', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = urlOf(input);
      if (isSnapshotsUrl(url)) return json(200, { snapshots: [S1, S2] });
      throw new Error(`unexpected fetch: ${url}`);
    });

    const { result } = renderHook(
      () => useProcessSnapshots('p1', { enabled: true, runEndedAt: S1.capturedAt }),
      { wrapper: wrapper(new QueryClient()) },
    );
    await waitFor(() => expect(result.current).toEqual([S1, S2]), { timeout: 60_000 });

    const [url] = snapshotCalls(fetchMock);
    expect(url).toContain('/processes/p1/snapshots');
    expect(url).toContain('database=test-db');
    expect(url).toContain('prefix=test');
  });

  it('a 404 answers undefined, no error thrown', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = urlOf(input);
      if (isSnapshotsUrl(url)) {
        return json(404, { reason: 'no-snapshot-support', message: 'This task keeps no snapshot history' });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    const queryClient = new QueryClient();
    const snapshotsQuery = () => queryClient.getQueryCache().findAll({ queryKey: ['process-snapshots'] })[0];

    const { result } = renderHook(
      () => useProcessSnapshots('p1', { enabled: true, runEndedAt: null }),
      { wrapper: wrapper(queryClient) },
    );
    // The read has failed (and settled: not retried).
    await waitFor(() => expect(snapshotsQuery()?.state.status).toBe('error'), { timeout: 60_000 });
    expect(snapshotsQuery()?.state.fetchStatus).toBe('idle');
    expect(result.current).toBeUndefined();
    expect(snapshotCalls(fetchMock)).toHaveLength(1);
  });

  it('disabled, or without an id: no request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = urlOf(input);
      if (isSnapshotsUrl(url)) return json(200, { snapshots: [S1, S2] });
      return json(200, {
        _id: 'p9', processId: 'p9', name: 'P', rootId: 'p9',
        status: { state: 'idle' }, createdAt: '2026-11-19T12:00:00.000Z',
      });
    });

    // A sentinel read mounted in the same render: once its answer is in, a request the
    // snapshot hooks would have made has been made.
    const { result } = renderHook(
      () => ({
        disabled: useProcessSnapshots('p1', { enabled: false, runEndedAt: S1.capturedAt }),
        noId: useProcessSnapshots(undefined, { enabled: true, runEndedAt: null }),
        sentinel: useProcess('p9', { refetchInterval: false }),
      }),
      { wrapper: wrapper(new QueryClient()) },
    );
    await waitFor(() => expect(result.current.sentinel.process).not.toBeNull(), { timeout: 60_000 });

    expect(snapshotCalls(fetchMock)).toEqual([]);
    expect(result.current.disabled).toBeUndefined();
    expect(result.current.noId).toBeUndefined();
  });

  it('a new runEndedAt reads the list again', async () => {
    let calls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = urlOf(input);
      if (isSnapshotsUrl(url)) {
        calls += 1;
        return json(200, { snapshots: calls === 1 ? [S2] : [S1, S2] });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });

    const { result, rerender } = renderHook(
      ({ runEndedAt }: { runEndedAt: string | null }) => useProcessSnapshots('p1', { enabled: true, runEndedAt }),
      { wrapper: wrapper(new QueryClient()), initialProps: { runEndedAt: TS } },
    );
    await waitFor(() => expect(result.current).toEqual([S2]), { timeout: 60_000 });
    expect(calls).toBe(1);

    // The run was resumed and has ended again: a new end, a new list.
    rerender({ runEndedAt: S1.capturedAt });
    await waitFor(() => expect(result.current).toEqual([S1, S2]), { timeout: 60_000 });
    expect(calls).toBe(2);
  });
});
