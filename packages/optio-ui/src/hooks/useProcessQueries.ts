import { useContext, useEffect } from 'react';
import type { Process, ProcessMetadataFilter } from 'optio-contracts';
import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';
import { MultiProcessStreamContext } from '../context/MultiProcessStreamContext.js';

export function useProcessList(options?: {
  refetchInterval?: number | false;
  metadataFilter?: ProcessMetadataFilter;
}) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();

  const filterKey = options?.metadataFilter
    ? JSON.stringify(options.metadataFilter)
    : '';

  const { data, isLoading } = api.processes.list.useQuery({
    queryKey: ['processes', database, prefix, filterKey],
    queryData: {
      query: {
        database, prefix, limit: 50,
        ...(filterKey ? { metadataFilter: filterKey } : {}),
      },
    },
    refetchInterval: options?.refetchInterval ?? 5000,
  });
  return {
    processes: data?.status === 200 ? data.body.items : [],
    totalCount: data?.status === 200 ? data.body.totalCount : 0,
    isLoading,
  };
}

export function useProcess(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const ctx = useContext(MultiProcessStreamContext);

  // Self-register with the provider (flat kind) when a provider is mounted.
  // Flat registration means the provider streams only the root-level process
  // (no deep tree), which is sufficient for useProcess callers.
  useEffect(() => {
    if (!ctx || !id) return;
    return ctx.registerFlat(id);
  }, [ctx, id]);

  const slice = ctx && id ? ctx.getSlice(id) : null;

  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.get.useQuery({
    queryKey: ['process', database, prefix, id],
    queryData: { params: { id: id! }, query: { database, prefix } },
    enabled: !!id && !slice,  // skip polling when provider covers this pid
    refetchInterval: options?.refetchInterval ?? 5000,
  });

  if (slice) {
    // Structural cast: MultiProcessUpdate is shape-compatible with Process for the
    // fields callers rely on (status, name, etc.). Same pattern as Task 6.
    return { process: slice.rootProcess as unknown as Process | null, isLoading: false };
  }
  return {
    process: data?.status === 200 ? data.body : null,
    isLoading,
  };
}

export function useProcessTree(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTree.useQuery({
    queryKey: ['process-tree', database, prefix, id],
    queryData: { params: { id: id! }, query: { database, prefix } },
    enabled: !!id,
    refetchInterval: options?.refetchInterval ?? 5000,
  });
  return data?.status === 200 ? data.body : null;
}

export function useProcesses(
  pids: (string | undefined)[],
  options?: { refetchInterval?: number | false },
): Array<{ process: Process | null; isLoading: boolean }> {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const results = api.processes.get.useQueries({
    queries: pids.map((pid) => ({
      queryKey: ['process', database, prefix, pid],
      queryData: { params: { id: pid! }, query: { database, prefix } },
      enabled: !!pid,
      refetchInterval: options?.refetchInterval ?? 5000,
    })),
  }) as Array<{ data?: { status: number; body: Process }; isLoading: boolean }>;
  return results.map((q) => ({
    process: q.data?.status === 200 ? q.data.body : null,
    isLoading: q.isLoading,
  }));
}

export function useProcessTreeLog(id: string | undefined, options?: { refetchInterval?: number | false; limit?: number }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTreeLog.useQuery({
    queryKey: ['process-tree-log', database, prefix, id],
    queryData: { params: { id: id! }, query: { database, prefix, limit: options?.limit ?? 100 } },
    enabled: !!id,
    refetchInterval: options?.refetchInterval ?? 5000,
  });
  return data?.status === 200 ? data.body.items : [];
}
