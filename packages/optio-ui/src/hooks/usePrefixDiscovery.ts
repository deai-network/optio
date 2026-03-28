import { useOptioClient } from '../context/useOptioContext.js';

interface UsePrefixesResult {
  prefixes: string[];
  isLoading: boolean;
  error: unknown;
}

export function usePrefixes(): UsePrefixesResult {
  const client = useOptioClient();
  const { data, isLoading, error } = client.discovery.prefixes.useQuery(
    ['optio-prefixes'],
    {},
  );
  return {
    prefixes: data?.body?.prefixes ?? [],
    isLoading,
    error,
  };
}

interface UsePrefixDiscoveryResult {
  prefix: string | null;
  prefixes: string[];
  isLoading: boolean;
}

export function usePrefixDiscovery(): UsePrefixDiscoveryResult {
  const { prefixes, isLoading } = usePrefixes();
  const prefix = prefixes.length === 1 ? prefixes[0] : null;
  return { prefix, prefixes, isLoading };
}
