import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useProcessActions } from '../hooks/useProcessActions.js';
import { useProcessSnapshots } from '../hooks/useProcessSnapshots.js';

const TS = '2026-11-19T13:05:12.345Z';

vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: true },
    instances: [{ database: 'test-db', prefix: 'test', live: true }],
    isLoading: false,
  }),
}));

function wrap({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <OptioProvider prefix="test" database="test-db">{children}</OptioProvider>
    </QueryClientProvider>
  );
}

let bodies: any[];
let answer: { status: number; body: unknown };

beforeEach(() => {
  vi.restoreAllMocks();
  bodies = [];
  answer = { status: 200, body: { _id: 'c1', name: 'Child' } };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    bodies.push(init?.body ? JSON.parse(String(init.body)) : undefined);
    return new Response(JSON.stringify(answer.body), {
      status: answer.status, headers: { 'Content-Type': 'application/json' },
    });
  });
});

describe('useProcessActions().launch', () => {
  it('sends the session, and resume only when asked', async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    result.current.launch('p1');
    result.current.launch('p1', { resume: true });
    await waitFor(() => expect(bodies).toHaveLength(2), { timeout: 60_000 });
    expect(bodies[0]).toEqual({ sessionId: expect.any(String) });
    expect(bodies[1]).toEqual({ sessionId: expect.any(String), resume: true });
  });

  it('launch sends resumeFrom beside resume', async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    result.current.launch('p1', { resume: true, resumeFrom: TS });
    await waitFor(() => expect(bodies).toHaveLength(1), { timeout: 60_000 });
    expect(bodies[0]).toEqual({ sessionId: expect.any(String), resume: true, resumeFrom: TS });
  });

  it('resumeFrom without resume is sent as given (the server refuses it: invalid-request)', async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    result.current.launch('p1', { resumeFrom: TS });
    await waitFor(() => expect(bodies).toHaveLength(1), { timeout: 60_000 });
    expect(bodies[0]).toEqual({ sessionId: expect.any(String), resumeFrom: TS });
  });

  it("sends a task factory's values as params", async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    result.current.launch('f1', { params: { limit: 3 } });
    await waitFor(() => expect(bodies).toHaveLength(1), { timeout: 60_000 });
    expect(bodies[0]).toEqual({ sessionId: expect.any(String), params: { limit: 3 } });
  });

  it("sends a launch's ephemeral, true or false, and nothing when not given", async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    result.current.launch('f1', { params: {}, ephemeral: false });
    result.current.launch('f1', { params: {}, ephemeral: true });
    result.current.launch('f1', { params: {} });
    await waitFor(() => expect(bodies).toHaveLength(3), { timeout: 60_000 });
    expect(bodies.map((b: any) => b.ephemeral)).toEqual([false, true, undefined]);
  });
});

describe('useProcessActions().dismiss', () => {
  it('accepts the null body answered when the dismissal deleted the record', async () => {
    answer = { status: 200, body: null };
    const unhandled = vi.fn();
    process.on('unhandledRejection', unhandled);
    try {
      const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
      result.current.dismiss('p1');
      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1), { timeout: 60_000 });
      const url = String((vi.mocked(globalThis.fetch).mock.calls[0] as any[])[0]);
      expect(url).toContain('/processes/p1/dismiss');
      await new Promise((r) => setTimeout(r, 50));
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      process.off('unhandledRejection', unhandled);
    }
  });
});

describe('useProcessActions().launchAsync', () => {
  it('resolves with the launched record (a factory launch: its child)', async () => {
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    await expect(result.current.launchAsync('f1', { params: { limit: 3 } }))
      .resolves.toEqual({ _id: 'c1', name: 'Child' });
    expect(bodies[0]).toEqual({ sessionId: expect.any(String), params: { limit: 3 } });
  });

  it("rejects with the refusal, as ts-rest's { status, body }", async () => {
    answer = { status: 400, body: { reason: 'parameters-required', message: 'This task needs parameter values to be launched' } };
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    await expect(result.current.launchAsync('f1')).rejects.toMatchObject({
      status: 400, body: { reason: 'parameters-required' },
    });
  });
});

describe('useProcessActions().resurrectAsync', () => {
  it('resolves with the process once the resurrect is accepted', async () => {
    answer = { status: 200, body: { _id: 'p1', name: 'P' } };
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    await expect(result.current.resurrectAsync('p1')).resolves.toEqual({ _id: 'p1', name: 'P' });
    const url = String((vi.mocked(globalThis.fetch).mock.calls[0] as any[])[0]);
    expect(url).toContain('/processes/p1/resurrect');
    expect(bodies[0]).toEqual({ sessionId: expect.any(String) });
  });

  it("rejects with the refusal, as ts-rest's { status, body }", async () => {
    answer = { status: 409, body: { reason: 'not-launchable', message: 'Another launch of this process is in progress; try again shortly' } };
    const { result } = renderHook(() => useProcessActions(), { wrapper: wrap });
    await expect(result.current.resurrectAsync('p1')).rejects.toMatchObject({
      status: 409, body: { reason: 'not-launchable' },
    });
  });
});

describe('useProcessActions: a settled launch or resurrect reads the snapshot list again', () => {
  // A snapshot can be written without a run end (a resurrect's capture whose resume is refused),
  // which the list's runEndedAt key does not see.
  type Actions = ReturnType<typeof useProcessActions>;
  it.each<[string, number, (a: Actions) => void]>([
    ['a launch', 200, (a) => a.launch('p1', { resume: true, resumeFrom: TS })],
    ['a refused launch', 409, (a) => a.launch('p1', { resume: true, resumeFrom: TS })],
    ['a resurrect', 200, (a) => a.resurrect('p1')],
    ['a refused resurrect', 409, (a) => a.resurrect('p1')],
  ])('%s', async (_label, status, run) => {
    const listReads: Record<string, number> = {};
    let answered = 0;
    vi.mocked(globalThis.fetch).mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input);
      const list = /\/processes\/([^/]+)\/snapshots(\?|$)/.exec(url);
      if (list) {
        listReads[list[1]] = (listReads[list[1]] ?? 0) + 1;
        return new Response(JSON.stringify({ snapshots: [{ capturedAt: TS, endState: 'done' }] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      answered += 1;
      const body = status === 200 ? { _id: 'p1', name: 'P' } : { reason: 'not-launchable', message: 'refused' };
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const own = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <OptioProvider prefix="test" database="test-db">{children}</OptioProvider>
      </QueryClientProvider>
    );

    const { result } = renderHook(() => ({
      actions: useProcessActions(),
      p1: useProcessSnapshots('p1', { enabled: true, runEndedAt: TS }),
      p2: useProcessSnapshots('p2', { enabled: true, runEndedAt: TS }),
    }), { wrapper: own });
    await waitFor(() => expect(result.current.p1).toBeDefined(), { timeout: 60_000 });
    await waitFor(() => expect(result.current.p2).toBeDefined(), { timeout: 60_000 });
    expect(listReads).toEqual({ p1: 1, p2: 1 });

    run(result.current.actions);
    await waitFor(() => expect(listReads.p1).toBe(2), { timeout: 60_000 });
    expect(answered).toBe(1);
    // Only the acted-on process's list.
    expect(listReads.p2).toBe(1);
  });
});
