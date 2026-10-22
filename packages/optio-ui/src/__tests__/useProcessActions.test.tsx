import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useProcessActions } from '../hooks/useProcessActions.js';

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
