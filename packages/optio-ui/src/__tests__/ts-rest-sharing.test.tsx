import { describe, it, expect } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQueries, useQuery } from '@tanstack/react-query';
import { shareTsRestResponse } from '../ts-rest-sharing.js';
import { OptioContext } from '../context/OptioProvider.js';
import { useInstances } from '../hooks/useInstanceDiscovery.js';
import { useProcess, useProcesses, useProcessList, useProcessTree, useProcessTreeLog } from '../hooks/useProcessQueries.js';

// A response as ts-rest gives it: a fresh object with the fetch's own Headers.
function response(status: number, body: unknown) {
  return { status, body, headers: new Headers({ 'content-type': 'application/json' }) };
}

describe('shareTsRestResponse', () => {
  it('keeps the previous response when the status and the body are the same', () => {
    const before = response(200, { items: [{ _id: 'p1', status: { state: 'running' } }], totalCount: 1 });
    const after = response(200, { items: [{ _id: 'p1', status: { state: 'running' } }], totalCount: 1 });
    expect(shareTsRestResponse(before, after)).toBe(before);
  });

  it('gives the new response when the body changed, sharing the parts that stayed the same', () => {
    const before = response(200, { items: [{ _id: 'p1', status: { state: 'running' } }, { _id: 'p2', status: { state: 'idle' } }], totalCount: 2 });
    const after = response(200, { items: [{ _id: 'p1', status: { state: 'running' } }, { _id: 'p2', status: { state: 'done' } }], totalCount: 2 });
    const shared = shareTsRestResponse(before, after) as typeof after & { body: { items: unknown[] } };
    expect(shared).not.toBe(before);
    expect(shared.status).toBe(200);
    expect(shared.headers).toBe(after.headers);
    expect(shared.body).toEqual(after.body);
    // The unchanged item is the previous one; the changed one is the new value.
    expect(shared.body.items[0]).toBe((before.body as { items: unknown[] }).items[0]);
    expect(shared.body.items[1]).not.toBe((before.body as { items: unknown[] }).items[1]);
    expect(shared.body.items[1]).toEqual({ _id: 'p2', status: { state: 'done' } });
  });

  it('gives the new response when only the status changed', () => {
    const before = response(200, { message: 'x' });
    const after = response(404, { message: 'x' });
    const shared = shareTsRestResponse(before, after) as typeof after;
    expect(shared).not.toBe(before);
    expect(shared.status).toBe(404);
    expect(shared.body).toBe(before.body);
  });

  it('takes the first response as it is', () => {
    const first = response(200, { items: [] });
    expect(shareTsRestResponse(undefined, first)).toBe(first);
  });

  it('shares other data as react-query does by default', () => {
    const before = { a: [1, 2], b: { c: 'd' } };
    expect(shareTsRestResponse(before, { a: [1, 2], b: { c: 'd' } })).toBe(before);
    const changed = shareTsRestResponse(before, { a: [1, 2], b: { c: 'e' } }) as typeof before;
    expect(changed).not.toBe(before);
    expect(changed.a).toBe(before.a);
  });
});

// optio-ui's polling and list queries: an unchanged poll keeps the cached
// response object, so whoever reads that query (the host's own useQuery on the
// key, getQueryData) gets the same data.
describe("optio-ui's ts-rest queries share unchanged polls", () => {
  // A fake optio client: each route is a real react-query query answering a
  // fresh response with the same body, passing every other option through as
  // ts-rest's v5 wrapper does (its result spread, as ts-rest spreads it).
  function fakeClient(counter: { fetches: number }) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const withFetch = ({ queryData: _queryData, ...options }: any) => ({
      ...options,
      queryFn: async () => {
        counter.fetches += 1;
        return response(200, { instances: [{ database: 'd', prefix: 'p', live: true }], items: [{ _id: 'p1' }], totalCount: 1 });
      },
    });
    const route = {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      useQuery: (options: any) => ({ ...useQuery(withFetch(options)) }),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      useQueries: (options: any) => useQueries({ ...options, queries: options.queries.map(withFetch) }),
    };
    return {
      discovery: { instances: route },
      processes: { list: route, get: route, getTree: route, getTreeLog: route },
    };
  }

  const cases: Array<{ name: string; use: () => unknown; key: unknown[] }> = [
    { name: 'useInstances (the instances poll)', use: () => useInstances(), key: ['optio-instances'] },
    { name: 'useProcessList', use: () => useProcessList(), key: ['processes', 'd', 'p', ''] },
    { name: 'useProcess', use: () => useProcess('p1'), key: ['process', 'd', 'p', 'p1'] },
    { name: 'useProcesses', use: () => useProcesses(['p1']), key: ['process', 'd', 'p', 'p1'] },
    { name: 'useProcessTree', use: () => useProcessTree('p1'), key: ['process-tree', 'd', 'p', 'p1'] },
    { name: 'useProcessTreeLog', use: () => useProcessTreeLog('p1'), key: ['process-tree-log', 'd', 'p', 'p1'] },
  ];

  it.each(cases)('$name: an unchanged poll keeps the cached response', async ({ use, key }) => {
    const counter = { fetches: 0 };
    const client = fakeClient(counter);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <OptioContext.Provider value={{ prefix: 'p', database: 'd', live: true, baseUrl: '', client, resetSession: () => {} }}>
          {children}
        </OptioContext.Provider>
      </QueryClientProvider>
    );
    renderHook(use, { wrapper });
    await waitFor(() => expect(queryClient.getQueryData(key)).toBeDefined());
    const first = queryClient.getQueryData(key);

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: key });
    });

    expect(counter.fetches).toBe(2);
    expect(queryClient.getQueryData(key)).toBe(first);
  });
});
