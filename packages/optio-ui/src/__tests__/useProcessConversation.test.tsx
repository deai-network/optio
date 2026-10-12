import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useProcessConversation } from '../hooks/useProcessConversation.js';

// Avoid network calls during instance discovery — return a stable instance.
vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: false },
    instances: [{ database: 'test-db', prefix: 'test', live: false }],
    isLoading: false,
  }),
}));

function wrap({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <OptioProvider prefix="test" database="test-db">
        {children}
      </OptioProvider>
    </QueryClientProvider>
  );
}

function isConversationUrl(url: string): boolean {
  return /\/conversation(\?|$)/.test(url);
}

function processResponse(stoppedAt: string | null = null) {
  return new Response(
    JSON.stringify({
      _id: 'p1', processId: 'p1', name: 'P', rootId: 'p1',
      status: { state: 'cancelled', ...(stoppedAt ? { stoppedAt } : {}) },
      supportsResume: true, hasSavedState: true,
      createdAt: new Date().toISOString(),
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('useProcessConversation', () => {
  it('returns the conversation record on a 200', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        return new Response(
          JSON.stringify({ events: [[1, { type: 'x' }]], capturedAt: '2026-10-12T00:00:00Z' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return processResponse();
    });

    const { result } = renderHook(() => useProcessConversation('p1'), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBe(200);
    expect((result.current.data as any)?.body).toEqual({
      events: [[1, { type: 'x' }]],
      capturedAt: '2026-10-12T00:00:00Z',
    });
  });

  it('does not retry on a 4xx error', async () => {
    let conversationCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        conversationCalls += 1;
        return new Response(
          JSON.stringify({ reason: 'not-suspended', message: 'nope' }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return processResponse();
    });

    const { result } = renderHook(() => useProcessConversation('p1'), { wrapper: wrap });
    await waitFor(() => expect(result.current.isError).toBe(true));

    // Give any (incorrect) retry a chance to fire before asserting the count.
    await new Promise((r) => setTimeout(r, 50));
    expect(conversationCalls).toBe(1);
    expect((result.current.error as any)?.status).toBe(409);
    expect((result.current.error as any)?.body).toEqual({ reason: 'not-suspended', message: 'nope' });
  });

  it('does not fetch when enabled is false', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        return new Response(JSON.stringify({ events: [], capturedAt: null }), { status: 200 });
      }
      return processResponse();
    });

    renderHook(() => useProcessConversation('p1', { enabled: false }), { wrapper: wrap });
    // Let the (unrelated) process-doc fetch settle inside act() before asserting.
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const conversationCalls = fetchMock.mock.calls.filter(
      (c) => isConversationUrl(String(c[0])),
    );
    expect(conversationCalls.length).toBe(0);
  });
});
