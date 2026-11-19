import { describe, it, expect, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useOptioPrefix } from '../context/useOptioContext.js';
import { OptioProvider } from '../context/OptioProvider.js';

const poll = vi.hoisted(() => ({ fetches: 0 }));

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// A fake optio client. Its discovery query is a real react-query query whose
// answer is a fresh { status, body, headers } each time, as ts-rest's is (the
// fetch Headers keep react-query from sharing the whole response), and whose
// result is spread, as ts-rest's v5 wrapper spreads it: that reads every
// property, so the hook re-renders at a fetch's start and at its end. The
// answer takes a few ms, so start and end are two renders, as in a browser.
vi.mock('../client.js', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  return {
    createOptioClient: () => ({
      discovery: {
        instances: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          useQuery: ({ queryData: _queryData, ...options }: any) => ({
            ...useQuery({
              ...options,
              queryFn: async () => {
                poll.fetches += 1;
                await new Promise((resolve) => setTimeout(resolve, 5));
                return {
                  status: 200,
                  body: { instances: [{ database: 'auto-db', prefix: 'auto', live: true }] },
                  headers: new Headers(),
                };
              },
            }),
          }),
        },
      },
    }),
  };
});

describe('OptioProvider: a poll that changes nothing', () => {
  it('keeps its context value, so a consumer does not re-render across two poll cycles', async () => {
    let renders = 0;
    function Consumer() {
      renders += 1;
      return <div data-testid="prefix">{useOptioPrefix()}</div>;
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = render(
      <QueryClientProvider client={queryClient}>
        <OptioProvider>
          <Consumer />
        </OptioProvider>
      </QueryClientProvider>,
    );
    // The first answer discovers the instance: a real change, which the consumer renders.
    await waitFor(() => expect(getByTestId('prefix').textContent).toBe('auto'));
    const settled = renders;

    for (let cycle = 0; cycle < 2; cycle++) {
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: ['optio-instances'] });
        // react-query hands its notifications on in a timeout: let them land.
        await sleep(20);
      });
    }

    expect(poll.fetches).toBe(3);
    expect(renders).toBe(settled);
  });
});
