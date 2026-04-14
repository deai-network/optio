import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';

export function useProcessList(options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.list.useQuery(
    ['processes', database, prefix],
    { query: { database, prefix, limit: 50 } },
    { queryKey: ['processes', database, prefix], refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    processes: data?.status === 200 ? data.body.items : [],
    totalCount: data?.status === 200 ? data.body.totalCount : 0,
    isLoading,
  };
}

export function useProcess(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.get.useQuery(
    ['process', database, prefix, id],
    { params: { id: id! }, query: { database, prefix } },
    { queryKey: ['process', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return {
    process: data?.status === 200 ? data.body : null,
    isLoading,
  };
}

export function useProcessTree(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTree.useQuery(
    ['process-tree', database, prefix, id],
    { params: { id: id! }, query: { database, prefix } },
    { queryKey: ['process-tree', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body : null;
}

export function useProcessTreeLog(id: string | undefined, options?: { refetchInterval?: number | false; limit?: number }) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data } = api.processes.getTreeLog.useQuery(
    ['process-tree-log', database, prefix, id],
    { params: { id: id! }, query: { database, prefix, limit: options?.limit ?? 100 } },
    { queryKey: ['process-tree-log', database, prefix, id], enabled: !!id, refetchInterval: options?.refetchInterval ?? 5000 },
  );
  return data?.status === 200 ? data.body.items : [];
}
