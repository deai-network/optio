import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
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

  it('does not fetch anything when enabled is false (neither conversation nor process doc)', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        return new Response(JSON.stringify({ events: [], capturedAt: null }), { status: 200 });
      }
      return processResponse();
    });

    renderHook(() => useProcessConversation('p1', { enabled: false }), { wrapper: wrap });
    // Nothing should fetch at all — give any (incorrect) async fetch a chance
    // to fire before asserting zero calls.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('a window refocus does not refetch the record (fixed for its stoppedAt, so never stale)', async () => {
    let conversationCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        conversationCalls += 1;
        return new Response(
          JSON.stringify({ events: [], capturedAt: null }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      throw new Error(`unexpected process-doc fetch: ${url}`);
    });

    const { result } = renderHook(
      () => useProcessConversation('p1', { stoppedAt: '2026-10-12T00:00:00Z' }),
      { wrapper: wrap },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(conversationCalls).toBe(1);
    expect(result.current.isStale).toBe(false);

    try {
      // Blur then focus: react-query refetches every stale query on focus,
      // and a refetch marks the query fetching synchronously, so the result
      // right after the focus event is a deterministic checkpoint.
      act(() => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      expect(result.current.isFetching).toBe(false);
      expect(conversationCalls).toBe(1);
    } finally {
      focusManager.setFocused(undefined);
    }
  });

  it('an explicit refetch (a Retry) still fetches', async () => {
    let conversationCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (isConversationUrl(url)) {
        conversationCalls += 1;
        return new Response(
          JSON.stringify({ events: [], capturedAt: null }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      throw new Error(`unexpected process-doc fetch: ${url}`);
    });

    const { result } = renderHook(
      () => useProcessConversation('p1', { stoppedAt: '2026-10-12T00:00:00Z' }),
      { wrapper: wrap },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await act(async () => {
      await result.current.refetch();
    });
    expect(conversationCalls).toBe(2);
  });

  describe('opts.stoppedAt', () => {
    it('when provided, skips the process-doc fetch entirely', async () => {
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (isConversationUrl(url)) {
          return new Response(
            JSON.stringify({ events: [], capturedAt: null }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        // Should never be hit — fail loudly if it is.
        throw new Error(`unexpected process-doc fetch: ${url}`);
      });

      const { result } = renderHook(
        () => useProcessConversation('p1', { stoppedAt: '2026-10-12T00:00:00Z' }),
        { wrapper: wrap },
      );
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const processDocCalls = fetchMock.mock.calls.filter((c) => !isConversationUrl(String(c[0])));
      expect(processDocCalls.length).toBe(0);
      const conversationCalls = fetchMock.mock.calls.filter((c) => isConversationUrl(String(c[0])));
      expect(conversationCalls.length).toBe(1);
    });

    it('a changed stoppedAt refetches the conversation (distinct cache key)', async () => {
      let conversationCalls = 0;
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (isConversationUrl(url)) {
          conversationCalls += 1;
          return new Response(
            JSON.stringify({ events: [[conversationCalls, {}]], capturedAt: null }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          );
        }
        throw new Error(`unexpected process-doc fetch: ${url}`);
      });

      const { result, rerender } = renderHook(
        ({ stoppedAt }: { stoppedAt: string | null }) =>
          useProcessConversation('p1', { stoppedAt }),
        { wrapper: wrap, initialProps: { stoppedAt: '2026-10-12T00:00:00Z' } },
      );
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(conversationCalls).toBe(1);

      // Resume-then-resuspend: a new stoppedAt is a new cache key, so the
      // conversation is refetched instead of showing the first suspend's
      // cached result.
      rerender({ stoppedAt: '2026-10-12T01:00:00Z' });
      await waitFor(() => expect(conversationCalls).toBe(2));
    });

    it('null is a valid stoppedAt (still skips the process-doc fetch)', async () => {
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (isConversationUrl(url)) {
          return new Response(JSON.stringify({ events: [], capturedAt: null }), { status: 200 });
        }
        throw new Error(`unexpected process-doc fetch: ${url}`);
      });

      const { result } = renderHook(
        () => useProcessConversation('p1', { stoppedAt: null }),
        { wrapper: wrap },
      );
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(fetchMock.mock.calls.every((c) => isConversationUrl(String(c[0])))).toBe(true);
    });
  });
});
