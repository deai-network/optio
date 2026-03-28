import { useOptioPrefix, useOptioClient } from '../context/useOptioContext.js';

export function useProcessList(options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.list.useQuery(
    ['processes', prefix],
    { params: { prefix }, query: { limit: 50 } },
    { queryKey: ['processes', prefix], refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    processes: data?.status === 200 ? data.body.items : [],
    totalCount: data?.status === 200 ? data.body.totalCount : 0,
    isLoading,
  };
}

export function useProcess(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.get.useQuery(
    ['process', prefix, id],
    { params: { prefix, id: id! } },
    { queryKey: ['process', prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    process: data?.status === 200 ? data.body : null,
    isLoading,
  };
}

export function useProcessTree(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const api = useOptioClient();
  const { data } = api.processes.getTree.useQuery(
    ['process-tree', prefix, id],
    { params: { prefix, id: id! }, query: {} },
    { queryKey: ['process-tree', prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body : null;
}

export function useProcessTreeLog(id: string | undefined, options?: { refetchInterval?: number | false; limit?: number }) {
  const prefix = useOptioPrefix();
  const api = useOptioClient();
  const { data } = api.processes.getTreeLog.useQuery(
    ['process-tree-log', prefix, id],
    { params: { prefix, id: id! }, query: { limit: options?.limit ?? 100 } },
    { queryKey: ['process-tree-log', prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body.items : [];
}
