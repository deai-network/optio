import { useOptioClient } from '../context/useOptioContext.js';

export interface OptioInstance {
  database: string;
  prefix: string;
  live: boolean;
}

interface UseInstancesResult {
  instances: OptioInstance[];
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
}

export function useInstances(): UseInstancesResult {
  const client = useOptioClient();
  const { data, isLoading, error, refetch } = client.discovery.instances.useQuery(
    ['optio-instances'],
    {},
  );
  return {
    instances: data?.body?.instances ?? [],
    isLoading,
    error,
    refetch,
  };
}

interface UseInstanceDiscoveryResult {
  instance: OptioInstance | null;
  instances: OptioInstance[];
  isLoading: boolean;
}

export function useInstanceDiscovery(): UseInstanceDiscoveryResult {
  const { instances, isLoading } = useInstances();
  const instance = instances.length === 1 ? instances[0] : null;
  return { instance, instances, isLoading };
}
